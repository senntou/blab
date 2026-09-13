// 比較ビュー。
//
// 比較が「数値の diff」ではなく「**構成の diff + 変わった部分のコード diff**」になる。
// 研究者の頭の中は常に「baseline と同じ、ただし loss だけ新しいやつ」という形をしている
// ので、UI がその形で答える。

import { api } from '../api.js';
import { flattenTree, renderValue, shortHash, versionLabel } from '../config-tree.js';
import { filteredTable } from '../filter.js';
import { icon } from '../icons.js';
import { metricsOverlay } from '../overlay.js';
import { diffBlock } from '../source.js';
import { el, clear, colorFor, fmtNumber, tabs } from '../util.js';

function slotName(path) {
  return path === 'run' ? 'run' : path.replace(/^run\./, '');
}

function sameValue(a, b) {
  return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
}

/** 各 run の構成木を、スロットごとに横に並べて差分を出す。 */
function configDiff(details) {
  const trees = details.map((d) => flattenTree(d.resolved));
  const slots = [...new Set(trees.flatMap((t) => Object.keys(t)))].sort();

  const host = el('div', { class: 'compare-config' });
  let differences = 0;

  for (const slot of slots) {
    const nodes = trees.map((t) => t[slot] || null);
    const versions = nodes.map((n) => (n ? n.hash : null));
    const versionDiffers = new Set(versions).size > 1;

    // 引数の差分。root は args、子は builds[0..] を並べる。
    const argSets = nodes.map((n) => {
      if (!n) return {};
      if (n.args) return n.args;
      const builds = n.builds || [];
      if (!builds.length) return {};
      if (builds.length === 1) return builds[0].args || {};
      const merged = {};
      builds.forEach((b, i) => {
        for (const [k, v] of Object.entries(b.args || {})) merged[`${k} #${i}`] = v;
      });
      return merged;
    });
    const names = [...new Set(argSets.flatMap((a) => Object.keys(a)))].sort();
    const changed = names.filter((name) => !argSets.every((a) => sameValue(a[name], argSets[0][name])));

    if (!versionDiffers && !changed.length) continue;
    differences += 1;

    const section = el('section', { class: 'panel' });
    section.append(
      el('h2', {}, [
        icon('box', { size: 15 }),
        el('span', { text: slotName(slot) }),
        versionDiffers ? el('span', { class: 'chip chip-changed', text: '版が違う' }) : null,
      ]),
    );

    const table = el('table', { class: 'compare-table' });
    const head = el('tr', {}, [el('th', { text: '' })]);
    details.forEach((d, i) => {
      head.append(el('th', {}, [
        el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
        el('span', { text: d.name || d.path.split('/').pop() }),
      ]));
    });
    table.append(el('thead', {}, [head]));

    const body = el('tbody');
    const versionRow = el('tr', { class: versionDiffers ? 'row-changed' : '' }, [el('th', { text: 'component' })]);
    nodes.forEach((n) => {
      versionRow.append(
        el('td', {}, [
          n
            ? el('a', { href: `#/component/${encodeURIComponent(n.use)}` }, [
                el('strong', { text: n.use }),
                el('span', { class: 'tree-version', text: versionLabel(n) }),
              ])
            : el('span', { class: 'muted', text: '（無し）' }),
        ]),
      );
    });
    body.append(versionRow);

    for (const name of changed) {
      const row = el('tr', { class: 'row-changed' }, [el('th', { text: name })]);
      argSets.forEach((args) => {
        row.append(
          el('td', {}, [
            name in args ? renderValue(args[name]) : el('span', { class: 'muted', text: '（無し）' }),
          ]),
        );
      });
      body.append(row);
    }
    table.append(body);
    section.append(table);
    host.append(section);
  }

  if (!differences) {
    host.append(
      el('div', { class: 'empty-state' }, [
        icon('check', { size: 28, class: 'empty-icon' }),
        el('p', { text: '構成は同一です。' }),
        el('p', { class: 'muted', text: '違うのは実行時の乱数や環境だけということになります。' }),
      ]),
    );
  }
  return host;
}

/** 版が違う component について、そのソースの diff を出す。 */
function sourceDiff(details) {
  const host = el('div', { class: 'compare-source' });
  const trees = details.map((d) => flattenTree(d.resolved));
  const slots = [...new Set(trees.flatMap((t) => Object.keys(t)))].sort();

  const targets = [];
  for (const slot of slots) {
    const nodes = trees.map((t) => t[slot]).filter(Boolean);
    const hashes = [...new Set(nodes.map((n) => n.hash))];
    if (hashes.length > 1) targets.push({ slot, id: nodes[0].use, hashes });
  }

  if (!targets.length) {
    host.append(el('p', { class: 'muted pad', text: '版が違う component はありません' }));
    return host;
  }

  for (const target of targets) {
    const section = el('section', { class: 'panel' });
    section.append(
      el('h2', {}, [
        icon('code', { size: 15 }),
        el('span', { text: `${slotName(target.slot)}: ${target.id}` }),
        el('code', { class: 'muted', text: target.hashes.map(shortHash).join(' → ') }),
      ]),
    );
    const body = el('div');
    section.append(body);
    host.append(section);

    api
      .componentDiff(target.id, target.hashes[0], target.hashes[1])
      .then((doc) => {
        clear(body);
        if (!doc.files.length) {
          body.append(el('p', { class: 'muted', text: '差分はありません' }));
          return;
        }
        for (const entry of doc.files) {
          body.append(el('h3', { class: 'diff-file', text: entry.file }), diffBlock(entry.diff));
        }
      })
      .catch((e) => {
        clear(body);
        body.append(el('p', { class: 'muted', text: `diff を出せません: ${e.message}` }));
      });
  }
  return host;
}

function summaryTable(details) {
  const keys = [...new Set(details.flatMap((d) => Object.keys(d.summary || {})))].sort();
  if (!keys.length) return el('p', { class: 'muted pad', text: 'summary がありません' });

  const head = [
    'metric',
    ...details.map((d, i) => [
      el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
      el('span', { text: d.name || d.path.split('/').pop() }),
    ]),
  ];

  const table = filteredTable({
    keys,
    head,
    className: 'compare-table',
    prefKey: 'filter.compare.summary',
    row: (key) => {
      const values = details.map((d) => (d.summary || {})[key]);
      const numbers = values.filter((v) => typeof v === 'number');
      const best = numbers.length ? Math.max(...numbers) : null;
      const row = el('tr', {}, [el('th', { text: key })]);
      values.forEach((v) => {
        row.append(
          el('td', { class: v === best && numbers.length > 1 ? 'best' : '' }, [
            v === undefined ? el('span', { class: 'muted', text: '-' }) : renderValue(v),
          ]),
        );
      });
      return row;
    },
  });
  return table;
}

export async function compareView(ctx, query = new URLSearchParams()) {
  const node = el('div', { class: 'view' });
  const paths = [...new Set((query.get('paths') || '').split(',').map((p) => p.trim()).filter(Boolean))];

  node.append(el('h1', { text: '比較' }));

  if (paths.length < 2) {
    node.append(
      el('div', { class: 'empty-state' }, [
        icon('compare', { size: 28, class: 'empty-icon' }),
        el('p', { text: '比較するには 2 つ以上を選んでください。' }),
        el('p', { class: 'muted', text: 'run 一覧のチェックボックスで選んで「比較」ボタンを押してください（run と group を混ぜてもよい）。' }),
      ]),
    );
    return { node, refresh: async () => {}, live: () => false };
  }

  const details = await Promise.all(paths.map((p) => api.detail(p)));
  const runPaths = details.filter((d) => d.kind === 'run').map((d) => d.path);

  node.append(
    el('div', { class: 'chip-row' },
      details.map((d, i) => el('span', { class: 'chip' }, [
        el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
        el('span', { text: d.name || d.path.split('/').pop() }),
      ]))),
    tabs(
      [
        { id: 'config', label: '構成の diff', icon: 'box', render: () => configDiff(details) },
        { id: 'source', label: 'コードの diff', icon: 'code', render: () => sourceDiff(details) },
        {
          id: 'metrics',
          label: 'metrics',
          icon: 'chart',
          render: () => {
            const overlay = metricsOverlay(runPaths, new Map(details.map((d) => [d.path, d.name || d.path.split('/').pop()])));
            overlay.refresh();
            return overlay.node;
          },
        },
        { id: 'summary', label: 'summary', icon: 'table', render: () => summaryTable(details) },
      ],
      { prefKey: 'compare.tab' },
    ),
  );

  return { node, refresh: async () => {}, live: () => false };
}

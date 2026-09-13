// Component 詳細 — README / ソース / ラベル履歴 / 任意 2 版の diff /
// **逆引き: この component を使った全 run**（版別に分け、metric の分布付き）。

import { api } from '../api.js';
import { aggregate } from '../stats.js';
import { shortHash } from '../config-tree.js';
import { icon } from '../icons.js';
import { codeBlock, diffBlock, markdownBlock } from '../source.js';
import { el, clear, fmtNumber, fmtRelative, tabs } from '../util.js';

function versionName(version) {
  return version.label ? `@${version.label}` : shortHash(version.hash);
}

function sourceTab(id, versions) {
  const host = el('div', { class: 'source-panel' });
  if (!versions.length) {
    host.append(el('p', { class: 'muted pad', text: '読める版がありません' }));
    return host;
  }
  let hash = versions[0].hash;
  let file = null;

  const versionPicker = el('select', {
    onchange: (e) => { hash = e.target.value; file = null; load(); },
  }, versions.map((v) => el('option', { value: v.hash, text: `${versionName(v)} (${v.source})` })));

  const filePicker = el('select', { onchange: (e) => { file = e.target.value; show(); } });
  const body = el('div', { class: 'source-body' });

  async function load() {
    clear(filePicker);
    try {
      const doc = await api.componentFiles(id, hash);
      for (const f of doc.files) filePicker.append(el('option', { value: f.name, text: f.name }));
      file = file && doc.files.some((f) => f.name === file) ? file : (doc.files[0] || {}).name;
      filePicker.value = file || '';
      await show();
    } catch (e) {
      clear(body);
      body.append(el('p', { class: 'muted', text: e.message }));
    }
  }

  async function show() {
    clear(body);
    if (!file) return;
    try {
      const doc = await api.componentSource(id, file, hash);
      body.append(file.endsWith('.md') ? markdownBlock(doc.text) : codeBlock(doc.text, { language: file.endsWith('.py') ? 'python' : 'text' }));
    } catch (e) {
      body.append(el('p', { class: 'muted', text: e.message }));
    }
  }

  host.append(el('div', { class: 'source-head' }, [versionPicker, filePicker]), body);
  load();
  return host;
}

function diffTab(id, versions) {
  const host = el('div', { class: 'diff-panel' });
  if (versions.length < 2) {
    host.append(el('p', { class: 'muted pad', text: '版が 2 つ以上ないと diff できません' }));
    return host;
  }
  let a = versions[versions.length - 1].hash;
  let b = versions[0].hash;
  const body = el('div');

  const pick = (get, set) =>
    el('select', { onchange: (e) => { set(e.target.value); run(); } },
      versions.map((v) => el('option', { value: v.hash, selected: v.hash === get(), text: `${versionName(v)} (${v.source})` })));

  async function run() {
    clear(body);
    if (a === b) {
      body.append(el('p', { class: 'muted pad', text: '同じ版です' }));
      return;
    }
    try {
      const doc = await api.componentDiff(id, a, b);
      if (!doc.files.length) {
        body.append(el('p', { class: 'muted pad', text: '差分はありません' }));
        return;
      }
      for (const entry of doc.files) {
        body.append(el('h3', { class: 'diff-file', text: entry.file }), diffBlock(entry.diff));
      }
    } catch (e) {
      body.append(el('p', { class: 'muted', text: e.message }));
    }
  }

  host.append(
    el('div', { class: 'source-head' }, [
      pick(() => a, (v) => { a = v; }),
      el('span', { class: 'muted', text: '→' }),
      pick(() => b, (v) => { b = v; }),
    ]),
    body,
  );
  run();
  return host;
}

function runsTab(detail) {
  // **逆引き。** この component を使った run を版別に分け、metric の分布を添える。
  const host = el('div', { class: 'reverse' });
  const hashes = Object.keys(detail.by_version);
  if (!hashes.length) {
    host.append(el('p', { class: 'muted pad', text: 'まだどの run にも使われていません' }));
    return host;
  }
  for (const hash of hashes.sort()) {
    const runs = detail.by_version[hash];
    const label = (detail.versions.find((v) => v.hash === hash) || {}).label;
    const section = el('section', { class: 'panel' });
    section.append(
      el('h2', {}, [
        el('code', { text: shortHash(hash), title: hash }),
        label ? el('span', { class: 'chip chip-label', text: `@${label}` }) : null,
        el('span', { class: 'muted', text: ` ${runs.length} run` }),
      ]),
    );

    const stats = aggregate(runs.filter((r) => r.status === 'finished').map((r) => r.summary || {}));
    const keys = Object.keys(stats);
    if (keys.length) {
      const tiles = el('div', { class: 'summary-tiles' });
      for (const key of keys.sort()) {
        const s = stats[key];
        tiles.append(
          el('div', { class: 'tile' }, [
            el('span', { class: 'tile-label', text: key }),
            el('span', { class: 'tile-value' }, [
              el('strong', { text: fmtNumber(s.mean) }),
              s.std === null ? null : el('span', { class: 'agg-std', text: ` ± ${fmtNumber(s.std)}` }),
              el('span', { class: 'muted', text: ` (n=${s.n})` }),
            ]),
          ]),
        );
      }
      section.append(tiles);
    }

    const list = el('ul', { class: 'run-list' });
    for (const run of runs) {
      list.append(
        el('li', {}, [
          el('a', { href: `#/run/${run.path}` }, [el('span', { text: run.name || run.path.split('/').pop() })]),
          el('span', { class: `status status-${run.status}`, text: run.status }),
          el('span', { class: 'muted', text: run.created_at ? fmtRelative(run.created_at) : '' }),
        ]),
      );
    }
    section.append(list);
    host.append(section);
  }
  return host;
}

export async function componentView(ctx, id) {
  const node = el('div', { class: 'view' });
  const detail = await api.component(id);

  node.append(
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/components' }, [icon('chevron-left', { size: 14 }), el('span', { text: 'Components' })]),
    ]),
    el('h1', {}, [icon('box', { size: 20 }), el('span', { text: id })]),
  );

  const chips = el('div', { class: 'chip-row' });
  if (detail.current) {
    chips.append(el('code', { class: 'chip', title: detail.current, text: `作業コピー ${shortHash(detail.current)}` }));
  }
  for (const tag of detail.tags) chips.append(el('span', { class: 'tag-chip', text: `#${tag}` }));
  node.append(chips);

  if (detail.labels.length) {
    const table = el('table', { class: 'args' });
    table.append(el('caption', { text: 'ラベル（貼った順。latest は末尾）' }));
    const body = el('tbody');
    for (const entry of detail.labels) {
      body.append(
        el('tr', {}, [
          el('th', { text: entry.name }),
          el('td', {}, [
            el('code', { title: entry.hash, text: shortHash(entry.hash) }),
            entry.note ? el('span', { class: 'muted', text: ` ${entry.note}` }) : null,
          ]),
          el('td', { class: 'muted', text: entry.tagged_at || '' }),
        ]),
      );
    }
    table.append(body);
    node.append(table);
  }

  node.append(
    tabs(
      [
        detail.readme
          ? { id: 'readme', label: 'README', icon: 'book', render: () => markdownBlock(detail.readme) }
          : null,
        { id: 'source', label: 'ソース', icon: 'code', render: () => sourceTab(id, detail.versions) },
        { id: 'diff', label: 'diff', icon: 'compare', render: () => diffTab(id, detail.versions) },
        {
          id: 'runs',
          label: '使った run',
          icon: 'flask',
          count: Object.values(detail.by_version).reduce((n, r) => n + r.length, 0),
          render: () => runsTab(detail),
        },
      ],
      { prefKey: 'component.tab' },
    ),
  );

  return { node, refresh: async () => {}, live: () => false };
}

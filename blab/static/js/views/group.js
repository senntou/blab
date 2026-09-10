// Group 詳細: 集計（mean ± std）/ 配下 run / fold 重ね描き をタブで切り替える。

import { api } from '../api.js';
import { artifactBrowser } from '../artifacts.js';
import { icon } from '../icons.js';
import { metricsOverlay } from '../overlay.js';
import { getPref, setPref } from '../prefs.js';
import { RunTable } from '../table.js';
import { el, clear, copyButton, fmtNumber, fmtTime, nodeActions, pathField, statusBadge, tabs } from '../util.js';
import { emptyNote } from './run.js';

export async function groupView(ctx, path) {
  const node = el('div', { class: 'view' });
  const experiment = path.split('/')[0];
  // 集計で見せるキーは experiment 単位で覚える（同じ CV を何本も回すため）。
  const aggPrefKey = `agg:${experiment}`;
  let detail = null;
  let agg = null;
  let overlay = null;
  let overlayPaths = '';
  let activeTab = null;
  let table = null;
  let pickerOpen = false;

  const head = el('header', { class: 'view-head' });
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  async function refresh() {
    [detail, agg] = await Promise.all([api.run(path), api.aggregate(path)]);
    const runs = (detail.children || []).filter((c) => c.kind === 'run');
    const key = runs.map((r) => r.path).join('|');
    if (key !== overlayPaths) {
      overlayPaths = key;
      overlay = runs.length
        ? metricsOverlay(runs.map((r) => r.path), new Map(runs.map((r) => [r.path, r.name])))
        : null;
    }
    if (overlay) await overlay.refresh();
    render(runs);
  }

  function hiddenKeys() {
    const v = getPref(aggPrefKey, []);
    return new Set(Array.isArray(v) ? v : []);
  }

  function render(runs) {
    clear(head);
    head.append(
      el('div', { class: 'title-row' }, [
        el('a', { href: '#/', class: 'crumb' }, [icon('layers', { size: 15 }), el('span', { text: 'Experiments' })]),
        el('span', { class: 'crumb-sep', text: '/' }),
        el('a', { href: `#/e/${experiment}`, class: 'crumb', text: experiment }),
        el('span', { class: 'crumb-sep', text: '/' }),
        el('h1', { text: detail.name }),
        statusBadge(detail),
        el('span', { class: 'chip', text: detail.group_kind || 'group' }),
        nodeActions(path, detail.name, {
          notify: ctx.notify,
          onRenamed: (name) => { detail.name = name; render(runs); },
          onDeleted: () => { location.hash = `#/e/${experiment}`; },
        }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'btn primary',
          onclick: () => {
            ctx.setSelection(runs.map((r) => r.path));
            location.hash = '#/compare';
          },
        }, [icon('compare'), el('span', { text: '配下 run を比較' })]),
      ]),
      el('div', { class: 'head-meta' }, [
        el('span', { class: 'fact' }, [icon('table', { size: 13 }), el('span', { class: 'fact-value', text: `${runs.length} runs` })]),
        el('span', { class: 'fact' }, [icon('clock', { size: 13 }), el('span', { class: 'fact-value', text: fmtTime(detail.created_at) })]),
        agg.n_excluded
          ? el('span', { class: 'badge badge-stale' }, [el('span', { text: `集計から除外 ${agg.n_excluded}` })])
          : null,
      ]),
      el('div', { class: 'path-row' }, [
        pathField('出力先', detail.dir, 'folder'),
        pathField('相対パス', detail.path, 'hash'),
      ]),
    );

    const view = tabs([
      {
        id: 'aggregate',
        label: '集計',
        icon: 'sigma',
        count: Object.keys((agg && agg.metrics) || {}).length + Object.keys((agg && agg.values) || {}).length,
        render: () => aggregateTab(runs),
      },
      { id: 'runs', label: 'runs', icon: 'table', count: runs.length, render: () => runsTab(runs) },
      { id: 'metrics', label: 'metrics', icon: 'chart', render: metricsTab },
      {
        id: 'artifacts',
        label: 'artifacts',
        icon: 'image',
        count: (detail.artifacts || []).length,
        render: () => artifactBrowser(path, detail.artifacts || [], {
          emptyText: 'この group のアーティファクトはありません（fold 横断の成果物を置けます）',
        }),
      },
    ], {
      prefKey: 'tab:group',
      active: activeTab,
      onChange: (id) => { activeTab = id; },
    });

    clear(body);
    body.append(view.node);
  }

  // ---------------------------------------------------------------- 集計タブ

  // group 自身に記録された値（summary.json の values）。fold 平均では作れない
  // 指標——out-of-fold や fold 対応差の検定——がここに入る。集計とは別物なので
  // 別パネルにして、mean ± std と混ざらないようにする。
  function ownValuesPanel() {
    const own = Object.entries((agg && agg.values) || {});
    if (!own.length) return null;
    return el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('sigma', { size: 15 }),
        el('h2', { text: 'この group の値' }),
        el('span', { class: 'muted', text: `${own.length} キー・fold 集計ではない値` }),
        el('span', { class: 'spacer' }),
        copyButton(() => JSON.stringify(agg.values, null, 2), { label: 'JSON', title: 'group 自身の値の JSON をコピー' }),
      ]),
      el('div', { class: 'metric-cards' }, own.map(([k, v]) =>
        el('div', { class: 'metric-card' }, [
          el('span', { class: 'metric-key', text: k, title: k }),
          el('span', { class: 'metric-value', text: typeof v === 'number' ? fmtNumber(v, 6) : String(v) }),
        ]),
      )),
    ]);
  }

  function aggregateTab(runs) {
    const own = ownValuesPanel();
    const wrap = (p) => (own ? el('div', { class: 'stack' }, [own, p]) : p);
    const all = Object.entries((agg && agg.metrics) || {});
    const hidden = hiddenKeys();
    const shown = all.filter(([k]) => !hidden.has(k));

    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('sigma', { size: 15 }),
        el('h2', { text: '集計' }),
        el('span', { class: 'muted', text: `${agg.n_runs} run から / ${shown.length} / ${all.length} キー` }),
        el('span', { class: 'spacer' }),
        copyButton(() => JSON.stringify(agg, null, 2), { label: 'JSON', title: '集計結果の JSON をコピー' }),
        el('button', {
          class: `btn${pickerOpen ? ' on' : ''}`,
          onclick: () => {
            pickerOpen = !pickerOpen;
            render(runs);
          },
        }, [icon('columns'), el('span', { text: '表示するキー' })]),
      ]),
    ]);

    if (pickerOpen) panel.append(keyPicker(all.map(([k]) => k), hidden, runs));

    if (agg.n_excluded) {
      panel.append(
        el('p', { class: 'note' }, [
          icon('alert', { size: 14 }),
          el('span', { text: `finished でない run が ${agg.n_excluded} 本あり、集計から除外されています。` }),
        ]),
      );
    }
    if (!all.length) {
      panel.append(emptyNote('集計できる summary がありません'));
      return wrap(panel);
    }
    if (!shown.length) {
      panel.append(emptyNote('表示するキーがすべて隠されています（「表示するキー」から戻せます）'));
      return wrap(panel);
    }

    // まず主要な値をカードで、その下に詳細な表を出す。
    panel.append(
      el('div', { class: 'metric-cards' }, shown.map(([k, v]) =>
        el('div', { class: 'metric-card' }, [
          el('span', { class: 'metric-key', text: k, title: k }),
          el('span', { class: 'metric-value', text: fmtNumber(v.mean, 6) }),
          el('span', { class: 'metric-sub', text: `± ${fmtNumber(v.std, 3)} (n=${v.count})` }),
        ]),
      )),
      el('div', { class: 'table-scroll' }, [
        el('table', { class: 'simple' }, [
          el('thead', {}, [
            el('tr', {}, [
              el('th', { text: 'key' }),
              el('th', { class: 'num', text: 'mean ± std' }),
              el('th', { class: 'num', text: 'min' }),
              el('th', { class: 'num', text: 'max' }),
              el('th', { class: 'num', text: 'count' }),
            ]),
          ]),
          el('tbody', {}, shown.map(([k, v]) =>
            el('tr', {}, [
              el('td', { class: 'k', text: k }),
              el('td', { class: 'num strong', text: `${fmtNumber(v.mean, 6)} ± ${fmtNumber(v.std, 3)}` }),
              el('td', { class: 'num', text: fmtNumber(v.min, 6) }),
              el('td', { class: 'num', text: fmtNumber(v.max, 6) }),
              el('td', { class: 'num', text: v.count }),
            ]),
          )),
        ]),
      ]),
    );
    return wrap(panel);
  }

  function keyPicker(keys, hidden, runs) {
    const apply = (next) => {
      setPref(aggPrefKey, [...next]);
      render(runs);
    };
    return el('div', { class: 'key-picker' }, [
      el('div', { class: 'key-picker-head' }, [
        icon('info', { size: 13 }),
        el('span', { text: `集計表に出すキーを選ぶ（${experiment} の group 全体で共有・出力ディレクトリに保存）` }),
        el('span', { class: 'spacer' }),
        el('button', { class: 'link', text: 'すべて表示', onclick: () => apply(new Set()) }),
        el('button', { class: 'link', text: 'すべて隠す', onclick: () => apply(new Set(keys)) }),
      ]),
      el('div', { class: 'key-chips' }, keys.map((k) =>
        el('label', { class: `key-chip${hidden.has(k) ? ' off' : ''}` }, [
          el('input', {
            type: 'checkbox',
            checked: !hidden.has(k),
            onchange: (e) => {
              const next = new Set(hidden);
              if (e.target.checked) next.delete(k);
              else next.add(k);
              apply(next);
            },
          }),
          el('span', { text: k }),
        ]),
      )),
    ]);
  }

  // ----------------------------------------------------------------- run タブ

  function runsTab(runs) {
    if (!table) {
      // 列の選択は experiment 単位で共有する（cv5 も cv10 も同じ見え方になる）。
      table = new RunTable({
        storageKey: `group:${experiment}`,
        onSelect: (paths) => {
          const others = ctx.selection().filter((p) => !p.startsWith(`${path}/`));
          ctx.setSelection([...others, ...paths]);
        },
      });
      table.selected = new Set(ctx.selection().filter((p) => p.startsWith(`${path}/`)));
    }
    table.setRows(runs);
    return el('div', { class: 'panel-fill' }, [table.node]);
  }

  function metricsTab() {
    if (!overlay) return emptyNote('metrics を持つ run がありません');
    return overlay.node;
  }

  await refresh();
  return {
    node,
    refresh,
    live: () => detail && (detail.children || []).some((c) => c.status === 'running'),
    destroy: () => table && table.destroy(),
  };
}

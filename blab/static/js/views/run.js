// Run 詳細。summary / metrics / artifacts をタブで切り替える。

import { api } from '../api.js';
import { artifactBrowser } from '../artifacts.js';
import { lineChart, seriesPoints } from '../chart.js';
import { componentsPanel } from '../components-panel.js';
import { icon } from '../icons.js';
import { codeBlock, fileHeading } from '../source.js';
import { recordBadges } from '../table.js';
import {
  el, clear, colorFor, copyButton, fmtDuration, fmtNumber, fmtTime, nodeActions, pathField, statusBadge, tabs,
} from '../util.js';

const X_AXES = [
  ['step', 'step'],
  ['epoch', 'epoch'],
  ['time', '経過時間'],
];

export async function runView(ctx, path) {
  const node = el('div', { class: 'view' });
  const chartOpts = { xAxis: 'step', logY: false, filter: '' };
  let detail = null;
  let metrics = null;
  let activeTab = null;
  // ライブ更新のたびにファイル選択が戻らないよう、内容が変わるまで作り直さない。
  let artifactNode = null;
  let artifactSig = '';
  let codeName = null;

  const head = el('header', { class: 'view-head' });
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  async function refresh() {
    detail = await api.run(path);
    metrics = detail.metric_keys && detail.metric_keys.length ? await api.metrics(path) : null;
    render();
  }

  function render() {
    clear(head);
    const experiment = path.split('/')[0];
    const m = detail.meta || {};
    const selected = ctx.selection().includes(path);

    head.append(
      el('div', { class: 'title-row' }, [
        el('a', { href: '#/', class: 'crumb' }, [icon('layers', { size: 15 }), el('span', { text: 'Experiments' })]),
        el('span', { class: 'crumb-sep', text: '/' }),
        el('a', { href: `#/e/${experiment}`, class: 'crumb', text: experiment }),
        el('span', { class: 'crumb-sep', text: '/' }),
        el('h1', { text: detail.name }),
        statusBadge(detail),
        ...recordBadges(detail),
        nodeActions(path, detail.name, {
          notify: ctx.notify,
          onRenamed: (name) => { detail.name = name; render(); },
          onDeleted: () => {
            location.hash = detail.parent ? `#/group/${detail.parent}` : `#/e/${experiment}`;
          },
        }),
        el('span', { class: 'spacer' }),
        detail.parent
          ? el('a', { class: 'btn', href: `#/group/${detail.parent}` }, [icon('layers'), el('span', { text: '親 group' })])
          : null,
        el('button', {
          class: `btn${selected ? ' on' : ''}`,
          onclick: () => {
            const sel = new Set(ctx.selection());
            if (sel.has(path)) sel.delete(path);
            else sel.add(path);
            ctx.setSelection([...sel]);
            render();
          },
        }, [icon(selected ? 'x' : 'compare'), el('span', { text: selected ? '比較から外す' : '比較に追加' })]),
      ]),
      el('div', { class: 'head-meta' }, [
        fact('clock', '作成', fmtTime(detail.created_at)),
        fact('play', '所要', fmtDuration(detail.duration_sec)),
        m.git ? fact('git', 'git', `${String(m.git.commit || '').slice(0, 7)}${m.git.dirty ? '+dirty' : ''} @ ${m.git.branch || '?'}`) : null,
        m.env ? fact('cpu', 'host', `${m.env.hostname || '?'} / py${m.env.python || '?'}`) : null,
        metrics ? fact('chart', 'metrics', `${metrics.n_rows} 行 / ${metrics.keys.length} キー`) : null,
        ...(detail.tags || []).map((t) => el('span', { class: 'chip' }, [icon('tag', { size: 12 }), el('span', { text: t })])),
      ]),
      el('div', { class: 'path-row' }, [
        pathField('出力先', detail.dir, 'folder'),
        pathField('相対パス', detail.path, 'hash'),
        m.cmd ? el('div', { class: 'path-field', title: m.cmd }, [
          icon('terminal', { size: 14, class: 'path-icon' }),
          el('span', { class: 'path-label', text: 'cmd' }),
          el('code', { class: 'path-value', text: m.cmd }),
          copyButton(m.cmd, { title: 'コマンドをコピー' }),
        ]) : null,
      ]),
    );

    const sig = (detail.artifacts || []).concat(detail.logs || []).map((a) => `${a.path}:${a.size}`).join('|');
    if (sig !== artifactSig) {
      artifactSig = sig;
      artifactNode = null;
    }

    const view = tabs([
      {
        id: 'summary',
        label: 'summary',
        icon: 'sigma',
        count: Object.keys(detail.summary || {}).length,
        render: summaryTab,
      },
      {
        id: 'components',
        label: '構成',
        icon: 'grid',
        count: ((detail.components_raw || {}).bindings || []).length,
        render: componentsTab,
      },
      {
        id: 'metrics',
        label: 'metrics',
        icon: 'chart',
        count: metrics ? metrics.keys.length : 0,
        render: metricsTab,
      },
      {
        id: 'artifacts',
        label: 'artifacts',
        icon: 'image',
        count: (detail.artifacts || []).length + (detail.logs || []).length,
        render: artifactsTab,
      },
    ], {
      prefKey: 'tab:run',
      active: activeTab,
      onChange: (id) => { activeTab = id; },
    });

    clear(body);
    body.append(view.node);
  }

  function fact(iconName, label, value) {
    return el('span', { class: 'fact', title: `${label}: ${value}` }, [
      icon(iconName, { size: 13 }),
      el('span', { class: 'fact-label', text: label }),
      el('span', { class: 'fact-value', text: value }),
    ]);
  }

  // ------------------------------------------------------------- 構成タブ（★ blab の要）

  function componentsTab() {
    const wrap = el('div', { class: 'pane-cols' });
    const codeHost = el('div', { class: 'source-host' });

    async function showCode(name) {
      codeName = name;
      clear(codeHost);
      codeHost.append(el('p', { class: 'muted', text: `${name} を読み込み中…` }));
      try {
        const res = await api.code(path, name);
        clear(codeHost);
        codeHost.append(fileHeading(res.name, res.hash), codeBlock(res.source));
      } catch (e) {
        clear(codeHost);
        codeHost.append(el('div', { class: 'note warn' }, [
          icon('alert', { size: 14 }),
          el('span', { text: `読めません: ${e.message}` }),
        ]));
      }
    }

    wrap.append(
      el('div', { class: 'pane-main' }, [
        componentsPanel(detail.components_raw, { onCode: showCode }),
        el('section', { class: 'panel' }, [
          el('div', { class: 'panel-head' }, [
            icon('terminal', { size: 15 }),
            el('h2', { text: 'この run に残っているコード' }),
            el('span', { class: 'muted', text: `${(detail.code || []).length} ファイル` }),
          ]),
          (detail.code || []).length
            ? el('div', { class: 'file-list' }, (detail.code || []).map((f) =>
                el('button', {
                  class: `file-item${codeName === f.name ? ' on' : ''}`,
                  onclick: (e) => {
                    for (const b of wrap.querySelectorAll('.file-item')) b.classList.remove('on');
                    e.currentTarget.classList.add('on');
                    showCode(f.name);
                  },
                }, [icon('file', { size: 13 }), el('span', { text: f.name })])))
            : el('p', { class: 'empty-note', text: 'code/ に snapshot がありません（notebook / REPL から実行された run か、snapshot に失敗した run）' }),
          codeHost,
        ]),
      ]),
      el('div', { class: 'pane-side' }, [policyPanel(), metaPanel()]),
    );
    if (codeName) showCode(codeName);
    else if ((detail.code || []).length) {
      const entry = (detail.components_raw?.entrypoints || [])[0];
      const first = entry && entry.path ? entry.path.replace(/^code\//, '') : detail.code[0].name;
      showCode(first);
    }
    return wrap;
  }

  function policyPanel() {
    const violations = detail.policy_violations || [];
    if (!violations.length) return null;
    return el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('alert', { size: 15 }),
        el('h2', { text: 'ポリシー違反' }),
      ]),
      el('ul', { class: 'tight warn-list' }, violations.map((v) => el('li', { text: v }))),
      el('p', { class: 'muted small', text: 'blab.json の require_components / require_tags に反した記録。値そのものは正しく書かれている。' }),
    ]);
  }

  // ------------------------------------------------------------- summary タブ

  function summaryTab() {
    const wrap = el('div', { class: 'pane-cols' });
    wrap.append(
      el('div', { class: 'pane-main' }, [summaryPanel(), kvPanel('params', detail.params, 'sliders', true)]),
      el('div', { class: 'pane-side' }, [metaPanel()]),
    );
    return wrap;
  }

  function summaryPanel() {
    const entries = Object.entries(detail.summary || {});
    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('sigma', { size: 15 }),
        el('h2', { text: 'summary' }),
        el('span', { class: 'muted', text: `${entries.length} キー` }),
      ]),
    ]);
    if (!entries.length) {
      panel.append(emptyNote('summary が記録されていません'));
      return panel;
    }
    // 最終評価値は一番目立たせたいので、カードで大きく出す。
    panel.append(
      el('div', { class: 'metric-cards' }, entries.map(([k, v]) =>
        el('div', { class: 'metric-card' }, [
          el('span', { class: 'metric-key', text: k, title: k }),
          el('span', { class: 'metric-value', text: fmtNumber(v, 6) }),
        ]),
      )),
    );
    return panel;
  }

  function kvPanel(title, obj, iconName, numeric) {
    const entries = Object.entries(obj || {});
    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon(iconName, { size: 15 }),
        el('h2', { text: title }),
        el('span', { class: 'muted', text: `${entries.length} キー` }),
        el('span', { class: 'spacer' }),
        entries.length
          ? copyButton(() => JSON.stringify(numeric ? detail.params_raw ?? obj : obj, null, 2), { label: 'JSON', title: 'JSON をコピー' })
          : null,
      ]),
    ]);
    if (!entries.length) {
      panel.append(emptyNote('（なし）'));
      return panel;
    }
    panel.append(
      el('table', { class: 'kv' }, [
        el('tbody', {}, entries.map(([k, v]) =>
          el('tr', {}, [
            el('td', { class: 'k', text: k }),
            el('td', { class: 'v', text: numeric ? fmtNumber(v, 6) : String(v) }),
          ]),
        )),
      ]),
    );
    return panel;
  }

  function metaPanel() {
    const m = detail.meta || {};
    const rows = [
      ['id', m.id],
      ['status', detail.stale ? `${m.status} (stale)` : m.status],
      ['created_at', fmtTime(m.created_at)],
      ['finished_at', fmtTime(m.finished_at)],
      ['heartbeat_at', m.status === 'running' ? fmtTime(m.heartbeat_at) : null],
      ['duration', fmtDuration(m.duration_sec)],
      ['tags', (m.tags || []).join(', ')],
      ['cmd', m.cmd],
      ['git', m.git ? `${m.git.commit}${m.git.dirty ? '+dirty' : ''} (${m.git.branch})` : null],
      ['env', m.env ? `${m.env.hostname} / python ${m.env.python}` : null],
      ['platform', m.env ? m.env.platform : null],
      ['notes', m.notes],
    ].filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== '—');
    return el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [icon('info', { size: 15 }), el('h2', { text: 'meta' })]),
      el('table', { class: 'kv' }, [
        el('tbody', {}, rows.map(([k, v]) =>
          el('tr', {}, [el('td', { class: 'k', text: k }), el('td', { class: 'v', text: String(v) })]),
        )),
      ]),
    ]);
  }

  // ------------------------------------------------------------- metrics タブ

  function metricsTab() {
    const section = el('section', { class: 'panel charts' });
    const grid = el('div', { class: 'chart-grid' });
    const filterInput = el('input', {
      type: 'search',
      placeholder: 'キーで絞り込み',
      value: chartOpts.filter,
      oninput: (e) => {
        chartOpts.filter = e.target.value;
        redraw();
      },
    });
    section.append(
      el('div', { class: 'panel-head' }, [
        icon('chart', { size: 15 }),
        el('h2', { text: 'metrics' }),
        el('span', { class: 'muted', text: metrics ? `${metrics.n_rows} 行` : '' }),
        el('span', { class: 'spacer' }),
        el('span', { class: 'search-box' }, [icon('search', { size: 13, class: 'search-icon' }), filterInput]),
        el('span', { class: 'segmented' }, X_AXES.map(([value, label]) =>
          el('button', {
            class: `btn${chartOpts.xAxis === value ? ' on' : ''}`,
            text: label,
            onclick: () => {
              chartOpts.xAxis = value;
              render();
            },
          }),
        )),
        el('label', { class: 'checkline' }, [
          el('input', {
            type: 'checkbox',
            checked: chartOpts.logY,
            onchange: (e) => {
              chartOpts.logY = e.target.checked;
              render();
            },
          }),
          'log y',
        ]),
      ]),
      grid,
    );

    function redraw() {
      clear(grid);
      if (!metrics) {
        grid.append(emptyNote('metrics が記録されていません'));
        return;
      }
      const keys = metrics.keys.filter((k) => k.includes(chartOpts.filter));
      if (!keys.length) {
        grid.append(emptyNote('一致するキーがありません'));
        return;
      }
      keys.forEach((key, i) => {
        const s = metrics.series[key];
        if (!s) return;
        grid.append(
          lineChart({
            title: key,
            xLabel: chartOpts.xAxis,
            logY: chartOpts.logY,
            legend: false,
            series: [{ label: key, color: colorFor(i), points: seriesPoints(s, chartOpts.xAxis) }],
          }),
        );
      });
    }
    redraw();
    return section;
  }

  // ----------------------------------------------------------- artifacts タブ

  function artifactsTab() {
    if (artifactNode) return artifactNode;
    const files = detail.artifacts || [];
    const logs = detail.logs || [];
    const wrap = el('div', { class: 'stack' });
    wrap.append(
      el('section', { class: 'panel flush' }, [
        el('div', { class: 'panel-head' }, [
          icon('image', { size: 15 }),
          el('h2', { text: 'artifacts' }),
          el('span', { class: 'muted', text: `${files.length} 件` }),
        ]),
        artifactBrowser(path, files, { emptyText: 'アーティファクトがありません' }),
      ]),
    );
    if (logs.length) {
      wrap.append(
        el('section', { class: 'panel flush' }, [
          el('div', { class: 'panel-head' }, [
            icon('terminal', { size: 15 }),
            el('h2', { text: 'logs' }),
            el('span', { class: 'muted', text: `${logs.length} 件` }),
          ]),
          artifactBrowser(path, logs, { baseDir: 'logs', emptyText: 'ログがありません' }),
        ]),
      );
    }
    artifactNode = wrap;
    return wrap;
  }

  await refresh();
  return { node, refresh, live: () => detail && detail.status === 'running' };
}

export function emptyNote(text) {
  return el('p', { class: 'empty-note' }, [icon('info', { size: 14 }), el('span', { text })]);
}

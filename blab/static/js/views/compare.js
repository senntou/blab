// 比較ビュー: metrics 重ね描き / summary + params diff / アーティファクト並置。
// それぞれ独立した画面としてタブで切り替える。
//
// run と group を混ぜて並べられる。group の行に出るのは
//   1. group 自身に記録された値（out-of-fold 指標など）があればそれ
//   2. 無ければ配下 run の平均（mean ± std を併記し「agg」バッジを付ける）
// 「学習していないベースライン run」と「10 fold の CV group」を 1 つの表で
// 比べるための作り。どちらの数字を見ているかはセルの見た目で必ず分かるようにする。

import { api } from '../api.js';
import { artifactCompare } from '../artifacts.js';
import { argText } from '../components-panel.js';
import { icon } from '../icons.js';
import { metricsOverlay } from '../overlay.js';
import { getPref, setPref } from '../prefs.js';
import { diffBlock } from '../source.js';
import { el, clear, colorFor, fmtDuration, fmtNumber, statusBadge, tabs } from '../util.js';
import { emptyNote } from './run.js';

export async function compareView(ctx) {
  const node = el('div', { class: 'view' });
  let details = [];
  let overlay = null;
  let activeTab = null;
  // summary の「大きいほど良い / 小さいほど良い」はデータからは分からないので、
  // ユーザに選んでもらい、その選択を出力ディレクトリに覚えておく。
  let better = getPref('compare:better', 'max');

  const paths = ctx.selection();
  if (!paths.length) {
    node.append(
      el('header', { class: 'view-head' }, [
        el('div', { class: 'title-row' }, [icon('compare', { size: 20, class: 'title-icon' }), el('h1', { text: '比較' })]),
      ]),
      el('div', { class: 'empty-state' }, [
        icon('compare', { size: 30, class: 'empty-icon' }),
        el('p', { text: 'Run テーブルでチェックを入れると、ここに並びます。' }),
        el('a', { class: 'btn', href: '#/' }, [icon('layers'), el('span', { text: 'Experiments へ' })]),
      ]),
    );
    return { node, refresh: async () => {}, live: () => false };
  }

  const labels = new Map();
  const head = el('header', { class: 'view-head' });
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  async function refresh() {
    details = (await Promise.all(paths.map((p) => api.run(p).catch(() => null)))).filter(Boolean);
    for (const d of details) labels.set(d.path, d.name || d.path);
    rebuildOverlay();
    await overlay.refresh();
    render();
  }

  function rebuildOverlay() {
    // metrics.jsonl を持つのは run だけ。group は重ね描きの対象外。
    overlay = metricsOverlay(details.filter((d) => d.kind === 'run').map((d) => d.path), labels);
  }

  function nodeHref(d) {
    return `#/${d.kind === 'group' ? 'group' : 'run'}/${d.path}`;
  }

  async function drop(path) {
    ctx.setSelection(ctx.selection().filter((p) => p !== path));
    details = details.filter((d) => d.path !== path);
    rebuildOverlay();
    await overlay.refresh();
    render();
  }

  function render() {
    clear(head);
    head.append(
      el('div', { class: 'title-row' }, [
        icon('compare', { size: 20, class: 'title-icon' }),
        el('h1', { text: '比較' }),
        el('span', { class: 'pill pill-accent', text: comparedLabel() }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'btn',
          onclick: () => {
            ctx.setSelection([]);
            location.hash = '#/';
          },
        }, [icon('x'), el('span', { text: '選択を解除' })]),
      ]),
      el('div', { class: 'run-chips' }, details.map((d, i) =>
        el('span', { class: 'run-chip' }, [
          el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
          el('a', { href: nodeHref(d), text: d.name, title: d.path }),
          d.kind === 'group'
            ? el('span', { class: 'chip', text: `${d.group_kind || 'group'}${d.n_children ? ` · ${d.n_children}` : ''}` })
            : null,
          statusBadge(d),
          el('span', { class: 'muted', text: fmtDuration(d.duration_sec) }),
          el('button', { class: 'icon-btn', title: '比較から外す', onclick: () => drop(d.path) }, [icon('x', { size: 13 })]),
        ]),
      )),
    );

    const nRuns = details.filter((d) => d.kind === 'run').length;
    const view = tabs([
      {
        id: 'metrics',
        label: 'metrics',
        icon: 'chart',
        render: () => (nRuns ? overlay.node : emptyNote('metrics を持つ run が選ばれていません（group には時系列がありません）')),
      },
      { id: 'summary', label: 'summary', icon: 'sigma', render: summaryTab },
      {
        id: 'components',
        label: '構成',
        icon: 'grid',
        count: componentKeys().length,
        render: componentsTab,
      },
      {
        id: 'artifacts',
        label: 'artifacts',
        icon: 'image',
        count: new Set(details.flatMap((d) => (d.artifacts || []).map((a) => a.name))).size,
        render: () => artifactCompare(details, { prefKey: 'compare:artifact' }),
      },
    ], {
      prefKey: 'tab:compare',
      active: activeTab,
      onChange: (id) => { activeTab = id; },
    });

    clear(body);
    body.append(view.node);
  }

  function comparedLabel() {
    const g = details.filter((d) => d.kind === 'group').length;
    const r = details.length - g;
    return g ? `${r} runs · ${g} groups` : `${r} runs`;
  }

  // -------------------------------------------------------------- summary タブ

  function summaryTab() {
    return el('div', { class: 'stack' }, [summaryTable(), paramsDiff()]);
  }

  function summaryTable() {
    const keys = [];
    for (const d of details) for (const k of Object.keys(d.summary || {})) if (!keys.includes(k)) keys.push(k);
    keys.sort();
    const pick = better === 'min' ? Math.min : Math.max;
    const best = new Map(
      keys.map((k) => {
        const values = details.map((d) => d.summary[k]).filter((v) => typeof v === 'number');
        return [k, values.length ? pick(...values) : null];
      }),
    );
    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('sigma', { size: 15 }),
        el('h2', { text: 'summary' }),
        el('span', { class: 'muted', text: `${keys.length} キー` }),
        el('span', { class: 'spacer' }),
        el('span', { class: 'checkline', text: 'ハイライト' }),
        el('span', { class: 'segmented' }, [['max', '最大が良い'], ['min', '最小が良い'], ['none', 'なし']].map(([v, label]) =>
          el('button', {
            class: `btn${better === v ? ' on' : ''}`,
            text: label,
            onclick: () => {
              better = v;
              setPref('compare:better', v);
              render();
            },
          }),
        )),
      ]),
    ]);
    if (!keys.length) {
      panel.append(emptyNote('summary がありません'));
      return panel;
    }
    panel.append(
      el('div', { class: 'table-scroll' }, [
        el('table', { class: 'simple' }, [
          el('thead', {}, [
            el('tr', {}, [el('th', { text: 'run / group' }), ...keys.map((k) => el('th', { class: 'num', text: k }))]),
          ]),
          el('tbody', {}, details.map((d, i) =>
            el('tr', {}, [
              el('td', {}, [
                el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
                el('a', { href: nodeHref(d), text: d.name }),
                d.kind === 'group' ? el('span', { class: 'chip', text: 'agg' }) : null,
              ]),
              ...keys.map((k) => {
                const v = d.summary[k];
                const isBest = better !== 'none' && typeof v === 'number' && v === best.get(k);
                if (v === undefined) return el('td', { class: 'num', text: '—' });
                // 配下 run の平均なら ±std と n を添える。group 自身に記録された
                // 値（OOF など）は素の数字のまま——SD が定義できない値だから。
                const st = (d.summary_stats || {})[k];
                return el('td', { class: `num${isBest ? ' best' : ''}` }, [
                  el('span', { text: fmtNumber(v, 6) }),
                  st
                    ? el('span', {
                        class: 'muted agg-sub',
                        text: ` ± ${fmtNumber(st.std, 3)} (n=${st.count})`,
                        title: `配下 run ${st.count} 本の平均。min ${fmtNumber(st.min, 6)} / max ${fmtNumber(st.max, 6)}`,
                      })
                    : null,
                ]);
              }),
            ]),
          )),
        ]),
      ]),
    );
    return panel;
  }

  // ---------------------------------------------------- 構成 diff（★ blab）
  //
  // 研究者の頭の中は「baseline と同じ、ただし loss だけ新しいやつ」という形をしている。
  // なので比較は params の数値 diff ではなく「構成の diff + 変わった部分のコード diff」で答える。

  function bindingsOf(detail) {
    return detail.bindings || {};
  }

  function componentKeys() {
    const keys = [];
    for (const d of details) {
      for (const k of Object.keys(bindingsOf(d))) {
        // component 列（role / id）だけを見る。args 列は下の表で開く。
        if (!k.includes('.') && !keys.includes(k)) keys.push(k);
      }
    }
    return keys.sort();
  }

  function componentsTab() {
    return el('div', { class: 'stack' }, [componentDiffTable(), sourceDiffs(), entrypointDiff()]);
  }

  function componentDiffTable() {
    const keys = componentKeys();
    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('grid', { size: 15 }),
        el('h2', { text: '構成の diff' }),
        el('span', { class: 'muted', text: `${keys.length} 役` }),
      ]),
    ]);
    if (!keys.length) {
      panel.append(emptyNote('選んだ node に component の記録がありません'));
      return panel;
    }
    // args も含めた行を作る。値が全て同じ行は薄く見せる。
    const argKeys = [];
    for (const d of details) {
      for (const k of Object.keys(bindingsOf(d))) {
        if (k.includes('.') && !argKeys.includes(k)) argKeys.push(k);
      }
    }
    argKeys.sort();
    const rows = [];
    for (const key of keys) {
      rows.push(key);
      for (const a of argKeys) if (a.startsWith(`${key}.`)) rows.push(a);
    }

    panel.append(el('div', { class: 'table-scroll' }, [
      el('table', { class: 'simple compare' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'role / id' }),
            ...details.map((d, i) => el('th', {}, [
              el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
              el('span', { text: d.name }),
            ])),
          ]),
        ]),
        el('tbody', {}, rows.map((key) => {
          const values = details.map((d) => bindingsOf(d)[key]);
          const same = values.every((v) => JSON.stringify(v ?? null) === JSON.stringify(values[0] ?? null));
          const isArg = key.includes('.');
          return el('tr', { class: same ? 'same' : 'diff' }, [
            el('td', { class: 'k' }, [
              isArg
                ? el('span', { class: 'muted', text: `  ${key.split('.').slice(1).join('.')}` })
                : el('a', { href: `#/component/${componentIdOf(key)}`, text: key }),
            ]),
            ...values.map((v) => el('td', {
              class: `${isArg ? 'num' : ''}${same ? '' : ' diff-cell'}`,
              text: v === undefined ? '—' : String(argText(v)),
            })),
          ]);
        })),
      ]),
    ]));
    return panel;
  }

  /** 列名（role）から component id を引く。role と id が違う場合に必要。 */
  function componentIdOf(key) {
    for (const d of details) {
      for (const b of (d.components_raw?.bindings || [])) {
        const label = b.role || b.id;
        if (label === key || `${label}#${b.index}` === key) return b.id;
      }
    }
    return key;
  }

  /** 同じ id で version が違うものは、ソースの diff をその場で開く。 */
  function sourceDiffs() {
    const versions = new Map(); // id -> Set(version)
    for (const d of details) {
      for (const b of (d.components_raw?.bindings || [])) {
        if (!b.version) continue;
        if (!versions.has(b.id)) versions.set(b.id, new Set());
        versions.get(b.id).add(b.version);
      }
    }
    const differing = [...versions.entries()].filter(([, set]) => set.size > 1);
    if (!differing.length) return null;

    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('compare', { size: 15 }),
        el('h2', { text: 'version が違う component のソース diff' }),
        el('span', { class: 'muted', text: `${differing.length} 件` }),
      ]),
    ]);
    for (const [id, set] of differing) {
      const list = [...set].sort();
      const host = el('div', { class: 'source-host' });
      panel.append(
        el('div', { class: 'panel-subhead' }, [
          icon('grid', { size: 14 }),
          el('a', { href: `#/component/${id}`, text: id }),
          el('span', { class: 'muted', text: list.map((v) => `@${v}`).join(' ↔ ') }),
        ]),
        host,
      );
      api.componentDiff(id, list[0], list[list.length - 1])
        .then((res) => { clear(host); host.append(diffBlock(res.diff)); })
        .catch((e) => { clear(host); host.append(emptyNote(`diff を作れません: ${e.message}`)); });
    }
    return panel;
  }

  function entrypointDiff() {
    const rows = details.map((d) => ({
      name: d.name,
      entrypoints: (d.components_raw?.entrypoints || []).map((e) => `${e.name || '?'} (${String(e.hash || '').slice(7, 19)})`),
      argv: (d.components_raw?.entrypoints || []).map((e) => e.argv).filter(Boolean),
    }));
    if (!rows.some((r) => r.entrypoints.length)) return null;
    const hashes = new Set(rows.flatMap((r) => r.entrypoints));
    return el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('terminal', { size: 15 }),
        el('h2', { text: 'entrypoint' }),
        el('span', {
          class: 'muted',
          text: hashes.size <= 1 ? '同じスクリプト' : `${hashes.size} 種類のスクリプト`,
        }),
      ]),
      el('table', { class: 'kv' }, [
        el('tbody', {}, rows.map((r) =>
          el('tr', {}, [
            el('td', { class: 'k', text: r.name }),
            el('td', { class: 'v' }, [
              el('div', {}, r.entrypoints.map((e) => el('code', { class: 'block', text: e }))),
              ...r.argv.map((a) => el('code', { class: 'argv block', text: a })),
            ]),
          ]))),
      ]),
    ]);
  }

  function paramsDiff() {
    const showSame = getPref('compare:showSameParams', false);
    const keys = [];
    for (const d of details) for (const k of Object.keys(d.params || {})) if (!keys.includes(k)) keys.push(k);
    keys.sort();
    const same = [];
    const diff = [];
    for (const k of keys) {
      const values = details.map((d) => JSON.stringify(d.params[k] ?? null));
      (values.every((v) => v === values[0]) ? same : diff).push(k);
    }
    const shown = showSame ? [...diff, ...same] : diff;

    const panel = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon('sliders', { size: 15 }),
        el('h2', { text: 'params diff' }),
        el('span', { class: 'muted', text: `差異 ${diff.length} / 全 ${keys.length}` }),
        el('span', { class: 'spacer' }),
        el('label', { class: 'checkline' }, [
          el('input', {
            type: 'checkbox',
            checked: showSame,
            onchange: (e) => {
              setPref('compare:showSameParams', e.target.checked);
              render();
            },
          }),
          '同一のキーも表示',
        ]),
      ]),
    ]);
    if (!shown.length) {
      panel.append(emptyNote(keys.length ? '差異のある params はありません' : 'params がありません'));
      return panel;
    }
    panel.append(
      el('div', { class: 'table-scroll' }, [
        el('table', { class: 'simple compare' }, [
          el('thead', {}, [
            el('tr', {}, [
              el('th', { text: 'param' }),
              ...details.map((d, i) => el('th', {}, [
                el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
                el('span', { text: d.name }),
              ])),
            ]),
          ]),
          el('tbody', {}, shown.map((k) => {
            const isDiff = diff.includes(k);
            return el('tr', { class: isDiff ? 'diff' : 'same' }, [
              el('td', { class: 'k', text: k }),
              ...details.map((d) => {
                const v = d.params[k];
                return el('td', {
                  class: `num${isDiff ? ' diff-cell' : ''}`,
                  text: v === undefined ? '—' : fmtNumber(v, 6),
                });
              }),
            ]);
          })),
        ]),
      ]),
    );
    return panel;
  }

  await refresh();
  return { node, refresh, live: () => details.some((d) => d.status === 'running') };
}

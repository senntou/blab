// Component 詳細 — blab の主画面。
//
// README / ソース / version 履歴と diff / **この component を使った run の逆引き**。
// 「この augmentation は結局効いたのか」に、run を辿らずここで答える。

import { api } from '../api.js';
import { argText } from '../components-panel.js';
import { icon } from '../icons.js';
import { codeBlock, diffBlock, markdownBlock } from '../source.js';
import { el, clear, fmtNumber, fmtTime, statusBadge, tabs } from '../util.js';

export async function componentView(ctx, id) {
  const node = el('div', { class: 'view' });
  const head = el('header', { class: 'view-head' });
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  let detail = null;
  let activeTab = null;
  let sourceVersion = null;
  let diffA = null;
  let diffB = null;

  async function refresh() {
    detail = await api.component(id);
    const versions = detail.versions.map((v) => v.id);
    if (!sourceVersion || !versions.includes(sourceVersion)) sourceVersion = detail.latest;
    if (!diffA || !versions.includes(diffA)) diffA = versions[Math.max(0, versions.length - 2)] || null;
    if (!diffB || !versions.includes(diffB)) diffB = detail.latest;
    render();
  }

  function render() {
    clear(head);
    head.append(
      el('div', { class: 'title-row' }, [
        el('a', { href: '#/components', class: 'crumb' }, [icon('grid', { size: 15 }), el('span', { text: 'Components' })]),
        el('span', { class: 'crumb-sep', text: '/' }),
        el('h1', { text: detail.id }),
        el('code', { class: 'ver', text: detail.latest || '未登録' }),
        el('span', { class: 'spacer' }),
      ]),
      el('div', { class: 'head-meta' }, [
        fact('terminal', 'entry', detail.entry || '不明'),
        fact('layers', 'versions', String(detail.versions.length)),
        fact('flask', '使用 run', String(detail.n_runs)),
        ...(detail.tags || []).map((t) => el('span', { class: 'chip' }, [icon('tag', { size: 12 }), el('span', { text: t })])),
      ]),
      warnings(),
    );

    const view = tabs([
      { id: 'runs', label: '使った run', icon: 'flask', count: detail.n_runs, render: runsTab },
      { id: 'readme', label: 'README', icon: 'file', render: readmeTab },
      { id: 'source', label: 'ソース', icon: 'terminal', render: sourceTab },
      { id: 'versions', label: 'version', icon: 'layers', count: detail.versions.length, render: versionsTab },
    ], {
      prefKey: 'tab:component',
      active: activeTab,
      onChange: (t) => { activeTab = t; },
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

  function warnings() {
    const out = [];
    if (detail.current_matches_latest === false) {
      out.push(note('warn', `current/${detail.id}.py が ${detail.latest} と異なります。`
        + `この component を version 未指定で load すると整合性ゲートで止まります`));
    }
    if (detail.readme_stale) {
      out.push(note('warn', 'README.ja.md が最新 version の登録より古いままです'));
    }
    if (detail.current_matches_latest === null && detail.latest) {
      out.push(note('info', 'current/ がないので編集中の内容と比較できません（clone / rsync 直後の状態）'));
    }
    return out.length ? el('div', { class: 'note-row' }, out) : null;
  }

  function note(kind, text) {
    return el('div', { class: `note ${kind}` }, [icon(kind === 'warn' ? 'alert' : 'info', { size: 14 }), el('span', { text })]);
  }

  // ------------------------------------------------------------- 逆引き

  function runsTab() {
    const wrap = el('div', { class: 'pane' });
    const versions = Object.keys(detail.by_version || {});
    if (!versions.length) {
      wrap.append(el('p', { class: 'empty-note', text: 'この component を使った run はまだありません' }));
      return wrap;
    }
    // version ごとに分ける。「v2 にしてから上がったのか」を見るための区切り。
    const order = [...detail.versions.map((v) => v.id).reverse(), 'dirty', '?'];
    for (const version of order) {
      const rows = (detail.runs || []).filter((r) => (r.version || (r.dirty ? 'dirty' : '?')) === version);
      if (!rows.length) continue;
      wrap.append(versionSection(version, rows));
    }
    return wrap;
  }

  function versionSection(version, rows) {
    // summary のキーは run によって違うので、この version で使われているものを集める
    const keys = [];
    for (const r of rows) {
      for (const k of Object.keys(r.summary || {})) {
        if (!keys.includes(k) && typeof r.summary[k] === 'number') keys.push(k);
      }
    }
    keys.sort();
    const shown = keys.slice(0, 4);
    const paths = new Set(rows.map((r) => r.path));

    const section = el('section', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        icon(version === 'dirty' ? 'alert' : 'layers', { size: 15 }),
        el('h2', { text: `@${version}` }),
        el('span', { class: 'muted', text: `${paths.size} run / ${rows.length} binding` }),
        el('span', { class: 'spacer' }),
        ...shown.map((k) => distChip(k, rows)),
      ]),
    ]);

    section.append(el('div', { class: 'table-scroll' }, [
      el('table', { class: 'grid' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'run' }),
            el('th', { text: 'status' }),
            el('th', { text: 'role' }),
            el('th', { text: 'args' }),
            ...shown.map((k) => el('th', { class: 'num', text: k })),
            el('th', { text: 'created' }),
          ]),
        ]),
        el('tbody', {}, rows.map((r) =>
          el('tr', {}, [
            el('td', {}, [el('a', { href: `#/run/${r.path}`, text: r.name, title: r.path })]),
            el('td', {}, [statusBadge({ status: r.status })]),
            el('td', {}, [
              r.role ? el('code', { text: r.role }) : el('span', { class: 'muted', text: '—' }),
              r.loaded_by !== null && r.loaded_by !== undefined
                ? el('span', { class: 'chip', text: `内部 #${r.loaded_by}`, title: 'ほかの component の実体化中に load された' })
                : null,
              r.failed ? el('span', { class: 'badge bad', text: 'load 失敗' }) : null,
            ]),
            el('td', {}, [argsCell(r.args)]),
            ...shown.map((k) => el('td', { class: 'num', text: fmtNumber(r.summary?.[k], 4) })),
            el('td', { text: fmtTime(r.created_at) }),
          ]),
        )),
      ]),
    ]));
    return section;
  }

  /** metric の分布を 1 行で。「効いたのか」への即答。 */
  function distChip(key, rows) {
    const values = rows.map((r) => r.summary?.[key]).filter((v) => typeof v === 'number');
    if (!values.length) return null;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const min = Math.min(...values);
    const max = Math.max(...values);
    return el('span', {
      class: 'chip',
      title: `${key}: n=${values.length}, min=${min}, max=${max}`,
    }, [
      el('span', { class: 'chip-key', text: key }),
      el('span', { text: `${fmtNumber(mean, 4)}` }),
      el('span', { class: 'muted', text: `(${fmtNumber(min, 3)}–${fmtNumber(max, 3)})` }),
    ]);
  }

  function argsCell(args) {
    const entries = Object.entries(args || {});
    if (!entries.length) return el('span', { class: 'muted', text: '—' });
    return el('span', { class: 'args-cell' }, entries.map(([k, v]) =>
      el('code', { class: 'arg', title: `${k} = ${JSON.stringify(v)}` }, [
        el('span', { class: 'arg-key', text: k }),
        el('span', { text: `=${argText(v)}` }),
      ])));
  }

  // ------------------------------------------------------------- README / ソース

  function readmeTab() {
    const wrap = el('div', { class: 'pane' });
    if (!detail.readme) {
      wrap.append(el('p', { class: 'empty-note', text: 'README.ja.md がありません（component ディレクトリに置くとここに出ます）' }));
      return wrap;
    }
    if (detail.readme_stale) wrap.append(note('warn', 'この README は最新 version の登録より古い'));
    wrap.append(markdownBlock(detail.readme));
    return wrap;
  }

  function sourceTab() {
    const wrap = el('div', { class: 'pane' });
    const host = el('div', { class: 'source-host' });
    const picker = el('select', {
      class: 'select',
      onchange: (e) => { sourceVersion = e.target.value; load(); },
    }, [
      ...detail.versions.map((v) => el('option', { value: v.id, text: `@${v.id}`, selected: v.id === sourceVersion })),
      detail.current_hash ? el('option', { value: 'current', text: 'current/（編集中）', selected: sourceVersion === 'current' }) : null,
    ]);
    wrap.append(
      el('div', { class: 'pane-toolbar' }, [
        icon('terminal', { size: 15 }),
        el('span', { class: 'muted', text: 'ソース' }),
        picker,
      ]),
      host,
    );

    async function load() {
      clear(host);
      host.append(el('p', { class: 'muted', text: '読み込み中…' }));
      try {
        const res = await api.componentSource(detail.id, sourceVersion);
        clear(host);
        host.append(
          el('div', { class: 'file-path' }, [icon('file', { size: 13 }), el('code', { text: res.path })]),
          codeBlock(res.source),
        );
      } catch (e) {
        clear(host);
        host.append(note('warn', `ソースを読めません: ${e.message}`));
      }
    }
    load();
    return wrap;
  }

  // ------------------------------------------------------------- version と diff

  function versionsTab() {
    const wrap = el('div', { class: 'pane' });
    wrap.append(
      el('section', { class: 'panel' }, [
        el('div', { class: 'panel-head' }, [icon('layers', { size: 15 }), el('h2', { text: '登録履歴' }),
          el('span', { class: 'muted', text: '上が古い（配列の順序が登録順）' })]),
        el('table', { class: 'grid' }, [
          el('thead', {}, [el('tr', {}, [
            el('th', { text: 'version' }), el('th', { text: 'registered' }),
            el('th', { text: 'note' }), el('th', { text: '使用 run' }), el('th', { text: 'hash' }),
          ])]),
          el('tbody', {}, detail.versions.map((v) =>
            el('tr', {}, [
              el('td', {}, [el('code', { text: v.id }), v.id === detail.latest ? el('span', { class: 'chip', text: 'latest' }) : null]),
              el('td', { text: fmtTime(v.registered_at) }),
              el('td', { text: v.note || '' }),
              el('td', { class: 'num', text: String((detail.by_version?.[v.id] || []).length) }),
              el('td', {}, [el('code', { class: 'hash', text: (v.hash || '').slice(7, 19), title: v.hash })]),
            ]),
          )),
        ]),
      ]),
    );

    if (detail.versions.length < 2) return wrap;

    const host = el('div', { class: 'source-host' });
    const pick = (value, onchange) => el('select', { class: 'select', onchange }, detail.versions.map((v) =>
      el('option', { value: v.id, text: `@${v.id}`, selected: v.id === value })));
    wrap.append(
      el('section', { class: 'panel' }, [
        el('div', { class: 'panel-head' }, [
          icon('compare', { size: 15 }), el('h2', { text: 'version 間の diff' }),
          el('span', { class: 'spacer' }),
          pick(diffA, (e) => { diffA = e.target.value; loadDiff(); }),
          el('span', { class: 'muted', text: '→' }),
          pick(diffB, (e) => { diffB = e.target.value; loadDiff(); }),
        ]),
        host,
      ]),
    );

    async function loadDiff() {
      clear(host);
      host.append(el('p', { class: 'muted', text: '読み込み中…' }));
      try {
        const res = await api.componentDiff(detail.id, diffA, diffB);
        clear(host);
        host.append(diffBlock(res.diff));
      } catch (e) {
        clear(host);
        host.append(note('warn', `diff を作れません: ${e.message}`));
      }
    }
    loadDiff();
    return wrap;
  }

  await refresh();
  return { node, refresh, live: () => false };
}


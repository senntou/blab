// Experiment 一覧 — run 数 / 実行中の数 / 最終更新。数が増えても読めるよう行で並べる。
// 上に実行中の run の一覧を出し、experiment をたどらずにそこへ飛べるようにする。
// その下に最近の run（既定で畳む）を置き、終わったばかりの run にもすぐ飛べるようにする。

import { api } from '../api.js';
import { docsMark } from '../docs.js';
import { countLabel, filterBox, rankByQuery } from '../filter.js';
import { icon, statusDot } from '../icons.js';
import { getPref, setPref } from '../prefs.js';
import { el, clear, fmtDuration, fmtRelative, fmtTime } from '../util.js';

const COLUMNS = [
  { key: 'name', label: 'experiment' },
  { key: 'n_runs', label: 'run', num: true },
  { key: 'n_running', label: '実行中', num: true },
  { key: 'updated_at', label: '最終更新' },
];

function compare(a, b) {
  if (a === undefined || a === null || a === '') return 1;
  if (b === undefined || b === null || b === '') return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

function cell(e, column) {
  if (column.key === 'name') {
    return el('a', { class: 'cell-name', href: `#/e/${encodeURIComponent(e.path)}` }, [
      icon('flask', { size: 14 }),
      el('span', { text: e.name }),
      docsMark(e.docs),
    ]);
  }
  if (column.key === 'n_running') {
    return e.n_running
      ? el('span', { class: 'num is-running', text: String(e.n_running) })
      : el('span', { class: 'muted', text: '-' });
  }
  if (column.key === 'updated_at') {
    return el('span', { title: e.updated_at ? fmtTime(e.updated_at) : '', text: e.updated_at ? fmtRelative(e.updated_at) : '-' });
  }
  return el('span', { class: 'num', text: String(e[column.key]) });
}

function elapsedSince(iso) {
  const ms = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(ms) ? undefined : Math.max(0, (Date.now() - ms) / 1000);
}

function whereLinks(r) {
  const where = [
    el('a', { href: `#/e/${encodeURIComponent(r.experiment)}`, text: r.experiment }),
  ];
  if (r.group) {
    where.push(
      el('span', { class: 'sep', text: '/' }),
      el('a', { href: `#/group/${r.experiment}/${r.group}`, text: r.group }),
    );
  }
  return el('span', { class: 'live-where' }, where);
}

function runLink(r, status) {
  return el('a', { class: 'live-name', href: `#/run/${r.path}`, title: r.path }, [
    el('span', { class: `status status-${status}`, title: status }, [statusDot()]),
    el('span', { text: r.name || r.path.split('/').pop() }),
  ]);
}

function liveItem(r) {
  return el('li', { class: 'live-item' }, [
    runLink(r, 'running'),
    whereLinks(r),
    el('span', {
      class: 'live-time',
      title: `開始 ${fmtTime(r.created_at)}`,
      text: `${fmtDuration(elapsedSince(r.created_at))} 経過`,
    }),
  ]);
}

/** 実行中（heartbeat が生きている）の run の一覧。無ければ出さない。 */
function livePanel(live) {
  if (!live.length) return null;
  return el('section', { class: 'panel live-panel' }, [
    el('h2', {}, [
      el('span', { class: 'status status-running' }, [statusDot()]),
      el('span', { text: '実行中' }),
      el('span', { class: 'pill pill-running', text: String(live.length) }),
    ]),
    el('ul', { class: 'live-list' }, live.map(liveItem)),
  ]);
}

function recentItem(r) {
  const finished = r.finished_at ? `\n終了 ${fmtTime(r.finished_at)}` : '';
  return el('li', { class: 'live-item' }, [
    runLink(r, r.status),
    whereLinks(r),
    el('span', {
      class: 'live-time',
      title: `開始 ${fmtTime(r.created_at)}${finished}`,
      text: fmtRelative(r.created_at),
    }),
  ]);
}

/**
 * 最近作られた run（サーバが新しい順に数十本返す）。場所を取らないよう既定では畳み、
 * 開いたときも数行分の高さでスクロールさせる。開閉は覚えておく。
 */
function recentPanel(recent, scrollTop) {
  if (!recent.length) return null;
  const list = el('ul', { class: 'live-list recent-list' }, recent.map(recentItem));
  const details = el('details', { class: 'panel recent-panel' }, [
    el('summary', {}, [
      icon('chevron-right', { size: 14, class: 'recent-chevron' }),
      el('span', { text: '最近の run' }),
      el('span', { class: 'muted recent-count', text: `新しい順に ${recent.length} 本` }),
    ]),
    list,
  ]);
  details.open = getPref('experiments.recentOpen', false);
  details.addEventListener('toggle', () => setPref('experiments.recentOpen', details.open));
  // 実行中はポーリングで描き直すので、一覧のスクロール位置を引き継ぐ。
  if (scrollTop) requestAnimationFrame(() => { list.scrollTop = scrollTop; });
  return details;
}

export async function experimentsView() {
  const node = el('div', { class: 'view' });
  let running = 0;

  async function load() {
    const { experiments, live = [], recent = [] } = await api.experiments();
    running = experiments.reduce((n, e) => n + e.n_running, 0);
    const scrollTop = node.querySelector('.recent-list')?.scrollTop || 0;
    clear(node);
    node.append(el('h1', { text: 'Experiments' }));
    const panel = livePanel(live);
    if (panel) node.append(panel);
    const recentNode = recentPanel(recent, scrollTop);
    if (recentNode) node.append(recentNode);

    if (!experiments.length) {
      node.append(
        el('div', { class: 'empty-state' }, [
          icon('flask', { size: 28, class: 'empty-icon' }),
          el('p', { text: 'まだ run がありません。' }),
          el('p', { class: 'muted' }, [
            el('code', { text: 'blab run experiments/<name>.yaml' }),
            ' で最初の run を作れます。',
          ]),
        ]),
      );
      return;
    }

    let sortKey = getPref('experiments.sort', 'updated_at');
    let sortDesc = getPref('experiments.desc', true);
    const count = el('span', { class: 'filter-count' });
    const body = el('div', { class: 'table-body' });
    const box = filterBox({ prefKey: 'filter.experiments', className: 'search', onInput: draw });

    function draw() {
      const sorted = [...experiments].sort((a, b) => {
        const d = compare(a[sortKey], b[sortKey]);
        return sortDesc ? -d : d;
      });
      // 絞り込み中は一致度の高い順（同点は列のソート順）。
      const shown = rankByQuery(sorted, box.input.value, (e) => e.name);
      count.textContent = countLabel(shown.length, experiments.length);
      clear(body);
      if (!shown.length) {
        body.append(el('p', { class: 'muted pad', text: '該当する experiment がありません' }));
        return;
      }

      const head = el('tr', {}, COLUMNS.map((column) =>
        el('th', {
          class: [sortKey === column.key ? 'sorted' : '', column.num ? 'col-num' : ''].filter(Boolean).join(' '),
          onclick: () => {
            if (sortKey === column.key) sortDesc = !sortDesc;
            else {
              sortKey = column.key;
              sortDesc = column.key !== 'name';
            }
            setPref('experiments.sort', sortKey);
            setPref('experiments.desc', sortDesc);
            draw();
          },
        }, [
          el('span', { text: column.label }),
          sortKey === column.key ? icon(sortDesc ? 'chevron-down' : 'chevron-up', { size: 12 }) : null,
        ])));

      const tbody = el('tbody');
      for (const e of shown) {
        tbody.append(
          el('tr', {
            class: 'row-link',
            // 行のどこを押しても開く（名前のリンクはそのまま素通し）。
            onclick: (ev) => {
              if (ev.target.closest('a')) return;
              location.hash = `/e/${encodeURIComponent(e.path)}`;
            },
          }, COLUMNS.map((column) => el('td', { class: column.num ? 'col-num' : '' }, [cell(e, column)]))),
        );
      }
      body.append(el('table', { class: 'nodes experiments-table' }, [el('thead', {}, [head]), tbody]));
    }

    node.append(
      el('div', { class: 'table-wrap' }, [
        el('div', { class: 'table-controls' }, [box, count]),
        body,
      ]),
    );
    draw();
  }

  await load();
  return { node, refresh: load, live: () => running > 0 };
}

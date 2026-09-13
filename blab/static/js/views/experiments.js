// Experiment 一覧 — run 数 / 実行中の数 / 最終更新。数が増えても読めるよう行で並べる。

import { api } from '../api.js';
import { countLabel, filterBox, rankByQuery } from '../filter.js';
import { icon } from '../icons.js';
import { getPref, setPref } from '../prefs.js';
import { el, clear, fmtRelative, fmtTime } from '../util.js';

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

export async function experimentsView() {
  const node = el('div', { class: 'view' });
  let running = 0;

  async function load() {
    const { experiments } = await api.experiments();
    running = experiments.reduce((n, e) => n + e.n_running, 0);
    clear(node);
    node.append(el('h1', { text: 'Experiments' }));

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

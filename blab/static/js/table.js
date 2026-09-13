// run テーブル。列は resolved.yaml の**構成**と summary.* から動的に作る。
//
// 「どの版の resnet18 を使った run か」でソート・フィルタできることが狙い（design.md §11）。
// group は折りたたみ行にし、集計（読むときに導出したもの）をその行に出す。

import { icon, statusDot } from './icons.js';
import { getPref, setPref } from './prefs.js';
import { el, clear, fmtDuration, fmtNumber, fmtRelative, isNumber } from './util.js';

const FIXED = [
  { key: '_name', label: 'run', kind: 'name' },
  { key: '_status', label: '状態', kind: 'status' },
  { key: '_created', label: '作成', kind: 'time' },
  { key: '_duration', label: '所要', kind: 'duration' },
];

function cellValue(row, key) {
  if (key === '_name') return row.name || row.path.split('/').pop();
  if (key === '_status') return row.status || '';
  if (key === '_created') return row.created_at || '';
  if (key === '_duration') return row.duration_sec;
  if (key.startsWith('config.')) return (row.config || {})[key.slice(7)];
  if (key.startsWith('summary.')) {
    const name = key.slice(8);
    if (row.kind === 'group') {
      const stat = (row.aggregate || {})[name];
      return stat ? stat.mean : undefined;
    }
    return (row.summary || {})[name];
  }
  return undefined;
}

function discoverColumns(rows) {
  const config = new Set();
  const summary = new Set();
  for (const row of rows) {
    for (const key of Object.keys(row.config || {})) config.add(key);
    for (const key of Object.keys(row.summary || {})) summary.add(key);
    for (const key of Object.keys(row.aggregate || {})) summary.add(key);
  }
  return [
    ...FIXED,
    ...[...summary].sort().map((k) => ({ key: `summary.${k}`, label: k, kind: 'summary' })),
    ...[...config].sort().map((k) => ({ key: `config.${k}`, label: k, kind: 'config' })),
  ];
}

/** 既定で出す列。構成の列は多くなりがちなので、component の版だけを最初から出す。 */
function defaultVisible(columns) {
  return columns
    .filter((c) => {
      if (c.kind !== 'config') return true;
      // `dataset` は版、`dataset.n` は引数。既定では版だけ。
      return !c.label.includes('.');
    })
    .map((c) => c.key);
}

function renderCell(row, column) {
  const value = cellValue(row, column.key);

  if (column.kind === 'name') {
    const href = row.kind === 'group' ? `#/group/${row.path}` : `#/run/${row.path}`;
    return el('a', { class: 'cell-name', href }, [
      row.kind === 'group' ? icon('layers', { size: 14 }) : null,
      el('span', { text: String(value) }),
    ]);
  }
  if (column.kind === 'status') {
    if (row.kind === 'group') return el('span', { class: 'muted', text: `${row.n_runs} runs` });
    return el('span', { class: `status status-${value}` }, [statusDot(value), el('span', { text: value })]);
  }
  if (column.kind === 'time') return el('span', { text: value ? fmtRelative(value) : '-' });
  if (column.kind === 'duration') return el('span', { text: fmtDuration(value) });

  if (column.kind === 'summary' && row.kind === 'group') {
    const stat = (row.aggregate || {})[column.label];
    if (!stat) return el('span', { class: 'muted', text: '-' });
    return el('span', { class: 'agg', title: `n=${stat.n}` }, [
      el('strong', { text: fmtNumber(stat.mean) }),
      stat.std === null
        ? null
        : el('span', { class: 'agg-std', text: ` ± ${fmtNumber(stat.std)}` }),
    ]);
  }

  if (value === undefined || value === null) return el('span', { class: 'muted', text: '-' });
  if (isNumber(value)) return el('span', { class: 'num', text: fmtNumber(value) });
  return el('span', { text: String(value) });
}

function compare(a, b) {
  if (a === undefined || a === null) return 1;
  if (b === undefined || b === null) return -1;
  if (isNumber(a) && isNumber(b)) return a - b;
  return String(a).localeCompare(String(b));
}

/**
 * @param {Array} rows   /api/nodes の rows
 * @param {object} opts  {ctx, prefKey}
 */
export function nodeTable(rows, { ctx = null, prefKey = 'table' } = {}) {
  const wrap = el('div', { class: 'table-wrap' });
  const columns = discoverColumns(rows);
  const known = new Set(columns.map((c) => c.key));

  let visible = new Set(
    (getPref(`${prefKey}.columns`, null) || defaultVisible(columns)).filter((k) => known.has(k)),
  );
  if (!visible.size) visible = new Set(defaultVisible(columns));

  let sortKey = getPref(`${prefKey}.sort`, '_created');
  let sortDesc = getPref(`${prefKey}.desc`, true);
  let filter = '';
  // group は既定で畳んでおく。開いたものだけ憶えるので、初めて開く group は必ず畳まれた状態から始まる。
  const expanded = new Set(getPref(`${prefKey}.expanded`, []));

  const controls = el('div', { class: 'table-controls' });
  const body = el('div', { class: 'table-body' });
  wrap.append(controls, body);

  function byParent() {
    // group の下の run を、その group 行の直後に畳んで出す。
    const groups = rows.filter((r) => r.kind === 'group');
    const groupPaths = groups.map((g) => g.path);
    const top = [];
    const children = new Map(groupPaths.map((p) => [p, []]));
    for (const row of rows) {
      const parent = groupPaths.find((p) => row.path.startsWith(`${p}/`) && row.path !== p);
      if (parent) children.get(parent).push(row);
      else top.push(row);
    }
    return { top, children };
  }

  function matches(row) {
    if (!filter) return true;
    const needle = filter.toLowerCase();
    if ((row.name || '').toLowerCase().includes(needle)) return true;
    if (row.path.toLowerCase().includes(needle)) return true;
    for (const key of visible) {
      const value = cellValue(row, key);
      if (value !== undefined && value !== null && String(value).toLowerCase().includes(needle)) {
        return true;
      }
    }
    return false;
  }

  function toggleExpanded(path) {
    if (expanded.has(path)) expanded.delete(path);
    else expanded.add(path);
    setPref(`${prefKey}.expanded`, [...expanded]);
    draw();
  }

  function renderRow(row, shown, { child = false } = {}) {
    const isGroup = row.kind === 'group';
    const tr = el('tr', {
      class: [child ? 'child-row' : '', isGroup ? 'row-group' : ''].filter(Boolean).join(' '),
      title: isGroup ? (expanded.has(row.path) ? 'クリックで畳む' : 'クリックで開く') : undefined,
      // group 行はどこをクリックしても開閉する（リンクやチェックボックスは素通しする）。
      onclick: isGroup
        ? (e) => {
            if (e.target.closest('a, input, button')) return;
            toggleExpanded(row.path);
          }
        : undefined,
    });
    if (ctx) {
      const selected = ctx.selection().includes(row.path);
      tr.append(
        el('td', { class: 'pick' }, [
          el('input', {
            type: 'checkbox',
            checked: selected,
            title: '比較に追加',
            onchange: (e) => {
              const next = new Set(ctx.selection());
              if (e.target.checked) next.add(row.path);
              else next.delete(row.path);
              ctx.setSelection([...next]);
            },
          }),
        ]),
      );
    }
    shown.forEach((column, i) => {
      if (column.kind !== 'name') {
        const cell = el('td', {});
        cell.append(renderCell(row, column));
        tr.append(cell);
        return;
      }
      // 比較チェックボックス（ctx あり）が付くと本当の :first-child は pick 列になるので、
      // 子行のインデントは「name 列」であることを明示したこのクラスで狙う（:first-child 不可）。
      const cell = el('td', { class: 'col-name' });
      // flex は中の div にだけ掛ける（td 自体を flex にすると行の高さが他の列とずれる）。
      const wrap = el('div', { class: 'cell-first' });
      if (i === 0 && row.kind === 'group') {
        wrap.append(
          el('button', {
            class: 'twisty',
            type: 'button',
            title: expanded.has(row.path) ? '畳む' : '開く',
            onclick: () => toggleExpanded(row.path),
          }, [icon('chevron-right', { size: 14, class: `twisty-icon${expanded.has(row.path) ? ' open' : ''}` })]),
        );
      }
      wrap.append(renderCell(row, column));
      cell.append(wrap);
      tr.append(cell);
    });
    return tr;
  }

  function draw() {
    clear(controls);
    clear(body);

    controls.append(
      el('label', { class: 'search' }, [
        icon('search', { size: 14 }),
        el('input', {
          type: 'search',
          placeholder: '名前・値で絞り込む',
          value: filter,
          oninput: (e) => {
            filter = e.target.value;
            draw();
          },
        }),
      ]),
      columnPicker(),
    );

    const shown = columns.filter((c) => visible.has(c.key));
    const table = el('table', { class: 'nodes' });
    const head = el('tr');
    if (ctx) head.append(el('th', { class: 'pick' }));
    for (const column of shown) {
      head.append(
        el('th', {
          class: `${sortKey === column.key ? 'sorted' : ''} col-${column.kind}`,
          onclick: () => {
            if (sortKey === column.key) sortDesc = !sortDesc;
            else {
              sortKey = column.key;
              sortDesc = true;
            }
            setPref(`${prefKey}.sort`, sortKey);
            setPref(`${prefKey}.desc`, sortDesc);
            draw();
          },
        }, [
          el('span', { text: column.label }),
          sortKey === column.key ? icon(sortDesc ? 'chevron-down' : 'chevron-up', { size: 12 }) : null,
        ]),
      );
    }
    table.append(el('thead', {}, [head]));

    const { top, children } = byParent();
    const sorted = [...top].sort((a, b) => {
      const d = compare(cellValue(a, sortKey), cellValue(b, sortKey));
      return sortDesc ? -d : d;
    });

    const tbody = el('tbody');
    let count = 0;
    for (const row of sorted) {
      const kids = (children.get(row.path) || []).filter(matches);
      if (!matches(row) && !kids.length) continue;
      tbody.append(renderRow(row, shown));
      count += 1;
      // 絞り込み中は、畳んだままだと一致した子が隠れて見つからなくなるので開く。
      if (row.kind === 'group' && (expanded.has(row.path) || (filter && kids.length))) {
        const sortedKids = kids.sort((a, b) => {
          const d = compare(cellValue(a, sortKey), cellValue(b, sortKey));
          return sortDesc ? -d : d;
        });
        for (const kid of sortedKids) {
          tbody.append(renderRow(kid, shown, { child: true }));
          count += 1;
        }
      }
    }
    table.append(tbody);

    if (!count) {
      body.append(el('p', { class: 'muted pad', text: '該当する run がありません' }));
      return;
    }
    body.append(table);
  }

  function columnPicker() {
    const details = el('details', { class: 'col-picker' });
    details.append(el('summary', {}, [icon('columns', { size: 14 }), el('span', { text: '列' })]));
    const list = el('div', { class: 'col-list' });
    for (const column of columns) {
      list.append(
        el('label', { class: `col-opt col-${column.kind}` }, [
          el('input', {
            type: 'checkbox',
            checked: visible.has(column.key),
            onchange: (e) => {
              if (e.target.checked) visible.add(column.key);
              else visible.delete(column.key);
              setPref(`${prefKey}.columns`, [...visible]);
              draw();
            },
          }),
          el('span', { text: column.label }),
          el('span', { class: 'col-kind', text: column.kind === 'config' ? '構成' : column.kind === 'summary' ? 'summary' : '' }),
        ]),
      );
    }
    details.append(list);
    return details;
  }

  draw();
  return wrap;
}

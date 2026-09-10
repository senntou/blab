// Run テーブル: 列の動的構成 / 表示切替 / 並べ替え / フィルタ / group の折りたたみ。
//
// 表示設定（列・順序・フィルタ・行の詰め方）は prefs.js 経由でサーバ側の
// <root>/.blab-ui/prefs.json に保存する。ブラウザには持たない。

import { icon } from './icons.js';
import { getPref, setPref } from './prefs.js';
import {
  el, clear, debounce, fmtDuration, fmtNumber, fmtTime, isNumber, statusBadge,
} from './util.js';

const BASE_COLUMNS = [
  { key: 'name', label: 'name', kind: 'name' },
  { key: 'status', label: 'status', kind: 'status' },
  { key: 'duration_sec', label: 'duration', kind: 'duration' },
  { key: 'created_at', label: 'created', kind: 'time' },
  { key: 'tags', label: 'tags', kind: 'string' },
  { key: 'id', label: 'id', kind: 'string' },
];

const DEFAULT_HIDDEN = ['id', 'tags'];

const GROUP_META = {
  base: { label: '基本', icon: 'info' },
  bindings: { label: '構成', icon: 'grid' },
  params: { label: 'params', icon: 'sliders' },
  summary: { label: 'summary', icon: 'sigma' },
};

/** 「記録できなかったこと」を run 一覧でも隠さない（design.md §0）。 */
export function recordBadges(row) {
  const c = row.components || {};
  const out = [];
  if (c.dirty) {
    out.push(el('span', { class: 'badge dirty', text: 'dirty', title: '未登録の component を実行した run（ソースは code/_components/ に残っている）' }));
  }
  if ((c.unresolved_imports || []).length) {
    out.push(el('span', {
      class: 'badge warn',
      text: '未記録の参照',
      title: c.unresolved_imports.join('\n'),
    }));
  }
  if ((row.policy_violations || []).length) {
    out.push(el('span', {
      class: 'badge warn',
      text: 'ポリシー違反',
      title: row.policy_violations.join('\n'),
    }));
  }
  if (c.failed) {
    out.push(el('span', { class: 'badge bad', text: 'load 失敗', title: 'entry が例外を投げた load がある' }));
  }
  return out;
}

export function cellValue(row, key) {
  if (key.startsWith('params.')) return row.params[key.slice(7)];
  if (key.startsWith('summary.')) return row.summary[key.slice(8)];
  if (key.startsWith('binding.')) return (row.bindings || {})[key.slice(8)];
  if (key === 'tags') return (row.tags || []).join(', ');
  return row[key];
}

export function buildColumns(rows) {
  const params = [];
  const summary = [];
  const bindings = [];
  for (const row of rows) {
    for (const k of Object.keys(row.params || {})) {
      if (!params.includes(k)) params.push(k);
    }
    for (const k of Object.keys(row.summary || {})) {
      if (!summary.includes(k)) summary.push(k);
    }
    for (const k of Object.keys(row.bindings || {})) {
      if (!bindings.includes(k)) bindings.push(k);
    }
  }
  params.sort();
  summary.sort();
  // component 列（role / id）を先に、その args 列を後ろに。args は数が多いので
  // 初回だけ既定で隠す（列ピッカーから出せる）。
  bindings.sort((a, b) => (a.includes('.') - b.includes('.')) || a.localeCompare(b));
  return [
    ...BASE_COLUMNS.map((c) => ({ ...c, group: 'base' })),
    ...bindings.map((k) => ({
      key: `binding.${k}`,
      label: k,
      kind: k.includes('.') ? 'auto' : 'string',
      group: 'bindings',
      hiddenByDefault: k.includes('.'),
    })),
    ...params.map((k) => ({ key: `params.${k}`, label: k, kind: 'auto', group: 'params' })),
    ...summary.map((k) => ({ key: `summary.${k}`, label: k, kind: 'auto', group: 'summary' })),
  ];
}

/** 再描画で DOM から一時的に外れる入力欄のフォーカスとカーソル位置を保つ。 */
function withFocus(input) {
  if (document.activeElement !== input) return () => {};
  const { selectionStart, selectionEnd } = input;
  return () => {
    input.focus();
    try {
      input.setSelectionRange(selectionStart, selectionEnd);
    } catch (e) {
      /* type によっては選択範囲を持たない */
    }
  };
}

function columnKind(col, rows) {
  if (col.kind !== 'auto') return col.kind;
  for (const row of rows) {
    const v = cellValue(row, col.key);
    if (v === null || v === undefined) continue;
    return isNumber(v) ? 'number' : 'string';
  }
  return 'string';
}

export class RunTable {
  /**
   * @param {object} opts
   * @param {string} opts.storageKey       列設定などの保存キー（experiment 単位）
   * @param {Function} opts.onSelect       選択が変わったときのコールバック
   * @param {boolean} opts.selectable      チェックボックス列を出すか
   */
  constructor({ storageKey, onSelect, selectable = true }) {
    this.prefKey = `table:${storageKey}`;
    this.onSelect = onSelect || (() => {});
    this.selectable = selectable;
    this.rows = [];
    this.columns = [];
    this.selected = new Set();
    this.collapsed = new Set();
    // 初めて見る group はデフォルトで折りたたむ（見たことがあるものは触らない）。
    this.knownGroups = new Set();
    this.state = this.load();
    this.pickerOpen = false;
    this.pickerQuery = '';

    // 骨格は一度だけ作る。再描画で作り直すのは表本体だけにして、
    // 検索欄のフォーカスやスクロール位置がライブ更新で飛ばないようにする。
    this.toolbarNode = el('div', { class: 'table-toolbar' });
    this.scrollNode = el('div', { class: 'table-scroll' });
    this.pickerHost = el('div', { class: 'picker-host' });
    this.node = el('div', { class: `table-wrap${this.state.dense ? ' dense' : ''}` }, [
      this.toolbarNode,
      this.scrollNode,
      this.pickerHost,
    ]);

    this.searchInput = el('input', {
      type: 'search',
      class: 'table-search',
      placeholder: 'すべての列を検索',
      value: this.state.q || '',
      oninput: debounce((e) => {
        this.setState({ q: e.target.value });
      }, 150),
    });
    this.countLabel = el('span', { class: 'row-count' });

    // ピッカーは開閉のたびに作り直すが、この入力欄だけは使い回してフォーカスを保つ。
    this.pickerSearch = el('input', {
      type: 'search',
      placeholder: '列を絞り込む',
      oninput: debounce((e) => {
        this.pickerQuery = e.target.value;
        this.render();
      }, 150),
    });

    // ピッカーの外側クリックで閉じる。
    this.onDocClick = (e) => {
      if (!this.pickerOpen) return;
      if (this.pickerHost.contains(e.target) || this.columnsBtn?.contains(e.target)) return;
      this.pickerOpen = false;
      this.render();
    };
    document.addEventListener('click', this.onDocClick);
  }

  load() {
    const fallback = {
      hidden: [...DEFAULT_HIDDEN],
      seen: [],
      order: [],
      sort: { key: 'created_at', dir: -1 },
      filters: {},
      q: '',
      dense: false,
      showFilters: false,
    };
    const stored = getPref(this.prefKey, null);
    return stored && typeof stored === 'object' ? { ...fallback, ...stored } : fallback;
  }

  save() {
    setPref(this.prefKey, this.state);
  }

  /** 状態を更新して保存し、描き直す。 */
  setState(patch) {
    Object.assign(this.state, patch);
    this.save();
    this.render();
  }

  setRows(rows) {
    this.rows = rows;
    this.columns = buildColumns(rows);
    this.applyDefaultHidden();
    const known = new Set(this.rows.map((r) => r.path));
    for (const p of [...this.selected]) if (!known.has(p)) this.selected.delete(p);
    for (const r of this.rows) {
      if (r.kind === 'group' && !this.knownGroups.has(r.path)) {
        this.knownGroups.add(r.path);
        this.collapsed.add(r.path);
      }
    }
    this.render();
  }

  /** 初めて見る列の既定表示を 1 度だけ適用する（以後はユーザの選択を尊重する）。 */
  applyDefaultHidden() {
    const seen = new Set(this.state.seen || []);
    const hidden = new Set(this.state.hidden || []);
    let changed = false;
    for (const col of this.columns) {
      if (seen.has(col.key)) continue;
      seen.add(col.key);
      changed = true;
      if (col.hiddenByDefault) hidden.add(col.key);
    }
    if (!changed) return;
    this.state.seen = [...seen];
    this.state.hidden = [...hidden];
    this.save();
  }

  orderedColumns() {
    const order = this.state.order || [];
    const byKey = new Map(this.columns.map((c) => [c.key, c]));
    const ordered = [];
    for (const key of order) {
      if (byKey.has(key)) {
        ordered.push(byKey.get(key));
        byKey.delete(key);
      }
    }
    ordered.push(...byKey.values());
    return ordered;
  }

  visibleColumns() {
    const hidden = new Set(this.state.hidden || []);
    return this.orderedColumns().filter((c) => !hidden.has(c.key));
  }

  // --------------------------------------------------------------- フィルタ

  matchesQuery(row) {
    const q = (this.state.q || '').trim().toLowerCase();
    if (!q) return true;
    const haystack = [row.name, row.path, ...this.columns.map((c) => cellValue(row, c.key))];
    return haystack.some((v) => v !== null && v !== undefined && String(v).toLowerCase().includes(q));
  }

  matches(row) {
    if (!this.matchesQuery(row)) return false;
    for (const [key, f] of Object.entries(this.state.filters || {})) {
      const v = cellValue(row, key);
      if (f.status) {
        const status = row.stale ? 'stale' : row.status;
        if (f.status !== 'all' && status !== f.status) return false;
      }
      if (f.text) {
        if (v === null || v === undefined) return false;
        if (!String(v).toLowerCase().includes(f.text.toLowerCase())) return false;
      }
      if (f.min !== undefined && f.min !== '' && f.min !== null) {
        if (!isNumber(v) || v < Number(f.min)) return false;
      }
      if (f.max !== undefined && f.max !== '' && f.max !== null) {
        if (!isNumber(v) || v > Number(f.max)) return false;
      }
    }
    return true;
  }

  setFilter(key, patch) {
    const filters = { ...(this.state.filters || {}) };
    const next = { ...(filters[key] || {}), ...patch };
    for (const [k, v] of Object.entries(next)) {
      if (v === '' || v === null || v === undefined || v === 'all') delete next[k];
    }
    if (Object.keys(next).length) filters[key] = next;
    else delete filters[key];
    this.setState({ filters });
  }

  activeFilterCount() {
    return Object.keys(this.state.filters || {}).length + ((this.state.q || '').trim() ? 1 : 0);
  }

  // ---------------------------------------------------------------- 並べ替え

  sortRows(rows) {
    const { key, dir } = this.state.sort || {};
    if (!key) return rows;
    const sorted = [...rows];
    sorted.sort((a, b) => {
      const va = cellValue(a, key);
      const vb = cellValue(b, key);
      if (va === undefined || va === null) return 1;
      if (vb === undefined || vb === null) return -1;
      if (isNumber(va) && isNumber(vb)) return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
    return sorted;
  }

  toggleSort(key) {
    const cur = this.state.sort || {};
    this.setState({ sort: { key, dir: cur.key === key && cur.dir === 1 ? -1 : 1 } });
  }

  // ------------------------------------------------------------------ 描画

  render() {
    const cols = this.visibleColumns();
    const visible = this.rows.filter((r) => this.matches(r));
    const byPath = new Map(visible.map((r) => [r.path, r]));

    // 親が表示対象に含まれない行をトップレベルとして扱う（group 配下は子として描く）。
    const roots = visible.filter((r) => !r.parent || !byPath.has(r.parent));
    const childrenOf = new Map();
    for (const r of visible) {
      if (r.parent && byPath.has(r.parent)) {
        if (!childrenOf.has(r.parent)) childrenOf.set(r.parent, []);
        childrenOf.get(r.parent).push(r);
      }
    }

    this.renderToolbar(visible.length);

    // name が先頭列のときだけ、横スクロールしても残るように固定する。
    const stickyName = cols.length > 0 && cols[0].key === 'name';

    const table = el('table', {
      class: `runs${stickyName ? ' sticky-name' : ''}${this.selectable ? '' : ' no-check'}`,
    });
    const thead = el('thead');
    const headRow = el('tr', { class: 'head' });
    if (this.selectable) headRow.append(el('th', { class: 'col-check' }, [this.selectAllBox(visible)]));
    for (const col of cols) {
      const sort = this.state.sort || {};
      const active = sort.key === col.key;
      const filtered = !!(this.state.filters || {})[col.key];
      headRow.append(
        el('th', {
          class: [
            `col-${columnKind(col, visible)}`,
            col.group ? `col-${col.group}` : '',
            active ? 'sorted' : '',
            filtered ? 'filtered' : '',
            col.key === 'name' ? 'col-name' : '',
          ].filter(Boolean).join(' '),
          title: `${col.key}（クリックで並べ替え）`,
          onclick: () => this.toggleSort(col.key),
        }, [
          el('span', { class: 'th-label' }, [
            col.group && col.group !== 'base'
              ? el('span', { class: `th-tag th-tag-${col.group}`, text: col.group === 'params' ? 'P' : 'S', title: col.group })
              : null,
            el('span', { class: 'th-text', text: col.label }),
            active
              ? icon(sort.dir === 1 ? 'arrowUp' : 'arrowDown', { size: 12, class: 'th-arrow' })
              : icon('sort', { size: 12, class: 'th-arrow th-arrow-idle' }),
          ]),
        ]),
      );
    }
    thead.append(headRow);
    if (this.state.showFilters) thead.append(this.filterRow(cols, visible));
    table.append(thead);

    const tbody = el('tbody');
    const emit = (row, depth) => {
      tbody.append(this.rowNode(row, cols, depth, childrenOf.has(row.path)));
      if (row.kind === 'group' && !this.collapsed.has(row.path)) {
        for (const child of this.sortRows(childrenOf.get(row.path) || [])) emit(child, depth + 1);
      }
    };
    for (const row of this.sortRows(roots)) emit(row, 0);
    if (!visible.length) {
      tbody.append(
        el('tr', {}, [
          el('td', { class: 'empty', colspan: cols.length + (this.selectable ? 1 : 0) }, [
            icon('search', { size: 22, class: 'empty-icon' }),
            el('div', { text: this.rows.length ? '条件に合う run がありません' : 'run がまだありません' }),
            this.activeFilterCount()
              ? el('button', { class: 'btn', text: '条件をすべて解除', onclick: () => this.clearFilters() })
              : null,
          ]),
        ]),
      );
    }
    table.append(tbody);

    // ライブ更新のたびにスクロール位置が先頭へ戻らないようにする。
    const { scrollTop, scrollLeft } = this.scrollNode;
    clear(this.scrollNode);
    this.scrollNode.append(table);
    this.scrollNode.scrollTop = scrollTop;
    this.scrollNode.scrollLeft = scrollLeft;

    // フィルタ行の sticky 位置は見出し行の実測値に追従させる。
    this.trackHeadHeight(headRow);

    const refocusPicker = withFocus(this.pickerSearch);
    clear(this.pickerHost);
    if (this.pickerOpen) {
      this.pickerHost.append(this.picker());
      refocusPicker();
    }
  }

  trackHeadHeight(headRow) {
    const apply = () => {
      const h = headRow.getBoundingClientRect().height;
      if (h) this.node.style.setProperty('--head-h', `${Math.round(h)}px`);
    };
    if (this.headObserver) this.headObserver.disconnect();
    if (typeof ResizeObserver === 'function') {
      this.headObserver = new ResizeObserver(apply);
      this.headObserver.observe(headRow);
    }
    requestAnimationFrame(apply);
  }

  clearFilters() {
    this.searchInput.value = '';
    this.setState({ filters: {}, q: '' });
  }

  selectAllBox(visible) {
    // group も選べる（CV 全体 と 単発 run を同じ比較表に並べるため）。
    const selectable = visible;
    const allSelected = selectable.length > 0 && selectable.every((r) => this.selected.has(r.path));
    return el('input', {
      type: 'checkbox',
      checked: allSelected,
      title: '表示中の run / group をすべて選択',
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => {
        for (const r of selectable) {
          if (e.target.checked) this.selected.add(r.path);
          else this.selected.delete(r.path);
        }
        this.onSelect([...this.selected]);
        this.render();
      },
    });
  }

  renderToolbar(count) {
    const total = this.rows.length;
    this.countLabel.textContent = count === total ? `${total} 行` : `${count} / ${total} 行`;
    // 入力中に再描画が走ると、DOM から外した時点でフォーカスが外れてしまう。
    const refocus = withFocus(this.searchInput);
    clear(this.toolbarNode);

    this.columnsBtn = el('button', {
      class: `btn${this.pickerOpen ? ' on' : ''}`,
      title: '表示する列を選ぶ',
      onclick: () => {
        this.pickerOpen = !this.pickerOpen;
        this.render();
      },
    }, [icon('columns'), el('span', { text: `列 ${this.visibleColumns().length}/${this.columns.length}` })]);

    this.toolbarNode.append(
      el('span', { class: 'search-box' }, [icon('search', { size: 14, class: 'search-icon' }), this.searchInput]),
      this.countLabel,
      this.selected.size
        ? el('span', { class: 'pill pill-accent', text: `${this.selected.size} 選択中` })
        : null,
      el('span', { class: 'spacer' }),
      el('button', {
        class: `btn${this.state.showFilters ? ' on' : ''}`,
        title: '列ごとのフィルタ行を表示する',
        onclick: () => this.setState({ showFilters: !this.state.showFilters }),
      }, [icon('filter'), el('span', { text: 'フィルタ' }),
        this.activeFilterCount() ? el('span', { class: 'btn-count', text: String(this.activeFilterCount()) }) : null]),
      this.activeFilterCount()
        ? el('button', { class: 'icon-btn', title: 'フィルタと検索を解除', onclick: () => this.clearFilters() }, [icon('x')])
        : null,
      el('button', {
        class: `btn${this.state.dense ? ' on' : ''}`,
        title: '行の高さを詰めて一度に多くの run を見る',
        onclick: () => {
          this.node.classList.toggle('dense', !this.state.dense);
          this.setState({ dense: !this.state.dense });
        },
      }, [icon('table'), el('span', { text: '行を詰める' })]),
      this.columnsBtn,
    );
    refocus();
  }

  filterRow(cols, rows) {
    const tr = el('tr', { class: 'filters' });
    if (this.selectable) tr.append(el('th', { class: 'col-check' }));
    for (const col of cols) {
      const kind = columnKind(col, rows);
      const f = (this.state.filters || {})[col.key] || {};
      let control;
      if (kind === 'status') {
        control = el('select', {
          onchange: (e) => this.setFilter(col.key, { status: e.target.value }),
        }, ['all', 'running', 'finished', 'failed', 'killed', 'stale'].map((s) =>
          el('option', { value: s, text: s, selected: (f.status || 'all') === s }),
        ));
      } else if (kind === 'number' || kind === 'duration') {
        const unit = kind === 'duration' ? '秒' : '';
        control = el('span', { class: 'range' }, [
          el('input', {
            type: 'number', placeholder: `min${unit}`, value: f.min ?? '', step: 'any',
            onchange: (e) => this.setFilter(col.key, { min: e.target.value }),
          }),
          el('input', {
            type: 'number', placeholder: `max${unit}`, value: f.max ?? '', step: 'any',
            onchange: (e) => this.setFilter(col.key, { max: e.target.value }),
          }),
        ]);
      } else {
        control = el('input', {
          type: 'search', placeholder: '含む', value: f.text ?? '',
          onchange: (e) => this.setFilter(col.key, { text: e.target.value }),
        });
      }
      tr.append(el('th', { class: col.key === 'name' ? 'col-name' : '' }, [control]));
    }
    return tr;
  }

  rowNode(row, cols, depth, hasChildren) {
    const isGroup = row.kind === 'group';
    const tr = el('tr', {
      class: `row-${row.kind}${this.selected.has(row.path) ? ' selected' : ''}`,
      dataset: { path: row.path },
    });
    if (this.selectable) {
      tr.append(
        el('td', { class: 'col-check' }, [
          el('input', {
            type: 'checkbox',
            checked: this.selected.has(row.path),
            title: isGroup ? 'group を比較に入れる（集計値で並ぶ）' : null,
            onchange: (e) => {
              if (e.target.checked) this.selected.add(row.path);
              else this.selected.delete(row.path);
              tr.classList.toggle('selected', e.target.checked);
              this.onSelect([...this.selected]);
              this.renderToolbar(this.rows.filter((r) => this.matches(r)).length);
            },
          }),
        ]),
      );
    }
    for (const col of cols) {
      const v = cellValue(row, col.key);
      let content;
      let text = null;
      if (col.key === 'name') {
        const label = el('a', {
          class: 'row-name',
          href: `#/${isGroup ? 'group' : 'run'}/${row.path}`,
          text: row.name,
          title: row.path,
        });
        content = el('span', { class: 'name-cell', style: `padding-left:${depth * 14}px` }, [
          isGroup && hasChildren
            ? el('button', {
                class: 'twisty',
                title: this.collapsed.has(row.path) ? '展開' : '折りたたむ',
                onclick: (e) => {
                  e.preventDefault();
                  if (this.collapsed.has(row.path)) this.collapsed.delete(row.path);
                  else this.collapsed.add(row.path);
                  this.render();
                },
              }, [icon(this.collapsed.has(row.path) ? 'chevronRight' : 'chevronDown', { size: 14 })])
            : el('span', { class: 'twisty-spacer' }),
          isGroup ? icon('layers', { size: 14, class: 'row-icon' }) : icon('dot', { size: 14, class: 'row-icon run' }),
          label,
          isGroup
            ? el('span', { class: 'chip', text: `${row.group_kind || 'group'}${row.n_children ? ` · ${row.n_children}` : ''}` })
            : null,
          ...recordBadges(row),
        ]);
      } else if (col.key === 'status') {
        content = statusBadge(row);
      } else if (col.key === 'duration_sec') {
        content = el('span', { class: 'num', text: fmtDuration(v) });
        text = isNumber(v) ? `${v.toFixed(1)} 秒` : null;
      } else if (col.key === 'created_at') {
        content = fmtTime(v);
        text = v ? String(v) : null;
      } else if (isNumber(v)) {
        content = el('span', { class: 'num', text: fmtNumber(v) });
        text = String(v);
      } else if (v === null || v === undefined) {
        content = el('span', { class: 'muted', text: '—' });
      } else {
        content = el('span', { class: 'cell-text', text: String(v) });
        text = String(v);
      }
      const numeric = isNumber(v) || col.key === 'duration_sec';
      const td = el('td', {
        class: [numeric ? 'num' : '', col.key === 'name' ? 'col-name' : ''].filter(Boolean).join(' '),
      }, [content]);
      // 値は 1 行に収めて省略するので、全体はツールチップで読めるようにする。
      if (text) td.title = text;
      if (isGroup && col.key.startsWith('summary.') && isNumber(v)) {
        td.classList.add('agg');
        const stat = ((row.aggregate || {}).metrics || {})[col.key.slice(8)];
        td.title = stat
          ? `mean ${fmtNumber(stat.mean, 6)} ± ${fmtNumber(stat.std, 3)} (n=${stat.count})`
          : 'mean（集計値）';
      }
      tr.append(td);
    }
    return tr;
  }

  // ------------------------------------------------------------- 列ピッカー

  picker() {
    const hidden = new Set(this.state.hidden || []);
    const ordered = this.orderedColumns();
    const q = this.pickerQuery.trim().toLowerCase();
    const shown = q ? ordered.filter((c) => c.key.toLowerCase().includes(q)) : ordered;

    const setHidden = (keys) => this.setState({ hidden: keys });

    // グループ単位の一括操作（params が数十個あるときに 1 つずつ触らないで済むように）。
    const groupToggle = (group) => {
      const keys = this.columns.filter((c) => (c.group || 'base') === group).map((c) => c.key);
      if (!keys.length) return null;
      const shownCount = keys.filter((k) => !hidden.has(k)).length;
      const meta = GROUP_META[group] || { label: group, icon: 'columns' };
      return el('div', { class: 'picker-group' }, [
        icon(meta.icon, { size: 14 }),
        el('span', { class: 'picker-group-name', text: meta.label }),
        el('span', { class: 'muted', text: `${shownCount}/${keys.length}` }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'link', text: 'すべて表示',
          onclick: () => setHidden([...hidden].filter((k) => !keys.includes(k))),
        }),
        el('button', {
          class: 'link', text: 'すべて隠す',
          onclick: () => setHidden([...new Set([...hidden, ...keys.filter((k) => k !== 'name')])]),
        }),
      ]);
    };

    let dragged = null;
    const list = el('ul', { class: 'picker-list' });
    let lastGroup = null;
    for (const col of shown) {
      const group = col.group || 'base';
      if (group !== lastGroup && !q) {
        const header = groupToggle(group);
        if (header) list.append(el('li', { class: 'picker-sep' }, [header]));
        lastGroup = group;
      }
      const item = el('li', { draggable: q ? 'false' : 'true', dataset: { key: col.key } }, [
        el('span', { class: 'grip', title: q ? '検索中は並べ替えできません' : 'ドラッグで並べ替え' }, [icon('sliders', { size: 13 })]),
        el('label', {}, [
          el('input', {
            type: 'checkbox',
            checked: !hidden.has(col.key),
            onchange: (e) => {
              if (e.target.checked) hidden.delete(col.key);
              else hidden.add(col.key);
              setHidden([...hidden]);
            },
          }),
          el('span', { class: 'picker-key', text: col.key }),
        ]),
      ]);
      item.addEventListener('dragstart', () => {
        dragged = item;
        item.classList.add('dragging');
      });
      item.addEventListener('dragend', () => {
        item.classList.remove('dragging');
        // 検索で一部しか出していないときは並べ替えを確定しない。
        if (!q) {
          this.state.order = [...list.children]
            .filter((li) => li.dataset.key)
            .map((li) => li.dataset.key);
          this.save();
        }
        this.render();
      });
      item.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (!dragged || dragged === item) return;
        const box = item.getBoundingClientRect();
        const after = e.clientY > box.top + box.height / 2;
        list.insertBefore(dragged, after ? item.nextSibling : item);
      });
      list.append(item);
    }
    if (!shown.length) list.append(el('li', { class: 'muted', text: '一致する列がありません' }));

    return el('div', { class: 'picker' }, [
      el('div', { class: 'picker-head' }, [
        icon('columns', { size: 15 }),
        el('strong', { text: '列の表示と順序' }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'icon-btn', title: '閉じる',
          onclick: () => { this.pickerOpen = false; this.render(); },
        }, [icon('x')]),
      ]),
      el('div', { class: 'picker-tools' }, [
        el('span', { class: 'search-box' }, [icon('search', { size: 13, class: 'search-icon' }), this.pickerSearch]),
        el('button', {
          class: 'link',
          text: '既定に戻す',
          onclick: () => this.setState({ order: [], hidden: [...DEFAULT_HIDDEN], seen: [] }),
        }),
      ]),
      list,
      el('div', { class: 'picker-foot' }, [
        icon('info', { size: 13 }),
        el('span', { text: 'この設定は出力ディレクトリに保存されます' }),
      ]),
    ]);
  }

  destroy() {
    document.removeEventListener('click', this.onDocClick);
    if (this.headObserver) this.headObserver.disconnect();
  }
}

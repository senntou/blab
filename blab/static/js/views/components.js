// Component 一覧。レジストリに何が住んでいるかと、それぞれが何回使われたか。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { getPref, setPref } from '../prefs.js';
import { el, clear, fmtTime } from '../util.js';

const TAG_KEY = 'components:tag';

export async function componentsView(ctx) {
  const node = el('div', { class: 'view' });
  const head = el('header', { class: 'view-head' });
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  let items = [];
  let tag = getPref(TAG_KEY, '') || '';
  let query = '';

  async function refresh() {
    items = (await api.components()).components || [];
    render();
  }

  function allTags() {
    const set = new Set();
    for (const c of items) for (const t of c.tags || []) set.add(t);
    return [...set].sort();
  }

  function render() {
    const tags = allTags();
    clear(head);
    head.append(
      el('div', { class: 'title-row' }, [
        icon('grid', { size: 18 }),
        el('h1', { text: 'Components' }),
        el('span', { class: 'muted', text: `${items.length} 件` }),
        el('span', { class: 'spacer' }),
        el('input', {
          type: 'search',
          class: 'table-search',
          placeholder: 'id / タグを検索',
          value: query,
          oninput: (e) => { query = e.target.value; renderBody(); },
        }),
      ]),
      el('div', { class: 'chip-row' }, [
        chip('すべて', '', tag === ''),
        ...tags.map((t) => chip(t, t, tag === t)),
      ]),
    );
    renderBody();
  }

  function chip(label, value, on) {
    return el('button', {
      class: `chip clickable${on ? ' on' : ''}`,
      onclick: () => { tag = value; setPref(TAG_KEY, value); render(); },
    }, [icon('tag', { size: 12 }), el('span', { text: label })]);
  }

  function renderBody() {
    const q = query.trim().toLowerCase();
    const shown = items.filter((c) => {
      if (tag && !(c.tags || []).includes(tag)) return false;
      if (!q) return true;
      return `${c.id} ${(c.tags || []).join(' ')}`.toLowerCase().includes(q);
    });
    clear(body);
    if (!shown.length) {
      body.append(el('div', { class: 'empty-state' }, [
        icon('grid', { size: 28, class: 'empty-icon' }),
        el('p', { text: items.length ? '条件に合う component がありません' : 'component が登録されていません' }),
        items.length ? null : el('p', { class: 'muted', text: 'blab new <id> で雛形を作り、blab register <id> v1 で登録します' }),
      ]));
      return;
    }
    body.append(el('div', { class: 'card-grid' }, shown.map(cardOf)));
  }

  function cardOf(c) {
    if (c.error) {
      return el('a', { class: 'card bad', href: `#/component/${c.id}` }, [
        el('div', { class: 'card-head' }, [icon('alert', { size: 15 }), el('h3', { text: c.id })]),
        el('p', { class: 'card-note', text: c.error }),
      ]);
    }
    const stale = c.readme_stale;
    return el('a', { class: 'card', href: `#/component/${c.id}` }, [
      el('div', { class: 'card-head' }, [
        icon('grid', { size: 15 }),
        el('h3', { text: c.id }),
        el('span', { class: 'spacer' }),
        el('code', { class: 'ver', text: c.latest || '未登録' }),
      ]),
      el('div', { class: 'card-facts' }, [
        fact('layers', `${c.versions.length} version`),
        fact('flask', `${c.n_runs} run`),
        c.last_used ? fact('clock', fmtTime(c.last_used)) : null,
      ]),
      el('div', { class: 'chip-row' }, (c.tags || []).map((t) =>
        el('span', { class: 'chip' }, [icon('tag', { size: 11 }), el('span', { text: t })]))),
      c.current_matches_latest === false
        ? el('p', { class: 'card-note warn', text: 'current/ に未登録の編集がある（この component の load は止まる）' })
        : null,
      stale ? el('p', { class: 'card-note warn', text: 'README が最新 version の登録より古い' }) : null,
      (c.versions_used || []).includes('dirty')
        ? el('p', { class: 'card-note', text: 'dirty 実行された run がある' })
        : null,
    ]);
  }

  function fact(iconName, text) {
    return el('span', { class: 'fact' }, [icon(iconName, { size: 13 }), el('span', { class: 'fact-value', text })]);
  }

  await refresh();
  return { node, refresh, live: () => false };
}

// Component 一覧 — id / タグ / ラベル / 使用 run 数 / 最終使用日。

import { api } from '../api.js';
import { shortHash } from '../config-tree.js';
import { icon } from '../icons.js';
import { getPref, setPref } from '../prefs.js';
import { el, clear, fmtRelative } from '../util.js';

export async function componentsView() {
  const node = el('div', { class: 'view' });
  let components = [];
  let tagFilter = getPref('components.tag', null);

  function allTags() {
    const tags = new Set();
    for (const c of components) for (const t of c.tags) tags.add(t);
    return [...tags].sort();
  }

  function draw() {
    clear(node);
    node.append(el('h1', { text: 'Components' }));

    const tags = allTags();
    if (tags.length) {
      const bar = el('div', { class: 'tag-filter' });
      bar.append(
        el('button', {
          class: `tag ${tagFilter ? '' : 'on'}`,
          onclick: () => { tagFilter = null; setPref('components.tag', null); draw(); },
          text: 'すべて',
        }),
      );
      for (const tag of tags) {
        bar.append(
          el('button', {
            class: `tag ${tagFilter === tag ? 'on' : ''}`,
            onclick: () => { tagFilter = tag; setPref('components.tag', tag); draw(); },
            text: `#${tag}`,
          }),
        );
      }
      node.append(bar);
    }

    const shown = components.filter((c) => !tagFilter || c.tags.includes(tagFilter));
    if (!shown.length) {
      node.append(
        el('div', { class: 'empty-state' }, [
          icon('box', { size: 28, class: 'empty-icon' }),
          el('p', { text: 'component がありません。' }),
          el('p', { class: 'muted' }, [el('code', { text: 'blab new <id>' }), ' で作れます。']),
        ]),
      );
      return;
    }

    const table = el('table', { class: 'nodes' });
    table.append(
      el('thead', {}, [
        el('tr', {}, [
          el('th', { text: 'id' }),
          el('th', { text: '作業コピー' }),
          el('th', { text: 'タグ' }),
          el('th', { text: 'ラベル' }),
          el('th', { text: '使用 run' }),
          el('th', { text: '版' }),
          el('th', { text: '最終使用' }),
        ]),
      ]),
    );
    const body = el('tbody');
    for (const c of shown) {
      body.append(
        el('tr', {}, [
          el('td', { class: 'cell-first' }, [
            el('a', { class: 'cell-name', href: `#/component/${encodeURIComponent(c.id)}` }, [
              icon('box', { size: 14 }),
              el('span', { text: c.id }),
            ]),
          ]),
          el('td', {}, [
            c.short ? el('code', { text: c.short, title: c.hash }) : el('span', { class: 'muted', text: '-' }),
            c.label ? el('span', { class: 'chip chip-label', text: `@${c.label}` }) : null,
          ]),
          el('td', {}, c.tags.map((t) => el('span', { class: 'tag-chip', text: `#${t}` }))),
          el('td', {}, c.labels.map((l) => el('span', { class: 'chip chip-label', title: shortHash(l.hash), text: l.name }))),
          el('td', { class: 'num', text: String(c.n_runs) }),
          el('td', { class: 'num', text: String(c.n_versions) }),
          el('td', { text: c.last_used ? fmtRelative(c.last_used) : '-' }),
        ]),
      );
    }
    table.append(body);
    node.append(el('div', { class: 'table-wrap' }, [table]));
  }

  async function load() {
    const res = await api.components();
    components = res.components;
    draw();
  }

  await load();
  return { node, refresh: load, live: () => false };
}

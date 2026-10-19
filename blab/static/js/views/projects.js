// プロジェクトの選択 — グローバル索引（~/.blab/projects.json）に載ったものが候補。
//
// プロジェクトの外で `blab ui` を起動したときと、URL の ?p= が索引に無いときに出る。

import { icon } from '../icons.js';
import { el } from '../util.js';

/** そのプロジェクトを開くページの URL。プロジェクトが変わればパスも別物なので hash は捨てる。 */
export function projectHref(uid) {
  return `?p=${encodeURIComponent(uid)}#/`;
}

export function projectsView(projects, { message = null } = {}) {
  const node = el('div', { class: 'view' }, [el('h1', { text: 'プロジェクト' })]);
  if (message) node.append(el('p', { class: 'muted', text: message }));

  if (!projects.length) {
    node.append(
      el('div', { class: 'empty-state' }, [
        icon('box', { size: 28, class: 'empty-icon' }),
        el('p', { text: '登録されたプロジェクトがありません。' }),
        el('p', { class: 'muted' }, [
          'プロジェクトのルートで ',
          el('code', { text: 'blab init' }),
          ' か ',
          el('code', { text: 'blab link <path>' }),
          ' を実行すると、ここに出ます。',
        ]),
      ]),
    );
    return { node, refresh: async () => {} };
  }

  const tbody = el('tbody');
  for (const p of projects) {
    const name = p.available
      ? el('a', { class: 'cell-name', href: projectHref(p.uid) }, [icon('box', { size: 14 }), el('span', { text: p.name })])
      : el('span', { class: 'cell-name muted' }, [icon('box', { size: 14 }), el('span', { text: p.name })]);
    tbody.append(
      el('tr', {
        class: p.available ? 'row-link' : '',
        onclick: (ev) => {
          if (!p.available || ev.target.closest('a')) return;
          location.href = projectHref(p.uid);
        },
      }, [
        el('td', {}, [name]),
        el('td', {}, [el('code', { class: 'muted', text: p.path, title: p.path })]),
        el('td', {}, [p.available ? null : el('span', { class: 'muted', text: '見つかりません' })]),
      ]),
    );
  }
  const head = el('tr', {}, ['プロジェクト', 'パス', ''].map((text) => el('th', { text })));
  node.append(
    el('div', { class: 'table-wrap' }, [
      el('table', { class: 'nodes projects-table' }, [el('thead', {}, [head]), tbody]),
    ]),
  );
  return { node, refresh: async () => {} };
}

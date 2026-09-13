// 1 つの Experiment 配下の run / group 一覧。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { nodeTable } from '../table.js';
import { el, clear } from '../util.js';

export async function runsView(ctx, path) {
  const node = el('div', { class: 'view' });
  let rows = [];

  async function load() {
    const res = await api.nodes({ experiment: path });
    rows = res.rows;
    clear(node);
    node.append(
      el('div', { class: 'crumbs' }, [
        el('a', { href: '#/' }, [icon('chevron-left', { size: 14 }), el('span', { text: 'Experiments' })]),
      ]),
      el('div', { class: 'head-row' }, [
        el('h1', {}, [icon('flask', { size: 20 }), el('span', { text: path })]),
        el('a', { class: 'btn trash-link', href: `#/group/${path}/_trash`, title: '削除した run（_trash）を見る' }, [
          icon('trash', { size: 14 }),
          el('span', { text: 'ゴミ箱' }),
        ]),
      ]),
    );
    if (!rows.length) {
      node.append(el('p', { class: 'muted pad', text: 'run がありません' }));
      return;
    }
    node.append(nodeTable(rows, { ctx, prefKey: `runs.${path}`, onChanged: load }));
  }

  await load();
  return {
    node,
    refresh: load,
    live: () => rows.some((r) => r.status === 'running' || r.status === 'stale'),
  };
}

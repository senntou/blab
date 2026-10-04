// 1 つの Experiment 配下の run / group 一覧と、experiment 自身のドキュメント。

import { api } from '../api.js';
import { docsMark, docsPanel } from '../docs.js';
import { icon } from '../icons.js';
import { nodeTable } from '../table.js';
import { el, clear, tabs } from '../util.js';

export async function runsView(ctx, path) {
  const node = el('div', { class: 'view' });
  let rows = [];

  async function load() {
    const [res, info] = await Promise.all([
      api.nodes({ experiment: path }),
      // ドキュメントが読めなくても一覧は出す。
      api.docs(path).catch(() => ({ docs: [], notes: '' })),
    ]);
    rows = res.rows;
    const docs = info.docs || [];
    clear(node);
    node.append(
      el('div', { class: 'crumbs' }, [
        el('a', { href: '#/' }, [icon('chevron-left', { size: 14 }), el('span', { text: 'Experiments' })]),
      ]),
      el('div', { class: 'head-row' }, [
        el('h1', {}, [icon('flask', { size: 20 }), el('span', { text: path }), docsMark(docs.map((d) => d.name))]),
        el('a', { class: 'btn trash-link', href: `#/group/${path}/_trash`, title: '削除した run（_trash）を見る' }, [
          icon('trash', { size: 14 }),
          el('span', { text: 'ゴミ箱' }),
        ]),
      ]),
    );
    node.append(
      tabs(
        [
          {
            id: 'runs',
            label: 'run',
            icon: 'flask',
            count: rows.filter((r) => r.kind === 'run').length,
            render: () => (rows.length
              ? nodeTable(rows, { ctx, prefKey: `runs.${path}`, onChanged: load })
              : el('p', { class: 'muted pad', text: 'run がありません' })),
          },
          {
            id: 'docs',
            label: 'ドキュメント',
            icon: 'note',
            count: docs.length || null,
            render: () => docsPanel(path, docs, { notes: info.notes, kind: 'experiment' }),
          },
        ],
        // 一覧を見に来ることが多いので、既定は run。ドキュメントを開いたらそれを憶える。
        { prefKey: 'experiment.tab' },
      ),
    );
  }

  await load();
  return {
    node,
    refresh: load,
    live: () => rows.some((r) => r.status === 'running' || r.status === 'stale'),
  };
}

// Run テーブル（中心画面）。experiment 配下の run / group を 1 枚の表にする。
//
// レイアウトは「表の中だけがスクロールする」形。ページ自体は縦にも横にも
// スクロールしない（table-scroll が両方向のはみ出しを引き受ける）。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, fmtRelative } from '../util.js';
import { RunTable } from '../table.js';

export async function runsView(ctx, experiment) {
  const node = el('div', { class: 'view view-fill' });
  const counts = el('div', { class: 'head-meta' });
  const head = el('header', { class: 'view-head compact' }, [
    el('div', { class: 'title-row' }, [
      el('a', { href: '#/', class: 'crumb' }, [icon('layers', { size: 15 }), el('span', { text: 'Experiments' })]),
      el('span', { class: 'crumb-sep', text: '/' }),
      el('h1', { text: experiment }),
    ]),
    counts,
  ]);
  const body = el('div', { class: 'view-body' });
  node.append(head, body);

  const table = new RunTable({
    storageKey: experiment,
    onSelect: (paths) => {
      // ほかの experiment で選んだ run は残したまま、この画面ぶんだけ入れ替える。
      const others = ctx.selection().filter((p) => !p.startsWith(`${experiment}/`));
      ctx.setSelection([...others, ...paths]);
    },
  });
  table.selected = new Set(ctx.selection().filter((p) => p.startsWith(`${experiment}/`)));

  let live = false;

  async function refresh() {
    const { rows } = await api.nodes(experiment);
    live = rows.some((r) => r.status === 'running');
    table.setRows(rows);

    const runs = rows.filter((r) => r.kind === 'run');
    const groups = rows.filter((r) => r.kind === 'group');
    const running = runs.filter((r) => r.status === 'running');
    const latest = Math.max(0, ...runs.map((r) => new Date(r.created_at || 0).getTime() / 1000 || 0));
    clear(counts);
    counts.append(
      el('span', { class: 'pill' }, [icon('table', { size: 13 }), el('span', { text: `${runs.length} runs` })]),
      groups.length
        ? el('span', { class: 'pill' }, [icon('layers', { size: 13 }), el('span', { text: `${groups.length} groups` })])
        : null,
      running.length
        ? el('span', { class: 'pill pill-running' }, [icon('play', { size: 13 }), el('span', { text: `${running.length} 実行中` })])
        : null,
      latest
        ? el('span', { class: 'pill pill-quiet' }, [icon('clock', { size: 13 }), el('span', { text: `最新 ${fmtRelative(latest)}` })])
        : null,
    );

    if (body.firstChild !== table.node) {
      clear(body);
      body.append(table.node);
    }
  }

  await refresh();
  return { node, refresh, live: () => live, destroy: () => table.destroy() };
}

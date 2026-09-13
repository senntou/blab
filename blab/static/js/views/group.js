// Group 詳細 — mean ± std の集計表（読むときに導出）、fold 重ね描き、
// **run の所属 group の付け替え**（UI が行う唯一の書き込み）。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { metricsOverlay } from '../overlay.js';
import { nodeTable } from '../table.js';
import { el, clear, fmtNumber, tabs } from '../util.js';

function aggregateTable(detail) {
  const stats = detail.aggregate || {};
  const keys = Object.keys(stats).sort();
  if (!keys.length) {
    return el('p', { class: 'muted pad', text: 'finished な run の summary がまだありません' });
  }
  const table = el('table', { class: 'args' });
  table.append(
    el('caption', {}, [
      el('span', { text: '集計（配下の葉の run から、読むときに導出）' }),
    ]),
    el('thead', {}, [
      el('tr', {}, ['metric', 'mean', 'std', 'min', 'max', 'n'].map((h) => el('th', { text: h }))),
    ]),
  );
  const body = el('tbody');
  for (const key of keys) {
    const s = stats[key];
    body.append(
      el('tr', {}, [
        el('th', { text: key }),
        el('td', { class: 'num' }, [el('strong', { text: fmtNumber(s.mean) })]),
        el('td', { class: 'num', text: s.std === null ? '-' : fmtNumber(s.std) }),
        el('td', { class: 'num', text: fmtNumber(s.min) }),
        el('td', { class: 'num', text: fmtNumber(s.max) }),
        el('td', { class: 'num', text: String(s.n) }),
      ]),
    );
  }
  table.append(body);
  return table;
}

function movePanel(detail, reload, notify) {
  const runs = (detail.runs || []).filter((r) => r.kind === 'run');
  const host = el('section', { class: 'panel' });
  host.append(
    el('h2', {}, [icon('move', { size: 16 }), el('span', { text: 'run の所属を変える' })]),
    el('p', { class: 'muted' }, [
      '付け忘れや打ち間違いの救済。',
      el('code', { text: 'blab mv' }),
      ' と同じ操作を呼ぶ。移動の事実は ',
      el('code', { text: 'moved_from' }),
      ' に残る。',
    ]),
  );
  const input = el('input', { type: 'text', placeholder: '移動先の group 名（空で experiment 直下へ）' });
  const picker = el('select', {}, runs.map((r) => el('option', { value: r.path, text: r.name || r.path.split('/').pop() })));
  host.append(
    el('div', { class: 'move-row' }, [
      picker,
      input,
      el('button', {
        class: 'btn',
        onclick: async () => {
          try {
            await api.moveGroup(picker.value, input.value.trim() || null);
            await reload();
          } catch (e) {
            notify(`移動できませんでした: ${e.message}`);
          }
        },
      }, [icon('move', { size: 14 }), el('span', { text: '移動' })]),
    ]),
  );
  return host;
}

export async function groupView(ctx, path) {
  const node = el('div', { class: 'view' });
  let detail = null;

  async function load() {
    detail = await api.detail(path);
    clear(node);

    const experiment = path.split('/')[0];
    node.append(
      el('div', { class: 'crumbs' }, [
        el('a', { href: '#/' }, [el('span', { text: 'Experiments' })]),
        el('span', { class: 'sep', text: '/' }),
        el('a', { href: `#/e/${encodeURIComponent(experiment)}` }, [el('span', { text: experiment })]),
      ]),
      el('h1', {}, [icon('layers', { size: 20 }), el('span', { text: detail.name || path.split('/').pop() })]),
      el('p', { class: 'muted', text: `${detail.n_runs} 本の run` }),
      aggregateTable(detail),
    );

    const runs = detail.runs || [];
    node.append(
      tabs(
        [
          { id: 'runs', label: 'run', icon: 'flask', count: runs.length,
            render: () => nodeTable(runs, { ctx, prefKey: `group.${path}`, onChanged: load }) },
          { id: 'overlay', label: '重ね描き', icon: 'chart',
            render: () => {
              const runNodes = runs.filter((r) => r.kind === 'run');
              const labels = new Map(runNodes.map((r) => [r.path, r.name || r.path.split('/').pop()]));
              const overlay = metricsOverlay(runNodes.map((r) => r.path), labels);
              overlay.refresh();
              return overlay.node;
            } },
          { id: 'move', label: '所属の変更', icon: 'move',
            render: () => movePanel(detail, load, ctx.notify) },
        ],
        { prefKey: 'group.tab' },
      ),
    );
  }

  await load();
  return {
    node,
    refresh: load,
    live: () => detail && (detail.runs || []).some((r) => r.status === 'running' || r.status === 'stale'),
  };
}

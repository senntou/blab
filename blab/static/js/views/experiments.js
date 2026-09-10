// Experiment 一覧。カードで run 数 / group 数 / 実行中 / 最終更新を見せる。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, fmtRelative, nodeActions } from '../util.js';

export async function experimentsView(ctx) {
  const node = el('div', { class: 'view' });
  let live = false;

  async function refresh() {
    const { experiments } = await api.tree();
    live = experiments.some((e) => (e.counts || {}).running > 0);
    const totals = experiments.reduce(
      (acc, e) => ({
        run: acc.run + ((e.counts || {}).run || 0),
        group: acc.group + ((e.counts || {}).group || 0),
        running: acc.running + ((e.counts || {}).running || 0),
      }),
      { run: 0, group: 0, running: 0 },
    );

    clear(node);
    node.append(
      el('header', { class: 'view-head' }, [
        el('div', { class: 'title-row' }, [
          icon('layers', { size: 20, class: 'title-icon' }),
          el('h1', { text: 'Experiments' }),
          el('span', { class: 'pill', text: `${experiments.length} experiments` }),
        ]),
        el('div', { class: 'stat-row' }, [
          statTile('table', String(totals.run), 'runs'),
          statTile('layers', String(totals.group), 'groups'),
          statTile('play', String(totals.running), '実行中', totals.running ? 'running' : ''),
        ]),
      ]),
    );

    if (!experiments.length) {
      node.append(
        el('div', { class: 'empty-state' }, [
          icon('flask', { size: 30, class: 'empty-icon' }),
          el('p', { text: 'まだ run がありません。' }),
          el('p', { class: 'muted' }, [
            el('code', { text: 'blab.init(experiment="...")' }),
            ' で記録すると、ここに出ます。',
          ]),
        ]),
      );
      return;
    }

    node.append(
      el('div', { class: 'card-grid' }, experiments.map((e) => {
        const c = e.counts || {};
        return el('a', { class: 'card', href: `#/e/${e.path}` }, [
          el('div', { class: 'card-head' }, [
            icon('flask', { size: 16, class: 'card-icon' }),
            el('span', { class: 'card-title', text: e.name }),
            c.running
              ? el('span', { class: 'badge badge-running' }, [el('span', { text: `${c.running} running` })])
              : null,
            el('span', { class: 'spacer' }),
            nodeActions(e.path, e.name, {
              notify: ctx.notify,
              onRenamed: refresh,
              onDeleted: refresh,
            }),
          ]),
          el('div', { class: 'card-stats' }, [
            cardStat('table', c.run ?? 0, 'runs'),
            cardStat('layers', c.group ?? 0, 'groups'),
          ]),
          el('div', { class: 'card-foot' }, [
            icon('clock', { size: 13 }),
            el('span', { text: `更新 ${fmtRelative(e.updated_at)}` }),
          ]),
        ]);
      })),
    );
  }

  function statTile(name, value, label, tone = '') {
    return el('div', { class: `stat-tile${tone ? ` ${tone}` : ''}` }, [
      icon(name, { size: 16, class: 'stat-icon' }),
      el('span', { class: 'stat-value', text: value }),
      el('span', { class: 'stat-label', text: label }),
    ]);
  }

  function cardStat(name, value, label) {
    return el('span', { class: 'card-stat' }, [
      icon(name, { size: 14 }),
      el('strong', { text: String(value) }),
      el('span', { class: 'muted', text: label }),
    ]);
  }

  await refresh();
  return { node, refresh, live: () => live };
}

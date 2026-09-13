// Experiment 一覧 — run 数 / 実行中の数 / 最終更新。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, fmtRelative } from '../util.js';

export async function experimentsView() {
  const node = el('div', { class: 'view' });
  let running = 0;

  async function load() {
    const { experiments } = await api.experiments();
    running = experiments.reduce((n, e) => n + e.n_running, 0);
    clear(node);
    node.append(el('h1', { text: 'Experiments' }));

    if (!experiments.length) {
      node.append(
        el('div', { class: 'empty-state' }, [
          icon('flask', { size: 28, class: 'empty-icon' }),
          el('p', { text: 'まだ run がありません。' }),
          el('p', { class: 'muted' }, [
            el('code', { text: 'blab run experiments/<name>.yaml' }),
            ' で最初の run を作れます。',
          ]),
        ]),
      );
      return;
    }

    const list = el('div', { class: 'card-grid' });
    for (const e of experiments) {
      list.append(
        el('a', { class: 'card', href: `#/e/${encodeURIComponent(e.path)}` }, [
          el('h2', {}, [icon('flask', { size: 16 }), el('span', { text: e.name })]),
          el('dl', { class: 'card-stats' }, [
            el('div', {}, [el('dt', { text: 'run' }), el('dd', { text: String(e.n_runs) })]),
            e.n_running
              ? el('div', { class: 'is-running' }, [
                  el('dt', { text: '実行中' }),
                  el('dd', { text: String(e.n_running) }),
                ])
              : null,
            el('div', {}, [
              el('dt', { text: '最終更新' }),
              el('dd', { text: e.updated_at ? fmtRelative(e.updated_at) : '-' }),
            ]),
          ]),
        ]),
      );
    }
    node.append(list);
  }

  await load();
  return { node, refresh: load, live: () => running > 0 };
}

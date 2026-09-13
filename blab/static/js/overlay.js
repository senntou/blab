// 複数 run の metrics 重ね描き（Group 詳細と比較ビューで共有）。

import { api } from './api.js';
import { lineChart, seriesPoints } from './chart.js';
import { countLabel, filterBox, rankByQuery } from './filter.js';
import { icon } from './icons.js';
import { getPref, setPref } from './prefs.js';
import { el, clear, colorFor } from './util.js';

const X_AXES = [
  ['step', 'step'],
  ['epoch', 'epoch'],
  ['time', '経過時間'],
];

const PREF_KEY = 'overlay';

/**
 * @param {string[]} paths run のパス
 * @param {Map<string,string>} labels パス → 表示名
 * @returns {{node: HTMLElement, refresh: Function}}
 */
export function metricsOverlay(paths, labels) {
  const saved = getPref(PREF_KEY, {});
  const opts = { xAxis: saved.xAxis || 'step', logY: !!saved.logY, filter: getPref('filter.overlay', '') };
  const node = el('section', { class: 'panel charts' });
  const grid = el('div', { class: 'chart-grid' });
  const count = el('span', { class: 'filter-count' });
  let data = new Map(); // path -> metrics レスポンス

  const save = () => setPref(PREF_KEY, { xAxis: opts.xAxis, logY: opts.logY });

  const head = el('div', { class: 'panel-head' }, [
    icon('chart', { size: 15 }),
    el('h2', { text: 'metrics' }),
    el('span', { class: 'muted', text: `${paths.length} runs` }),
    el('span', { class: 'spacer' }),
    filterBox({
      prefKey: 'filter.overlay',
      onInput: (value) => {
        opts.filter = value;
        draw();
      },
    }),
    count,
    el('span', { class: 'segmented' }, X_AXES.map(([value, label]) =>
      el('button', {
        class: 'btn',
        text: label,
        dataset: { axis: value },
        onclick: () => {
          opts.xAxis = value;
          save();
          syncButtons();
          draw();
        },
      }),
    )),
    el('label', { class: 'checkline' }, [
      el('input', {
        type: 'checkbox',
        checked: opts.logY,
        onchange: (e) => {
          opts.logY = e.target.checked;
          save();
          draw();
        },
      }),
      'log y',
    ]),
  ]);
  node.append(head, grid);

  function syncButtons() {
    for (const btn of head.querySelectorAll('button[data-axis]')) {
      btn.classList.toggle('on', btn.dataset.axis === opts.xAxis);
    }
  }
  syncButtons();

  async function refresh() {
    const results = await Promise.all(
      paths.map(async (p) => {
        try {
          return [p, await api.metrics(p)];
        } catch (e) {
          return [p, null];
        }
      }),
    );
    data = new Map(results);
    draw();
  }

  function draw() {
    clear(grid);
    const keys = [];
    for (const m of data.values()) {
      if (!m) continue;
      for (const k of m.keys) if (!keys.includes(k)) keys.push(k);
    }
    const shown = rankByQuery(keys.sort(), opts.filter);
    count.textContent = countLabel(shown.length, keys.length);
    if (!shown.length) {
      grid.append(el('p', { class: 'empty-note' }, [
        icon('info', { size: 14 }),
        el('span', { text: keys.length ? '一致するキーがありません' : 'metrics がありません' }),
      ]));
      return;
    }
    for (const key of shown) {
      const series = [];
      paths.forEach((p, i) => {
        const m = data.get(p);
        const s = m && m.series[key];
        if (!s) return;
        series.push({
          label: labels.get(p) || p,
          color: colorFor(i),
          points: seriesPoints(s, opts.xAxis),
        });
      });
      grid.append(lineChart({ title: key, xLabel: opts.xAxis, logY: opts.logY, series }));
    }
  }

  return { node, refresh };
}

// 1 つのグラフを大きく開き、軸の範囲などを調整しながら見るためのダイアログ。
// ここでの調整はその場限りで、閉じれば消える（prefs にも書かない）。

import { chartRange, lineChart } from './chart.js';
import { icon } from './icons.js';
import { el, clear, fmtNumber } from './util.js';

const X_AXES = [
  ['auto', 'auto'],
  ['step', 'step'],
  ['epoch', 'epoch'],
  ['time', '経過時間'],
];

/** 入力欄の文字列を数値に。空や数値でないものは null（自動）。 */
export function parseLimit(text) {
  const t = String(text ?? '').trim();
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

/** 表示中の x 範囲に入る点の last / min / max。 */
export function seriesStats(points, xlim = null) {
  const lo = xlim && xlim[0] !== null ? xlim[0] : -Infinity;
  const hi = xlim && xlim[1] !== null ? xlim[1] : Infinity;
  let last = null;
  let min = null;
  let max = null;
  let n = 0;
  for (const [x, y] of points) {
    if (!Number.isFinite(y) || !(x >= lo && x <= hi)) continue;
    n += 1;
    last = y;
    if (min === null || y < min) min = y;
    if (max === null || y > max) max = y;
  }
  return { n, last, min, max };
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.xAxis   開いたときの x 軸の選択（'auto' | 'step' | 'epoch' | 'time'）
 * @param {boolean} opts.logY
 * @param {Function} opts.build  (xAxis の選択) => {axis, series: [{label, color, points}]}
 */
export function openChartFocus({ title, xAxis = 'auto', logY = false, build }) {
  const state = { xAxis, logY, xlim: [null, null], ylim: [null, null], hidden: new Set() };
  let built = build(state.xAxis);

  const chartHost = el('div', { class: 'focus-chart' });
  const statsHost = el('div', { class: 'focus-stats' });

  const limitInput = (axis, i, placeholder) =>
    el('input', {
      type: 'text',
      inputmode: 'decimal',
      class: 'focus-limit',
      placeholder,
      'aria-label': `${axis} ${placeholder}`,
      onchange: (e) => {
        const v = parseLimit(e.target.value);
        e.target.value = v === null ? '' : String(v);
        state[axis][i] = v;
        draw();
      },
    });
  const inputs = {
    xlim: [limitInput('xlim', 0, 'min'), limitInput('xlim', 1, 'max')],
    ylim: [limitInput('ylim', 0, 'min'), limitInput('ylim', 1, 'max')],
  };
  const syncInputs = () => {
    for (const axis of ['xlim', 'ylim']) {
      inputs[axis].forEach((input, i) => {
        const v = state[axis][i];
        input.value = v === null ? '' : String(Number(v.toPrecision(6)));
      });
    }
  };

  const axisButtons = X_AXES.map(([value, label]) =>
    el('button', {
      class: 'btn',
      text: label,
      dataset: { axis: value },
      onclick: () => {
        if (state.xAxis === value) return;
        state.xAxis = value;
        // x の単位が変わるので、x の範囲は意味を失う。
        state.xlim = [null, null];
        built = build(state.xAxis);
        syncInputs();
        draw();
      },
    }),
  );

  const controls = el('div', { class: 'focus-controls' }, [
    el('span', { class: 'segmented' }, axisButtons),
    el('label', { class: 'checkline' }, [
      el('input', {
        type: 'checkbox',
        checked: state.logY,
        onchange: (e) => {
          state.logY = e.target.checked;
          draw();
        },
      }),
      'log y',
    ]),
    el('span', { class: 'focus-lim' }, [el('span', { class: 'focus-lim-label', text: 'x' }), inputs.xlim[0], '–', inputs.xlim[1]]),
    el('span', { class: 'focus-lim' }, [el('span', { class: 'focus-lim-label', text: 'y' }), inputs.ylim[0], '–', inputs.ylim[1]]),
    el('button', {
      class: 'btn',
      title: '範囲を自動に戻す',
      onclick: () => {
        state.xlim = [null, null];
        state.ylim = [null, null];
        syncInputs();
        draw();
      },
    }, [icon('refresh', { size: 13 }), '範囲をリセット']),
  ]);

  function visibleSeries() {
    return built.series.filter((_, i) => !state.hidden.has(i));
  }

  function draw() {
    for (const btn of axisButtons) btn.classList.toggle('on', btn.dataset.axis === state.xAxis);
    clear(chartHost);
    const shown = visibleSeries();
    // viewBox を実際の表示幅に合わせ、目盛りの文字が縮んで読めなくならないようにする。
    const width = Math.round(Math.min(Math.max(chartHost.clientWidth || 1000, 320) - 26, 1100));
    chartHost.append(
      lineChart({
        series: shown,
        xLabel: built.axis,
        logY: state.logY,
        legend: false,
        xlim: state.xlim,
        ylim: state.ylim,
        width,
        height: Math.round(Math.min(Math.max(width * 0.5, 300), 520)),
        onBrush: (x0, x1) => {
          state.xlim = [x0, x1];
          syncInputs();
          draw();
        },
      }),
    );
    drawStats();
  }

  // 系列ごとの値の表。行を押すと、その系列の表示 / 非表示が切り替わる。
  function drawStats() {
    clear(statsHost);
    const range = chartRange(visibleSeries().map((s) => s.points), { xlim: state.xlim, ylim: state.ylim, logY: state.logY });
    const xlim = range ? [range.xMin, range.xMax] : null;
    const body = el('tbody');
    // 同じ表示名の run がありうるので、表示の切り替えは系列の番号で持つ。
    built.series.forEach((s, i) => {
      const hidden = state.hidden.has(i);
      const st = seriesStats(s.points, xlim);
      body.append(
        el('tr', {
          class: hidden ? 'off' : '',
          title: hidden ? '押すと表示' : '押すと隠す',
          onclick: () => {
            if (hidden) state.hidden.delete(i);
            else state.hidden.add(i);
            draw();
          },
        }, [
          el('td', {}, [el('span', { class: 'swatch', style: `background:${s.color}` })]),
          el('td', { class: 'focus-series-name', text: s.label }),
          el('td', { class: 'num', text: fmtNumber(st.last, 6) }),
          el('td', { class: 'num', text: fmtNumber(st.min, 6) }),
          el('td', { class: 'num', text: fmtNumber(st.max, 6) }),
          el('td', { class: 'num muted', text: String(st.n) }),
        ]),
      );
    });
    statsHost.append(
      el('table', { class: 'focus-table' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th'), el('th', { text: '系列' }),
            el('th', { class: 'num', text: 'last' }), el('th', { class: 'num', text: 'min' }),
            el('th', { class: 'num', text: 'max' }), el('th', { class: 'num', text: '点数' }),
          ]),
        ]),
        body,
      ]),
      el('p', { class: 'muted focus-hint', text: '値は表示中の x 範囲のもの。グラフを横にドラッグすると x を拡大する。' }),
    );
  }

  const close = () => {
    box.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
  };
  const dialog = el('div', { class: 'focus-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title }, [
    el('div', { class: 'focus-head' }, [
      icon('chart', { size: 15 }),
      el('h2', { class: 'focus-title', text: title }),
      el('span', { class: 'spacer' }),
      el('button', { class: 'icon-btn', title: '閉じる (Esc)', 'aria-label': '閉じる', onclick: close }, [icon('x', { size: 18 })]),
    ]),
    controls,
    chartHost,
    statsHost,
  ]);
  // 背景を押したら閉じる。ダイアログの中の操作は閉じない。
  const box = el('div', {
    class: 'focus-backdrop',
    onclick: (e) => {
      if (e.target === box) close();
    },
  }, [dialog]);

  document.body.append(box);
  document.addEventListener('keydown', onKey);
  draw();
  return { close };
}

// 依存なしの SVG 折れ線チャート。描き直しは要素まるごと差し替えで行う。

import { el, fmtNumber } from './util.js';

const NS = 'http://www.w3.org/2000/svg';
const W = 720;
const H = 260;
const PAD = { top: 12, right: 12, bottom: 30, left: 56 };

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    node.setAttribute(k, String(v));
  }
  return node;
}

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) return [min];
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) {
    ticks.push(Number(t.toPrecision(12)));
  }
  return ticks.length ? ticks : [min, max];
}

/**
 * @param {object} opts
 * @param {string} opts.title    見出し（メトリクスキー）
 * @param {Array}  opts.series   [{label, color, points: [[x, y], ...]}]
 * @param {string} opts.xLabel   x 軸ラベル
 * @param {boolean} opts.logY    y 軸を log10 で描く
 * @param {boolean} opts.legend  凡例を出す（単系列なら不要）
 */
export function lineChart({ title, series, xLabel = 'step', logY = false, legend = true }) {
  const usable = series.filter((s) => s.points && s.points.length);
  const wrap = el('figure', { class: 'chart' });
  if (title) wrap.append(el('figcaption', { class: 'chart-title', text: title }));
  if (!usable.length) {
    wrap.append(el('p', { class: 'muted', text: 'データがありません' }));
    return wrap;
  }

  const tf = logY ? (y) => (y > 0 ? Math.log10(y) : NaN) : (y) => y;
  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const s of usable) {
    for (const [x, y] of s.points) {
      const ty = tf(y);
      if (!Number.isFinite(x) || !Number.isFinite(ty)) continue;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (ty < yMin) yMin = ty;
      if (ty > yMax) yMax = ty;
    }
  }
  if (!Number.isFinite(xMin)) {
    wrap.append(el('p', { class: 'muted', text: '描画できる点がありません' }));
    return wrap;
  }
  if (xMin === xMax) {
    xMin -= 0.5;
    xMax += 0.5;
  }
  if (yMin === yMax) {
    yMin -= Math.abs(yMin) * 0.05 + 0.5;
    yMax += Math.abs(yMax) * 0.05 + 0.5;
  } else {
    const pad = (yMax - yMin) * 0.06;
    yMin -= pad;
    yMax += pad;
  }

  const px = (x) => PAD.left + ((x - xMin) / (xMax - xMin)) * (W - PAD.left - PAD.right);
  const py = (ty) => H - PAD.bottom - ((ty - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);

  const svg = svgEl('svg', {
    viewBox: `0 0 ${W} ${H}`,
    class: 'chart-svg',
    role: 'img',
  });

  // 軸とグリッド
  for (const t of niceTicks(yMin, yMax)) {
    const y = py(t);
    svg.append(svgEl('line', { x1: PAD.left, x2: W - PAD.right, y1: y, y2: y, class: 'grid' }));
    const label = svgEl('text', { x: PAD.left - 6, y: y + 4, class: 'tick tick-y' });
    label.textContent = fmtNumber(logY ? 10 ** t : t, 3);
    svg.append(label);
  }
  for (const t of niceTicks(xMin, xMax)) {
    const x = px(t);
    svg.append(svgEl('line', { x1: x, x2: x, y1: PAD.top, y2: H - PAD.bottom, class: 'grid' }));
    const label = svgEl('text', { x, y: H - PAD.bottom + 16, class: 'tick tick-x' });
    label.textContent = xLabel === 'time' ? `${fmtNumber(t, 3)}s` : fmtNumber(t, 4);
    svg.append(label);
  }
  svg.append(
    svgEl('line', {
      x1: PAD.left, x2: W - PAD.right, y1: H - PAD.bottom, y2: H - PAD.bottom, class: 'axis',
    }),
  );
  svg.append(
    svgEl('line', { x1: PAD.left, x2: PAD.left, y1: PAD.top, y2: H - PAD.bottom, class: 'axis' }),
  );

  // 折れ線
  for (const s of usable) {
    let d = '';
    let pen = false;
    for (const [x, y] of s.points) {
      const ty = tf(y);
      if (!Number.isFinite(x) || !Number.isFinite(ty)) {
        pen = false;
        continue;
      }
      d += `${pen ? 'L' : 'M'}${px(x).toFixed(2)} ${py(ty).toFixed(2)}`;
      pen = true;
    }
    svg.append(svgEl('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 1.6, class: 'series' }));
    if (s.points.length === 1) {
      const [x, y] = s.points[0];
      svg.append(svgEl('circle', { cx: px(x), cy: py(tf(y)), r: 3, fill: s.color }));
    }
  }

  // ホバー: 最近傍の x を各系列から拾って表示する
  const cursor = svgEl('line', { y1: PAD.top, y2: H - PAD.bottom, class: 'cursor', opacity: 0 });
  svg.append(cursor);
  const dots = svgEl('g');
  svg.append(dots);
  const tip = el('div', { class: 'chart-tip', hidden: true });

  const toValue = (evt) => {
    const rect = svg.getBoundingClientRect();
    const vx = ((evt.clientX - rect.left) / rect.width) * W;
    if (vx < PAD.left || vx > W - PAD.right) return null;
    return xMin + ((vx - PAD.left) / (W - PAD.left - PAD.right)) * (xMax - xMin);
  };

  svg.addEventListener('mousemove', (evt) => {
    const xv = toValue(evt);
    if (xv === null) return;
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    const lines = [];
    let cursorX = null;
    for (const s of usable) {
      let best = null;
      for (const [x, y] of s.points) {
        if (!Number.isFinite(tf(y))) continue;
        if (best === null || Math.abs(x - xv) < Math.abs(best[0] - xv)) best = [x, y];
      }
      if (!best) continue;
      cursorX = cursorX === null ? best[0] : cursorX;
      dots.append(svgEl('circle', { cx: px(best[0]), cy: py(tf(best[1])), r: 3, fill: s.color }));
      lines.push({ label: s.label, color: s.color, x: best[0], y: best[1] });
    }
    if (cursorX === null) return;
    cursor.setAttribute('x1', px(cursorX));
    cursor.setAttribute('x2', px(cursorX));
    cursor.setAttribute('opacity', 1);
    tip.innerHTML = '';
    tip.append(el('div', { class: 'tip-x', text: `${xLabel} ${fmtNumber(lines[0].x, 6)}` }));
    for (const l of lines) {
      tip.append(
        el('div', { class: 'tip-row' }, [
          el('span', { class: 'swatch', style: `background:${l.color}` }),
          el('span', { class: 'tip-label', text: l.label }),
          el('span', { class: 'tip-value', text: fmtNumber(l.y, 6) }),
        ]),
      );
    }
    tip.hidden = false;
    const box = wrap.getBoundingClientRect();
    const left = Math.min(evt.clientX - box.left + 12, box.width - 180);
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${evt.clientY - box.top + 12}px`;
  });
  svg.addEventListener('mouseleave', () => {
    cursor.setAttribute('opacity', 0);
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    tip.hidden = true;
  });

  wrap.append(svg, tip);
  if (legend && usable.length > 1) {
    wrap.append(
      el(
        'div',
        { class: 'legend' },
        usable.map((s) =>
          el('span', { class: 'legend-item' }, [
            el('span', { class: 'swatch', style: `background:${s.color}` }),
            el('span', { text: s.label }),
          ]),
        ),
      ),
    );
  }
  return wrap;
}

/** metrics API の 1 系列を [[x, y], ...] に落とす。 */
export function seriesPoints(series, xAxis = 'step') {
  const xs = series[xAxis] || series.step;
  const points = [];
  const t0 = series.time && series.time.length ? series.time[0] : 0;
  for (let i = 0; i < series.value.length; i += 1) {
    let x = xs ? xs[i] : i;
    if (xAxis === 'time') x = series.time[i] === null ? NaN : series.time[i] - t0;
    if (x === null || x === undefined) x = NaN;
    points.push([x, series.value[i]]);
  }
  return points;
}

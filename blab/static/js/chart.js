// 依存なしの SVG 折れ線チャート。描き直しは要素まるごと差し替えで行う。

import { icon } from './icons.js';
import { el, fmtNumber } from './util.js';

const NS = 'http://www.w3.org/2000/svg';
const PAD = { top: 12, right: 12, bottom: 30, left: 56 };
let clipSeq = 0;

// タッチでは離しても値の表示を残すので、そのグラフの外を触ったときに消す。
// グラフは描き直しのたびに作り直されるため、リスナーはグラフごとではなく 1 つだけ持つ。
document.addEventListener('pointerdown', (evt) => {
  for (const chart of document.querySelectorAll('.chart')) {
    if (!chart.contains(evt.target)) chart.dispatchEvent(new Event('chart-hide'));
  }
});

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
 * 描く範囲を決める。xlim / ylim は [min, max] で、片側だけ null（自動）にしてよい。
 * y を自動にしたときは、x の範囲に入っている点だけから決める（x を拡大したら y も追従する）。
 * 戻り値の y は logY なら log10 した値。描ける点が無ければ null。
 *
 * @param {Array} pointLists [[[x, y], ...], ...]
 */
export function chartRange(pointLists, { xlim = null, ylim = null, logY = false } = {}) {
  const tf = logY ? (y) => (y > 0 ? Math.log10(y) : NaN) : (y) => y;
  const fixed = (v) => v !== null && v !== undefined && Number.isFinite(v);
  const xLo = xlim && fixed(xlim[0]) ? xlim[0] : null;
  const xHi = xlim && fixed(xlim[1]) ? xlim[1] : null;
  const inX = (x) => (xLo === null || x >= xLo) && (xHi === null || x <= xHi);

  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const points of pointLists) {
    for (const [x, y] of points) {
      const ty = tf(y);
      if (!Number.isFinite(x) || !Number.isFinite(ty)) continue;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (!inX(x)) continue;
      if (ty < yMin) yMin = ty;
      if (ty > yMax) yMax = ty;
    }
  }
  if (!Number.isFinite(xMin)) return null;
  if (xLo !== null) xMin = xLo;
  if (xHi !== null) xMax = xHi;
  if (xMin >= xMax) {
    const mid = xLo !== null && xHi === null ? xMin : xMax;
    xMin = mid - 0.5;
    xMax = mid + 0.5;
  }

  // ylim は値の単位で受け取る。log では 0 以下を指定できないので、その側は自動に戻す。
  const yLo = ylim && fixed(ylim[0]) && Number.isFinite(tf(ylim[0])) ? tf(ylim[0]) : null;
  const yHi = ylim && fixed(ylim[1]) && Number.isFinite(tf(ylim[1])) ? tf(ylim[1]) : null;
  if (!Number.isFinite(yMin)) {
    // x の範囲に点が無い。y は指定か、0〜1 で枠だけ描く。
    yMin = yLo ?? (yHi !== null ? yHi - 1 : 0);
    yMax = yHi ?? yMin + 1;
  } else if (yMin === yMax) {
    yMin -= Math.abs(yMin) * 0.05 + 0.5;
    yMax += Math.abs(yMax) * 0.05 + 0.5;
  } else {
    const pad = (yMax - yMin) * 0.06;
    yMin -= pad;
    yMax += pad;
  }
  if (yLo !== null) yMin = yLo;
  if (yHi !== null) yMax = yHi;
  if (yMin >= yMax) {
    const mid = yLo !== null && yHi === null ? yMin : yMax;
    yMin = mid - 0.5;
    yMax = mid + 0.5;
  }
  return { xMin, xMax, yMin, yMax };
}

/**
 * @param {object} opts
 * @param {string} opts.title    見出し（メトリクスキー）
 * @param {Array}  opts.series   [{label, color, points: [[x, y], ...]}]
 * @param {string} opts.xLabel   x 軸ラベル
 * @param {boolean} opts.logY    y 軸を log10 で描く
 * @param {boolean} opts.legend  凡例を出す（単系列なら不要）
 * @param {Array}  opts.xlim     [min, max]。null の側は自動
 * @param {Array}  opts.ylim     [min, max]（値の単位）。null の側は自動
 * @param {number} opts.width    viewBox の幅
 * @param {number} opts.height   viewBox の高さ
 * @param {Function} opts.onExpand  あれば見出しに拡大ボタンを出し、押されたら呼ぶ
 * @param {Function} opts.onBrush   あればマウスの横ドラッグで範囲を選べる。(x0, x1) で呼ぶ
 */
export function lineChart({
  title, series, xLabel = 'step', logY = false, legend = true,
  xlim = null, ylim = null, width = 720, height = 260, onExpand = null, onBrush = null,
}) {
  const W = width;
  const H = height;
  const usable = series.filter((s) => s.points && s.points.length);
  const wrap = el('figure', { class: 'chart' });
  if (title || onExpand) {
    const caption = el('figcaption', { class: 'chart-title' }, [el('span', { class: 'chart-name', text: title || '' })]);
    if (onExpand) {
      caption.append(
        el('button', { class: 'icon-btn chart-expand', title: '拡大して詳しく見る', 'aria-label': '拡大', onclick: onExpand }, [
          icon('zoom', { size: 14 }),
        ]),
      );
    }
    wrap.append(caption);
  }
  if (!usable.length) {
    wrap.append(el('p', { class: 'muted', text: 'データがありません' }));
    return wrap;
  }

  const tf = logY ? (y) => (y > 0 ? Math.log10(y) : NaN) : (y) => y;
  const range = chartRange(usable.map((s) => s.points), { xlim, ylim, logY });
  if (!range) {
    wrap.append(el('p', { class: 'muted', text: '描画できる点がありません' }));
    return wrap;
  }
  const { xMin, xMax, yMin, yMax } = range;

  const px = (x) => PAD.left + ((x - xMin) / (xMax - xMin)) * (W - PAD.left - PAD.right);
  const py = (ty) => H - PAD.bottom - ((ty - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);

  const svg = svgEl('svg', {
    viewBox: `0 0 ${W} ${H}`,
    class: 'chart-svg',
    role: 'img',
  });

  // 軸とグリッド。log で 1 桁以上にまたがるときは 10 のべき乗に目盛りを打つ。
  const decades = logY ? niceTicks(yMin, yMax).filter((t) => Number.isInteger(t)) : [];
  for (const t of decades.length >= 2 ? decades : niceTicks(yMin, yMax)) {
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

  // 範囲外にはみ出す線は描画域で切る。
  const clipId = `chart-clip-${(clipSeq += 1)}`;
  const defs = svgEl('defs');
  const clip = svgEl('clipPath', { id: clipId });
  clip.append(svgEl('rect', {
    x: PAD.left, y: PAD.top, width: W - PAD.left - PAD.right, height: H - PAD.top - PAD.bottom,
  }));
  defs.append(clip);
  svg.append(defs);
  const plot = svgEl('g', { 'clip-path': `url(#${clipId})` });
  svg.append(plot);

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
    plot.append(svgEl('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 1.6, class: 'series' }));
    if (s.points.length === 1) {
      const [x, y] = s.points[0];
      plot.append(svgEl('circle', { cx: px(x), cy: py(tf(y)), r: 3, fill: s.color }));
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

  // pointer イベントにして、スマホでもタップ / 横なぞりで値を読めるようにする。
  // 縦方向のスワイプはページのスクロールに残す（CSS の touch-action: pan-y）。
  const onMove = (evt) => {
    const xv = toValue(evt);
    if (xv === null) return;
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    const lines = [];
    let cursorX = null;
    for (const s of usable) {
      let best = null;
      for (const [x, y] of s.points) {
        if (!Number.isFinite(tf(y)) || x < xMin || x > xMax) continue;
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
  };
  svg.addEventListener('pointermove', onMove);
  svg.addEventListener('pointerdown', onMove);

  // マウスの横ドラッグで x の範囲を選ぶ。タッチは横なぞりを値の読み取りに使っているので対象外。
  if (onBrush) {
    const band = svgEl('rect', { y: PAD.top, height: H - PAD.top - PAD.bottom, class: 'brush', opacity: 0 });
    svg.append(band);
    let start = null;
    const bandTo = (xv) => {
      const a = px(Math.min(start, xv));
      band.setAttribute('x', a);
      band.setAttribute('width', Math.max(0, px(Math.max(start, xv)) - a));
      band.setAttribute('opacity', 1);
    };
    svg.addEventListener('pointerdown', (evt) => {
      if (evt.pointerType !== 'mouse' || evt.button !== 0) return;
      start = toValue(evt);
      if (start === null) return;
      svg.setPointerCapture(evt.pointerId);
      evt.preventDefault();
    });
    svg.addEventListener('pointermove', (evt) => {
      if (start === null) return;
      const xv = toValue(evt);
      if (xv !== null) bandTo(xv);
    });
    svg.addEventListener('pointerup', (evt) => {
      if (start === null) return;
      const rect = svg.getBoundingClientRect();
      const vx = Math.min(Math.max(((evt.clientX - rect.left) / rect.width) * W, PAD.left), W - PAD.right);
      const end = xMin + ((vx - PAD.left) / (W - PAD.left - PAD.right)) * (xMax - xMin);
      const from = start;
      start = null;
      band.setAttribute('opacity', 0);
      // 数 px 程度のずれはクリックとみなす。
      if (Math.abs(px(end) - px(from)) < 6) return;
      onBrush(Math.min(from, end), Math.max(from, end));
    });
  }
  const hide = () => {
    cursor.setAttribute('opacity', 0);
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    tip.hidden = true;
  };
  // タッチでは指を離した瞬間に pointerleave が来る。そこで消すと値が読めないので、
  // マウス以外では残しておき、グラフの外を触ったとき（chart-hide）に消す。
  svg.addEventListener('pointerleave', (evt) => {
    if (evt.pointerType === 'mouse') hide();
  });
  wrap.addEventListener('chart-hide', hide);

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

/**
 * x 軸の選択（'auto' | 'epoch' | 'step' | 'time'）を、描く系列に合わせて実際の軸に直す。
 *
 * 'auto' は、全系列で epoch がそろっていて（null なし）重複も無いときだけ epoch にし、
 * それ以外は step にする。1 epoch の中で何度も log すると、epoch 軸では同じ x に
 * 点が縦一列に重なって「全部 epoch 0」に見えるため。
 */
export function resolveXAxis(choice, seriesList) {
  if (choice !== 'auto') return choice;
  const epochUsable = seriesList.length > 0 && seriesList.every((s) => {
    const epochs = s.epoch || [];
    if (epochs.length !== s.value.length || epochs.some((e) => e === null || e === undefined)) return false;
    return new Set(epochs).size === epochs.length;
  });
  return epochUsable ? 'epoch' : 'step';
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

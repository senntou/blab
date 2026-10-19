// 画像を画面の上に浮かべて、拡大縮小・移動しながら見るビューア。
// Markdown の画像とアーティファクトの画像で共有する。
//
// - ホイールでカーソル位置を中心に拡大縮小、ドラッグで移動（タッチは 1 本で移動、2 本でピンチ）
// - 移動は画面の端で止めない。画像を画面の外まで追い出せる
// - 指を離したら止まる（慣性は付けない）
// - 「全体表示」で画像全体が収まる倍率にして中央に戻す（ダブルクリック / 0 キーでも）

import { icon } from './icons.js';
import { el } from './util.js';

// 倍率は原寸に対する比。上限はほぼ設けない（画素を 1 つずつ見られるくらいまで）。
export const MIN_SCALE = 1e-3;
export const MAX_SCALE = 1000;
// 原寸の何倍から画素をぼかさず四角のまま見せるか。
const PIXELATED_FROM = 4;
// 全体表示のときに画面の縁に残す余白（px）。
const FIT_MARGIN = 24;
// 全体表示で、下に浮かべた操作バーに画像が掛からないよう空けておく高さ（px）。
const BAR_SPACE = 52;

export function clampScale(s) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

/**
 * 画像全体が画面に収まる倍率と、中央に置くための位置。
 * view は {scale, x, y}。x, y は画面上での画像の左上。
 */
export function fitView(imgW, imgH, viewW, viewH, { margin = FIT_MARGIN, upscale = true } = {}) {
  if (!(imgW > 0 && imgH > 0)) return { scale: 1, x: 0, y: 0 };
  const availW = Math.max(viewW - 2 * margin, 1);
  const availH = Math.max(viewH - 2 * margin, 1);
  let scale = clampScale(Math.min(availW / imgW, availH / imgH));
  if (!upscale) scale = Math.min(scale, 1);
  return { scale, x: (viewW - imgW * scale) / 2, y: (viewH - imgH * scale) / 2 };
}

/** 画面上の点 (cx, cy) を動かさずに、倍率を factor 倍する。 */
export function zoomAt(view, factor, cx, cy) {
  const scale = clampScale(view.scale * factor);
  const k = scale / view.scale;
  return { scale, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k };
}

/** ホイールの移動量を倍率に。トラックパッドのピンチ（ctrlKey 付き）は細かく来るので強めに効かせる。 */
export function wheelFactor(e) {
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
  const k = e.ctrlKey ? 0.01 : 0.0015;
  return Math.exp(-e.deltaY * unit * k);
}

function fmtScale(s) {
  const pct = s * 100;
  if (pct >= 1000) return `${Math.round(pct / 100) / 10}k%`.replace('.0k', 'k');
  if (pct >= 10) return `${Math.round(pct)}%`;
  return `${Number(pct.toPrecision(2))}%`;
}

let current = null;

/**
 * 画像ビューアを開く。開いているものがあれば閉じて差し替える。
 * @param {string} url
 * @param {string} [caption]
 * @returns {{close: Function}}
 */
export function openImageView(url, caption = '') {
  if (current) current.close();

  let view = { scale: 1, x: 0, y: 0 };
  let natural = null; // {w, h}。読み込めるまでは null
  const pointers = new Map(); // pointerId -> {x, y}
  let gesture = null; // 押してからの動き（クリックとドラッグの区別、ピンチの基準）

  const img = el('img', { class: 'imgview-img', src: url, alt: caption, draggable: 'false' });
  const status = el('span', { class: 'imgview-status' });
  const zoomLabel = el('span', { class: 'imgview-zoom', title: '原寸に対する倍率' });
  const stage = el('div', { class: 'imgview-stage' }, [img, status]);

  const apply = () => {
    img.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
    img.classList.toggle('pixelated', view.scale >= PIXELATED_FROM);
    zoomLabel.textContent = natural ? fmtScale(view.scale) : '';
  };
  const size = () => ({ w: stage.clientWidth || window.innerWidth, h: stage.clientHeight || window.innerHeight });
  const fit = ({ upscale = true } = {}) => {
    if (!natural) return;
    const { w, h } = size();
    view = fitView(natural.w, natural.h, w, h - BAR_SPACE, { upscale });
    apply();
  };
  const zoomCenter = (factor) => {
    const { w, h } = size();
    view = zoomAt(view, factor, w / 2, h / 2);
    apply();
  };
  const actual = () => {
    const { w, h } = size();
    view = zoomAt(view, 1 / view.scale, w / 2, h / 2);
    apply();
  };
  const local = (e) => {
    const r = stage.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  img.addEventListener('load', () => {
    natural = { w: img.naturalWidth, h: img.naturalHeight };
    status.textContent = '';
    stage.classList.add('ready');
    // 開いたときは、小さな画像を引き伸ばさない。
    fit({ upscale: false });
  });
  img.addEventListener('error', () => {
    status.textContent = '画像を読み込めません';
  });
  status.textContent = '読み込み中…';

  stage.addEventListener('wheel', (e) => {
    e.preventDefault();
    const p = local(e);
    view = zoomAt(view, wheelFactor(e), p.x, p.y);
    apply();
  }, { passive: false });

  // ---- ドラッグで移動、2 本指でピンチ。どちらも画面の端で止めない。
  const pinchBase = () => {
    const [a, b] = [...pointers.values()];
    return { cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, dist: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
  };
  stage.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    stage.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, local(e));
    if (pointers.size === 1) {
      const p = local(e);
      gesture = { startX: p.x, startY: p.y, moved: false, onImage: e.target === img, pinch: null };
    } else if (pointers.size === 2 && gesture) {
      gesture.moved = true;
      gesture.pinch = pinchBase();
    }
    stage.classList.add('dragging');
  });
  stage.addEventListener('pointermove', (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev || !gesture) return;
    const p = local(e);
    if (pointers.size >= 2 && gesture.pinch) {
      pointers.set(e.pointerId, p);
      const next = pinchBase();
      const base = gesture.pinch;
      // 2 本の中点の移動で平行移動し、指の間の距離の比で拡大縮小する。
      view = { ...view, x: view.x + next.cx - base.cx, y: view.y + next.cy - base.cy };
      view = zoomAt(view, next.dist / base.dist, next.cx, next.cy);
      gesture.pinch = next;
    } else {
      view = { ...view, x: view.x + p.x - prev.x, y: view.y + p.y - prev.y };
      pointers.set(e.pointerId, p);
      if (Math.hypot(p.x - gesture.startX, p.y - gesture.startY) > 4) gesture.moved = true;
    }
    apply();
  });
  const release = (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    if (pointers.size === 1 && gesture) {
      // ピンチから 1 本に戻ったら、残った指でそのまま移動を続ける。
      gesture.pinch = null;
      return;
    }
    if (pointers.size) return;
    stage.classList.remove('dragging');
    // 動かさずに画像の外を押したら閉じる。
    if (e.type === 'pointerup' && gesture && !gesture.moved && !gesture.onImage) close();
    gesture = null;
  };
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', release);
  stage.addEventListener('dblclick', (e) => {
    e.preventDefault();
    fit();
  });

  // 画面の大きさが変わったら、画面の中央にあった点を中央に保つ。
  let last = null;
  const onResize = () => {
    const now = size();
    if (last) {
      view = { ...view, x: view.x + (now.w - last.w) / 2, y: view.y + (now.h - last.h) / 2 };
      apply();
    }
    last = now;
  };

  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Escape') close();
    else if (e.key === '0' || e.key === 'f') fit();
    else if (e.key === '1') actual();
    else if (e.key === '+' || e.key === '=') zoomCenter(1.25);
    else if (e.key === '-') zoomCenter(0.8);
    else return;
    e.preventDefault();
  };

  const toolButton = (name, title, onclick, text = null) =>
    el('button', { class: 'icon-btn', title, 'aria-label': title, onclick }, [icon(name, { size: 16 }), text]);

  const bar = el('div', { class: 'imgview-bar' }, [
    caption ? el('span', { class: 'imgview-caption', text: caption, title: caption }) : null,
    toolButton('zoom-out', '縮小 (-)', () => zoomCenter(0.8)),
    zoomLabel,
    toolButton('zoom-in', '拡大 (+)', () => zoomCenter(1.25)),
    el('button', { class: 'icon-btn imgview-actual', title: '原寸 (1)', onclick: actual, text: '1:1' }),
    toolButton('zoom', '全体表示 (0 / ダブルクリック)', () => fit(), '全体'),
    el('a', { class: 'icon-btn', href: url, target: '_blank', rel: 'noopener', title: '別タブで開く', 'aria-label': '別タブで開く' }, [icon('external', { size: 16 })]),
    toolButton('x', '閉じる (Esc)', () => close()),
  ]);

  const box = el('div', { class: 'imgview', role: 'dialog', 'aria-label': caption || '画像' }, [stage, bar]);

  function close() {
    box.remove();
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', onResize);
    if (current && current.box === box) current = null;
  }

  document.body.append(box);
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);
  last = size();
  apply();
  current = { box, close };
  return { close };
}

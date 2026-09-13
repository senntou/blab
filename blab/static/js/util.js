// DOM と整形のごく薄いヘルパ。フレームワークは入れない。

import { icon, statusDot } from './icons.js';
import { getPref, setPref } from './prefs.js';

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function fmtNumber(v, digits = 4) {
  if (v === null || v === undefined || v === '') return '';
  if (typeof v !== 'number') return String(v);
  if (!Number.isFinite(v)) return String(v);
  if (Number.isInteger(v) && Math.abs(v) < 1e6) return String(v);
  const abs = Math.abs(v);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return v.toExponential(Math.max(1, digits - 2));
  return Number(v.toPrecision(digits)).toString();
}

// 所要時間は単位を必ず付ける（"12" が分なのか秒なのか迷わないように）。
export function fmtDuration(sec) {
  if (typeof sec !== 'number' || !Number.isFinite(sec) || sec < 0) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  return r ? `${m}m ${String(r).padStart(2, '0')}s` : `${m}m`;
}

/** ソートやフィルタ用に「分」へ揃えた数値（表示は fmtDuration）。 */
export function durationMinutes(sec) {
  if (typeof sec !== 'number' || !Number.isFinite(sec)) return null;
  return sec / 60;
}

// created_at は ISO(オフセット付き)。表示は閲覧マシンのローカル時刻に落とす。
export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtRelative(epochSec) {
  if (!epochSec) return '—';
  const diff = Date.now() / 1000 - epochSec;
  if (diff < 60) return 'たった今';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 時間前`;
  return `${Math.floor(diff / 86400)} 日前`;
}

export function fmtBytes(n) {
  if (typeof n !== 'number') return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${i === 0 ? n : n.toFixed(1)} ${units[i]}`;
}

export function statusBadge(row) {
  const status = row.stale ? 'stale' : row.status || 'unknown';
  return el('span', { class: `badge badge-${status}` }, [statusDot(), el('span', { text: status })]);
}

/** クリップボードへコピーするボタン。成功したら一瞬チェックに変わる。 */
export function copyButton(getText, { label = '', title = 'コピー', size = 14 } = {}) {
  const btn = el('button', {
    class: `icon-btn${label ? ' with-label' : ''}`,
    type: 'button',
    title,
    'aria-label': title,
  }, [icon('copy', { size }), label ? el('span', { text: label }) : null]);
  btn.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const text = await (typeof getText === 'function' ? getText() : getText);
    const ok = await copyText(text);
    clear(btn);
    btn.classList.toggle('copied', ok);
    btn.append(icon(ok ? 'check' : 'alert', { size }), label ? el('span', { text: ok ? 'コピーしました' : '失敗' }) : null);
    setTimeout(() => {
      clear(btn);
      btn.classList.remove('copied');
      btn.append(icon('copy', { size }), label ? el('span', { text: label }) : null);
    }, 1400);
  });
  return btn;
}

export async function copyText(text) {
  const value = String(text ?? '');
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (e) {
    // クリップボード API が使えない環境（非セキュアコンテキストなど）の保険。
    try {
      const ta = el('textarea', { style: 'position:fixed;opacity:0' });
      ta.value = value;
      document.body.append(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (e2) {
      return false;
    }
  }
}

/** パスをモノスペースで見せて、右端にコピーボタンを添えた行。 */
export function pathField(label, value, iconName = 'folder') {
  if (!value) return null;
  return el('div', { class: 'path-field', title: value }, [
    icon(iconName, { size: 14, class: 'path-icon' }),
    label ? el('span', { class: 'path-label', text: label }) : null,
    el('code', { class: 'path-value', text: value }),
    copyButton(value, { title: `${label || 'パス'}をコピー` }),
  ]);
}

/**
 * タブ（セグメント）。押した位置は prefs に保存され、次に開いても復元される。
 *
 * @param {Array} items [{id, label, icon, count, render: () => Node}]
 * @param {object} opts {prefKey, active, onChange}
 */
export function tabs(items, { prefKey = null, active = null, onChange = null } = {}) {
  const shown = items.filter(Boolean);
  const stored = prefKey ? getPref(prefKey, null) : null;
  let currentId = [active, stored, shown[0] && shown[0].id].find((id) => shown.some((t) => t.id === id));

  const bar = el('div', { class: 'tabbar', role: 'tablist' });
  const panel = el('div', { class: 'tabpanel' });
  const node = el('div', { class: 'tabs' }, [bar, panel]);

  function select(id, { save = true } = {}) {
    currentId = id;
    if (save && prefKey) setPref(prefKey, id);
    for (const b of bar.children) b.classList.toggle('on', b.dataset.tab === id);
    for (const b of bar.children) b.setAttribute('aria-selected', b.dataset.tab === id ? 'true' : 'false');
    const item = shown.find((t) => t.id === id);
    clear(panel);
    if (item) panel.append(item.render());
    if (onChange) onChange(id);
  }

  for (const t of shown) {
    bar.append(
      el('button', {
        class: 'tab',
        role: 'tab',
        type: 'button',
        dataset: { tab: t.id },
        onclick: () => select(t.id),
      }, [
        t.icon ? icon(t.icon, { size: 15 }) : null,
        el('span', { text: t.label }),
        t.count !== undefined && t.count !== null
          ? el('span', { class: 'tab-count', text: String(t.count) })
          : null,
      ]),
    );
  }
  select(currentId, { save: false });
  return { node, bar, select, current: () => currentId };
}

export function basename(path) {
  const parts = String(path || '').split('/');
  return parts[parts.length - 1];
}

// 名前が同じ run が並んだときに色で見分けるための決定的なパレット。
const PALETTE = [
  '#4c8dff', '#f2994a', '#27ae60', '#eb5757', '#9b51e0',
  '#00b8d9', '#f2c94c', '#e2739d', '#6fcf97', '#8492a6',
];

export function colorFor(index) {
  return PALETTE[index % PALETTE.length];
}

export function isNumber(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

export function debounce(fn, ms = 200) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

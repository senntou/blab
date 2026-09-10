// UI 設定。保存先はブラウザではなく、サーバ側の <root>/.blab-ui/prefs.json。
//
// 起動時に一括で読み込み、以降は同期的に読める。書き込みはキー単位で
// デバウンスして PUT する（列のチェックを連打しても 1 回にまとまる）。

const SAVE_DELAY_MS = 400;

let cache = {};
let loaded = false;
let onError = () => {};
const pending = new Map(); // key -> timer

/** 起動時に 1 回だけ呼ぶ。失敗してもメモリ上の設定だけで動く。 */
export async function initPrefs(errorHandler) {
  if (errorHandler) onError = errorHandler;
  try {
    const res = await fetch('/api/prefs', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    cache = data.prefs && typeof data.prefs === 'object' ? data.prefs : {};
  } catch (e) {
    cache = {};
  }
  loaded = true;
  return cache;
}

export function prefsLoaded() {
  return loaded;
}

/** 保存済みの値。無ければ fallback を返す（オブジェクトなら浅くマージする）。 */
export function getPref(key, fallback = null) {
  const v = cache[key];
  if (v === undefined || v === null) return fallback;
  if (fallback && typeof fallback === 'object' && !Array.isArray(fallback) &&
      typeof v === 'object' && !Array.isArray(v)) {
    return { ...fallback, ...v };
  }
  return v;
}

/** 値を保存する。null を渡すとそのキーを消す（＝既定に戻す）。 */
export function setPref(key, value) {
  if (value === null || value === undefined) delete cache[key];
  else cache[key] = value;
  clearTimeout(pending.get(key));
  pending.set(key, setTimeout(() => flushKey(key, value), SAVE_DELAY_MS));
}

async function flushKey(key, value) {
  pending.delete(key);
  try {
    const res = await fetch(`/api/prefs?key=${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value ?? null),
    });
    if (!res.ok) throw new Error(`${res.status}`);
  } catch (e) {
    onError(`設定を保存できませんでした (${key})`);
  }
}

/** ページを閉じる直前に、たまっている保存を投げ切る。 */
export function flushPrefs() {
  for (const [key] of pending) {
    clearTimeout(pending.get(key));
    const body = JSON.stringify(cache[key] ?? null);
    const url = `/api/prefs?key=${encodeURIComponent(key)}`;
    // unload 中は fetch が中断されうるので keepalive を付ける。
    try {
      fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body, keepalive: true });
    } catch (e) {
      /* 保存できなくても表示には影響しない */
    }
  }
  pending.clear();
}

/**
 * 1 つのキーにぶら下がる設定オブジェクトを扱う小さなヘルパ。
 * `const s = prefState('table:mnist', {dense:false}); s.dense = true;` で保存される。
 */
export function prefState(key, defaults) {
  const stored = getPref(key, null);
  const state = { ...defaults, ...(stored && typeof stored === 'object' ? stored : {}) };
  return new Proxy(state, {
    set(target, prop, value) {
      target[prop] = value;
      setPref(key, { ...target });
      return true;
    },
    deleteProperty(target, prop) {
      delete target[prop];
      setPref(key, { ...target });
      return true;
    },
  });
}

// UI 設定（開いていたタブ、列の表示、並び順など）。
//
// **localStorage に置く。** v1 はルート直下の .blab-ui/ に書いていたが、v2 の閲覧層は
// 読み取り専用と決めた（layout.md §9。唯一の例外は run の group 付け替え）。UI の都合の
// 状態のために記録ディレクトリへ書くのは、その約束と引き換えにするほどの価値がない。

const KEY = 'blab.prefs.v2';

let cache = null;

function load() {
  if (cache) return cache;
  try {
    cache = JSON.parse(window.localStorage.getItem(KEY) || '{}') || {};
  } catch (e) {
    cache = {};
  }
  return cache;
}

function save() {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(cache));
  } catch (e) {
    // 容量超過やプライベートモード。設定が残らないだけなので黙って諦める。
  }
}

export function getPref(key, fallback = null) {
  const store = load();
  return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : fallback;
}

export function setPref(key, value) {
  load();
  if (value === null || value === undefined) delete cache[key];
  else cache[key] = value;
  save();
  return cache;
}

export function loadPrefs() {
  load();
}

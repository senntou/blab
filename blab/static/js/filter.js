// 名前での絞り込み。summary / metrics / 列が数百になっても目的のものに辿り着けるように。
//
//     traiacc           → t,r,a,i,a,c,c がこの順に出てくる名前（train/acc など。fzf と同じ）
//     test auc          → 語ごとに判定して AND
//     test auc -train   → さらに "train" を**連続で**含むものは除く
//
// 除外だけは部分文字列にする。サブシーケンスで除外すると、文字が散らばって含まれる
// だけの名前まで大量に消えてしまう。大文字小文字は区別しない。
//
// サブシーケンスは一致がゆるい（`lr` が `train/balanced_accuracy` にも一致する）ので、
// 語があるときは一致度の高い順に並べる（rankByQuery）。

import { icon } from './icons.js';
import { getPref, setPref } from './prefs.js';
import { el, clear } from './util.js';

export const FILTER_HINT = '名前で絞り込み（文字を順に拾う。空白区切りで AND、-語 で除外）';

/** `word` の文字が `text` にこの順で（連続でなくてよい）出てくるか。 */
export function isSubsequence(word, text) {
  let i = 0;
  for (let j = 0; i < word.length && j < text.length; j += 1) {
    if (word[i] === text[j]) i += 1;
  }
  return i === word.length;
}

const MATCH = 1;
const CONSECUTIVE = 6; // 直前の文字も一致していた
const BOUNDARY = 8; // 名前の先頭か、区切り（/ _ - . など）の直後で一致した
const GAP = 1; // 飛ばした 1 文字あたり
const SEPARATORS = new Set(['/', '_', '-', '.', ':', ' ']);

/**
 * `word` を `text` からサブシーケンスとして拾ったときの最良スコア。拾えなければ null。
 *
 * どの位置の文字で拾うかで点が変わるので、DP で最良の拾い方を探す（O(語長 × 名前長)）。
 */
export function fuzzyScore(word, text) {
  const m = word.length;
  const n = text.length;
  if (!m) return 0;
  if (!isSubsequence(word, text)) return null;
  // prev[j]: word の 1 つ前の文字を text[j] で拾ったときの最良スコア。
  let prev = new Array(n).fill(-Infinity);
  for (let i = 0; i < m; i += 1) {
    const cur = new Array(n).fill(-Infinity);
    // 間を飛ばして来る遷移の最良値。飛ばし量の減点は線形なので max(prev[k] + GAP*k) だけ持てばよい。
    let run = -Infinity;
    for (let j = i; j < n; j += 1) {
      if (i > 0 && j >= 2) run = Math.max(run, prev[j - 2] + GAP * (j - 2));
      if (text[j] !== word[i]) continue;
      const bonus = MATCH + (j === 0 || SEPARATORS.has(text[j - 1]) ? BOUNDARY : 0);
      if (i === 0) {
        cur[j] = bonus;
        continue;
      }
      const consecutive = prev[j - 1] + CONSECUTIVE;
      const skipped = run - GAP * (j - 1);
      cur[j] = Math.max(consecutive, skipped) + bonus;
    }
    prev = cur;
  }
  const best = Math.max(...prev);
  return best === -Infinity ? null : best;
}

function parseQuery(query) {
  const include = [];
  const exclude = [];
  for (const word of String(query || '').toLowerCase().split(/\s+/)) {
    if (!word || word === '-') continue;
    if (word.startsWith('-')) exclude.push(word.slice(1));
    else include.push(word);
  }
  return { include, exclude };
}

/**
 * 絞り込み文字列から `(name) => スコア | null` を作る。一致しなければ null。空なら全部 0。
 *
 * `name` に配列（複数の欄）を渡すと、語ごとに「いちばん良く一致する 1 つの欄」で見る。
 * 欄をつなげて判定すると、欄をまたいで文字を拾ってしまうため。
 */
export function nameScorer(query) {
  const { include, exclude } = parseQuery(query);
  return (name) => {
    const fields = [].concat(name).map((v) => String(v ?? '').toLowerCase());
    if (exclude.some((w) => fields.some((f) => f.includes(w)))) return null;
    let total = 0;
    for (const w of include) {
      let best = null;
      for (const f of fields) {
        const s = fuzzyScore(w, f);
        if (s !== null && (best === null || s > best)) best = s;
      }
      if (best === null) return null;
      total += best;
    }
    return total;
  };
}

/** 絞り込み文字列から `(name) => boolean` を作る。並べ替えない場所（run 一覧の行）用。 */
export function nameMatcher(query) {
  const score = nameScorer(query);
  return (name) => score(name) !== null;
}

/** 絞り込み、語があれば一致度の高い順に並べる。同点は元の順を保つ。 */
export function rankByQuery(items, query, nameOf = (item) => item) {
  const score = nameScorer(query);
  const scored = [];
  for (const item of items) {
    const s = score(nameOf(item));
    if (s !== null) scored.push([s, item]);
  }
  if (parseQuery(query).include.length) scored.sort((a, b) => b[0] - a[0]);
  return scored.map(([, item]) => item);
}

// 実行中はビュー全体が 3 秒ごとに作り直される（app.js）。打っている途中の欄が作り直されても
// フォーカスとカーソル位置を引き継げるよう、どの欄（prefKey）に居たかを憶えておく。
let focused = null; // {key, start, end}

/**
 * 絞り込み欄。`prefKey` を渡すと入力を prefs に保存し、作り直されても中身とフォーカスが残る。
 *
 * @returns {HTMLElement} ラベル要素。`.input` で中の input を取れる。
 */
export function filterBox({ prefKey = null, placeholder = FILTER_HINT, className = 'search-box', onInput }) {
  const initial = prefKey ? getPref(prefKey, '') : '';
  const input = el('input', { type: 'search', placeholder, title: placeholder, value: initial });
  input.value = initial;

  const remember = () => {
    if (prefKey) focused = { key: prefKey, start: input.selectionStart, end: input.selectionEnd };
  };
  input.addEventListener('focus', remember);
  input.addEventListener('input', () => {
    if (prefKey) setPref(prefKey, input.value || null);
    remember();
    onInput(input.value);
  });
  input.addEventListener('blur', () => {
    // 作り直しで DOM から外れた場合はフォーカスを憶えたままにする（次の欄が引き継ぐ）。
    setTimeout(() => {
      if (input.isConnected && focused && focused.key === prefKey) focused = null;
    }, 0);
  });

  if (prefKey && focused && focused.key === prefKey && typeof requestAnimationFrame === 'function') {
    const { start, end } = focused;
    requestAnimationFrame(() => {
      if (!input.isConnected) return;
      const active = document.activeElement;
      if (active && active !== document.body) return; // 他の場所に移ったフォーカスは奪わない
      input.focus();
      try {
        input.setSelectionRange(start ?? input.value.length, end ?? input.value.length);
      } catch (e) {
        // type=search でも通常は動く。動かない環境ではカーソル位置を諦める。
      }
    });
  }

  const box = el('label', { class: className }, [
    icon('search', { size: 14, class: 'search-icon' }),
    input,
  ]);
  box.input = input;
  return box;
}

/** `12 / 178 件` のような件数表示。 */
export function countLabel(shown, total) {
  return shown === total ? `${total} 件` : `${shown} / ${total} 件`;
}

/**
 * 1 行 1 名前の表に絞り込み欄を付ける（summary・集計など、行が数百になる表）。
 *
 * @param {object} opts
 *   keys      行の名前（この順に並ぶ）
 *   head      見出し。要素は文字列かノードの配列。null なら thead を出さない
 *   row       (key) => tr
 *   prefKey   絞り込み文字列を憶える prefs のキー
 *   className table の class
 *   caption   table の caption
 */
export function filteredTable({ keys, head = null, row, prefKey = null, className = 'args', caption = null }) {
  const host = el('div', { class: 'filtered' });
  const count = el('span', { class: 'filter-count' });
  const body = el('div', { class: 'filtered-body' });

  function draw(query) {
    const shown = rankByQuery(keys, query);
    count.textContent = countLabel(shown.length, keys.length);
    clear(body);
    if (!shown.length) {
      body.append(el('p', { class: 'empty-note' }, [icon('info', { size: 14 }), el('span', { text: '一致する名前がありません' })]));
      return;
    }
    const table = el('table', { class: className });
    if (caption) table.append(el('caption', { text: caption }));
    if (head) table.append(el('thead', {}, [el('tr', {}, head.map((h) => el('th', {}, h)))]));
    const tbody = el('tbody');
    for (const key of shown) tbody.append(row(key));
    table.append(tbody);
    body.append(table);
  }

  const box = filterBox({ prefKey, onInput: draw });
  host.append(el('div', { class: 'filter-bar' }, [box, count]), body);
  draw(box.input.value);
  return host;
}

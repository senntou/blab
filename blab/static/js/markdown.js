// Markdown の描画（component の README と、ノードに添付したドキュメント）。
//
// 人間や LLM が書いた「この実験は何のためか・結果をどう読んだか」を UI だけで読めるように
// するための部品。GitHub 風の記法の大半と、数式（$...$ / $$...$$ / \(...\) / \[...\]）を描く。
//
// - innerHTML は使わず DOM を直接組む。生の HTML は描画せず、文字として出す（勝手に消さない）
// - リンクは http(s) / mailto / 相対パスだけ。`javascript:` などは文字だけにする
// - 数式は同梱の KaTeX（/vendor/katex/）を、数式を含む文書を開いたときだけ読み込む。
//   読み込めなければ TeX のソースをそのまま見せる
// - `base`（ノードのパス）を渡すと、相対パスの画像・リンクをそのノードのファイルとして解決する

import { encodePath } from './api.js';
// source.js は markdownBlock を再エクスポートしているので循環 import になるが、
// codeBlock を使うのは描画時（モジュール評価の後）なので問題ない。
import { codeBlock } from './source.js';
import { el } from './util.js';

// ------------------------------------------------------------------ 数式

const KATEX_BASE = '/vendor/katex';
let katexLoading = null;

function loadKatex() {
  if (globalThis.katex) return Promise.resolve(globalThis.katex);
  if (!katexLoading) {
    katexLoading = new Promise((resolve, reject) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = `${KATEX_BASE}/katex.min.css`;
      document.head.append(css);
      const script = document.createElement('script');
      script.src = `${KATEX_BASE}/katex.min.js`;
      script.onload = () => resolve(globalThis.katex);
      script.onerror = () => {
        katexLoading = null;
        reject(new Error('KaTeX を読み込めませんでした'));
      };
      document.head.append(script);
    });
  }
  return katexLoading;
}

function typeset(nodes) {
  if (!nodes.length || !document.head) return;
  loadKatex()
    .then((katex) => {
      for (const node of nodes) {
        try {
          katex.render(node.dataset.tex, node, {
            displayMode: node.classList.contains('math-display'),
            throwOnError: false,
            // 日本語を \text{} の外に書いても警告で埋めない。
            strict: 'ignore',
          });
          node.classList.add('math-rendered');
        } catch (e) {
          node.title = e.message;
        }
      }
    })
    .catch(() => {
      // TeX のソースのまま残す（placeholder の textContent がそれ）。
    });
}

function mathNode(tex, display, ctx, block = false) {
  const source = tex.trim();
  const node = el(block ? 'div' : 'span', {
    class: display ? 'math math-display' : 'math math-inline',
    dataset: { tex: source },
    text: display ? `$$${source}$$` : `$${source}$`,
  });
  ctx.math.push(node);
  return node;
}

// ------------------------------------------------------------------ URL

/** 相対パスを `base/` 基準で正規化する（`..` と `.` を畳む）。 */
function joinPath(base, relative) {
  const out = [];
  for (const part of `${base}/${relative}`.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') out.pop();
    else out.push(part);
  }
  return out.join('/');
}

/**
 * リンク・画像の行き先を決める。
 * @returns {{href: string|null, external?: boolean, doc?: string, anchor?: string}}
 */
function resolveUrl(raw, ctx, { image = false } = {}) {
  const url = String(raw || '').trim();
  if (!url) return { href: null };
  if (url.startsWith('#')) return { href: null, anchor: slugify(url.slice(1)) };
  const scheme = url.match(/^([a-z][a-z0-9+.-]*):/i);
  if (scheme) {
    const s = scheme[1].toLowerCase();
    if (s === 'http' || s === 'https' || s === 'mailto') return { href: url, external: true };
    if (image && s === 'data' && /^data:image\/(png|jpe?g|gif|webp);/i.test(url)) return { href: url };
    return { href: null }; // javascript: などは辿らせない
  }
  if (url.startsWith('/') || url.startsWith('//')) return { href: url, external: true };
  if (!ctx.base) return { href: url, external: true };
  const [pathPart, hash] = url.split('#');
  let decoded = pathPart;
  try {
    decoded = decodeURIComponent(pathPart);
  } catch (e) {
    // % の壊れた URL はそのまま使う。
  }
  // 同じノードの別ドキュメントは、新しいタブではなくその場で切り替える。
  if (!image && ctx.onDocLink && /^[^/]+\.md$/i.test(decoded.replace(/^\.\//, ''))) {
    return { href: '#', doc: decoded.replace(/^\.\//, ''), anchor: hash ? slugify(hash) : null };
  }
  return { href: `/files/${encodePath(joinPath(ctx.base, decoded))}`, external: true };
}

export function slugify(text) {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[\s]+/g, '-')
    .replace(/[^\p{L}\p{N}_-]/gu, '');
}

// ------------------------------------------------------------------ インライン

const ESCAPABLE = '\\`*_{}[]()#+-.!|~$<>';

// sticky（y）正規表現で、いまの位置から始まるものだけを試す。上から順に優先。
const INLINE_RULES = [
  { start: '`', re: /(`+)([\s\S]*?[^`])\1(?!`)/y, make: (m) => el('code', { text: m[2].replace(/^ (.*) $/s, '$1') }) },
  { start: '\\', re: /\\\(([\s\S]+?)\\\)/y, make: (m, ctx) => mathNode(m[1], false, ctx) },
  { start: '\\', re: /\\\[([\s\S]+?)\\\]/y, make: (m, ctx) => mathNode(m[1], true, ctx) },
  { start: '\\', re: /\\\n/y, make: () => el('br') },
  { start: '\\', re: /\\(.)/y, test: (m) => ESCAPABLE.includes(m[1]), make: (m) => document.createTextNode(m[1]) },
  { start: '$', re: /\$\$([\s\S]+?)\$\$/y, make: (m, ctx) => mathNode(m[1], true, ctx) },
  // $5 と $10 のような通貨を数式にしない：開きの直後と閉じの直前は空白でなく、閉じの直後は数字でない。
  { start: '$', re: /\$(?=[^\s$])((?:\\.|[^$\\\n])*?[^\s\\$])\$(?!\d)/y, make: (m, ctx) => mathNode(m[1], false, ctx) },
  { start: '!', re: /!\[((?:\\.|[^\]\\])*)\]\(\s*<?([^)\s>]*)>?(?:\s+"([^"]*)")?\s*\)/y, make: (m, ctx) => imageNode(m, ctx) },
  { start: '[', re: /\[((?:\\.|`[^`]*`|[^\]\\`])+)\]\(\s*<?([^)\s>]*)>?(?:\s+"([^"]*)")?\s*\)/y, make: (m, ctx) => linkNode(m[1], m[2], ctx, m[3]) },
  { start: '<', re: /<((?:https?:\/\/|mailto:)[^>\s]+)>/y, make: (m, ctx) => linkNode(null, m[1], ctx) },
  { start: '<', re: /<!--[\s\S]*?-->/y, make: () => null },
  { start: 'h', re: /https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"、。）」]/y, boundary: true, make: (m, ctx) => linkNode(null, m[0], ctx) },
  { start: '*', re: /\*\*(?=\S)([\s\S]*?\S)\*\*/y, make: (m, ctx) => el('strong', {}, [inline(m[1], ctx)]) },
  { start: '_', re: /__(?=\S)([\s\S]*?\S)__(?![\p{L}\p{N}])/uy, boundary: true, make: (m, ctx) => el('strong', {}, [inline(m[1], ctx)]) },
  { start: '~', re: /~~(?=\S)([\s\S]*?\S)~~/y, make: (m, ctx) => el('del', {}, [inline(m[1], ctx)]) },
  { start: '*', re: /\*(?=[^\s*])([\s\S]*?[^\s*\\])\*(?!\*)/y, make: (m, ctx) => el('em', {}, [inline(m[1], ctx)]) },
  { start: '_', re: /_(?=[^\s_])([\s\S]*?[^\s_\\])_(?![\p{L}\p{N}])/uy, boundary: true, make: (m, ctx) => el('em', {}, [inline(m[1], ctx)]) },
  { start: ' ', re: / {2,}\n/y, make: () => el('br') },
];
const STARTERS = new Set(INLINE_RULES.map((r) => r.start));

function linkNode(label, rawUrl, ctx, title) {
  const target = resolveUrl(rawUrl, ctx);
  const children = label === null ? [document.createTextNode(rawUrl)] : [inline(label, ctx)];
  if (target.href === null && !target.anchor) {
    // 辿らせない行き先。文字だけ残す。
    return el('span', { class: 'md-link-disabled', title: rawUrl }, children);
  }
  const attrs = { href: target.href || '#', title: title || null };
  if (target.external) {
    attrs.target = '_blank';
    attrs.rel = 'noreferrer';
  }
  if (target.doc || target.anchor) {
    // ハッシュでルーティングしているので、`#見出し` をそのまま辿らせると画面が変わってしまう。
    attrs.onclick = (e) => {
      e.preventDefault();
      if (target.doc) ctx.onDocLink(target.doc, target.anchor);
      else scrollToAnchor(ctx.root, target.anchor);
    };
  }
  return el('a', attrs, children);
}

function imageNode(m, ctx) {
  const alt = m[1].replace(/\\(.)/g, '$1');
  const target = resolveUrl(m[2], ctx, { image: true });
  if (!target.href) return document.createTextNode(m[0]);
  const img = el('img', { src: target.href, alt, title: m[3] || null, loading: 'lazy' });
  // 画像は大きいことが多いので、押したら原寸を別タブで開ける。
  return el('a', { class: 'md-image', href: target.href, target: '_blank', rel: 'noreferrer' }, [img]);
}

export function scrollToAnchor(root, anchor) {
  if (!root || !anchor) return;
  const stack = [root];
  while (stack.length) {
    const node = stack.shift();
    if (node.dataset && node.dataset.anchor === anchor) {
      if (node.scrollIntoView) node.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (node.children) stack.push(...node.children);
  }
}

/** インライン記法を DOM にする。 */
function inline(text, ctx) {
  const frag = document.createDocumentFragment();
  let buf = '';
  const flush = () => {
    if (buf) frag.append(document.createTextNode(buf));
    buf = '';
  };
  let i = 0;
  const s = String(text);
  while (i < s.length) {
    const ch = s[i];
    let matched = false;
    if (STARTERS.has(ch)) {
      for (const rule of INLINE_RULES) {
        if (rule.start !== ch) continue;
        // `_` 強調と URL は単語の途中では始めない（snake_case のメトリクス名を守る）。
        if (rule.boundary && i > 0 && /[\p{L}\p{N}_]/u.test(s[i - 1])) continue;
        rule.re.lastIndex = i;
        const m = rule.re.exec(s);
        if (!m || (rule.test && !rule.test(m))) continue;
        flush();
        const node = rule.make(m, ctx);
        if (node) frag.append(node);
        i = rule.re.lastIndex;
        matched = true;
        break;
      }
    }
    if (!matched) {
      buf += ch;
      i += 1;
    }
  }
  flush();
  return frag;
}

// ------------------------------------------------------------------ ブロック

const RE = {
  fence: /^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)[^`]*$/,
  heading: /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/,
  hr: /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/,
  listItem: /^(\s*)([-*+]|\d{1,9}[.)])(\s+|$)(.*)$/,
  quote: /^ {0,3}>\s?/,
  mathOpen: /^\s*(\$\$|\\\[)/,
  tableDelim: /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/,
  comment: /^\s*<!--/,
};

function indentOf(line) {
  return line.match(/^ */)[0].length;
}

function startsBlock(line) {
  return RE.fence.test(line) || RE.heading.test(line) || RE.hr.test(line)
    || RE.listItem.test(line) || RE.quote.test(line) || RE.mathOpen.test(line);
}

/** `|` 区切りのセルに分ける。コード中の `|` と `\|` では切らない。 */
function splitRow(row) {
  let text = row.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
  const cells = [];
  let cur = '';
  let inCode = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (c === '\\' && text[i + 1] === '|') {
      cur += '|';
      i += 1;
    } else if (c === '`') {
      inCode = !inCode;
      cur += c;
    } else if (c === '|' && !inCode) {
      cells.push(cur.trim());
      cur = '';
    } else {
      cur += c;
    }
  }
  cells.push(cur.trim());
  return cells;
}

function parseTable(lines, i, ctx) {
  const header = splitRow(lines[i]);
  const align = splitRow(lines[i + 1]).map((c) => {
    const left = c.startsWith(':');
    const right = c.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return left ? 'left' : null;
  });
  i += 2;
  const rows = [];
  while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
    rows.push(splitRow(lines[i]));
    i += 1;
  }
  const cellAttrs = (k) => (align[k] ? { style: `text-align:${align[k]}` } : {});
  const table = el('table', { class: 'grid' }, [
    el('thead', {}, [el('tr', {}, header.map((h, k) => el('th', cellAttrs(k), [inline(h, ctx)])))]),
    el('tbody', {}, rows.map((r) => el('tr', {}, header.map((_, k) => el('td', cellAttrs(k), [inline(r[k] || '', ctx)]))))),
  ]);
  return [el('div', { class: 'md-table-wrap' }, [table]), i];
}

function parseList(lines, i, ctx) {
  const first = lines[i].match(RE.listItem);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const list = el(ordered ? 'ol' : 'ul');
  const startAt = ordered ? parseInt(first[2], 10) : 1;
  if (ordered && startAt !== 1) list.setAttribute('start', String(startAt));

  while (i < lines.length) {
    const m = lines[i].match(RE.listItem);
    if (!m || m[1].length < baseIndent || m[1].length > baseIndent + 1) break;
    if (/\d/.test(m[2]) !== ordered || RE.hr.test(lines[i])) break;
    const contentCol = m[1].length + m[2].length + Math.min(Math.max(m[3].length, 1), 4);
    const body = [m[4]];
    i += 1;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        // 空行の後も、字下げされた行が続けば同じ項目の中身。
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j += 1;
        if (j < lines.length && indentOf(lines[j]) > baseIndent) {
          body.push('');
          i += 1;
          continue;
        }
        break;
      }
      const lm = line.match(RE.listItem);
      if (lm && lm[1].length <= baseIndent + 1) break;
      if (indentOf(line) <= baseIndent && startsBlock(line)) break;
      body.push(line.replace(new RegExp(`^ {0,${contentCol}}`), ''));
      i += 1;
    }
    while (body.length && !body[body.length - 1].trim()) body.pop();

    const li = el('li');
    const task = body[0].match(/^\[([ xX])\]\s+(.*)$/);
    if (task) {
      li.className = 'task';
      li.append(el('input', { type: 'checkbox', disabled: true, checked: task[1] !== ' ' }));
      body[0] = task[2];
    }
    const tight = !body.includes('');
    const inner = el('div');
    parseBlocks(body, ctx, inner);
    for (const child of [...inner.children]) {
      // 詰めて書かれた項目は段落で包まない（行間が開きすぎる）。
      if (tight && child.tagName === 'P') li.append(...(child.childNodes ? [...child.childNodes] : child.children));
      else li.append(child);
    }
    list.append(li);
  }
  return [list, i];
}

/** `lines` を解釈して `into` に積む。引用と箇条書きの中身にも再帰で使う。 */
function parseBlocks(lines, ctx, into) {
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (RE.comment.test(line)) {
      // HTML コメントは書き手のメモ。描画しない。
      while (i < lines.length && !lines[i].includes('-->')) i += 1;
      i += 1;
      continue;
    }

    const fence = line.match(RE.fence);
    if (fence) {
      const marker = fence[1];
      const lang = fence[2].toLowerCase();
      const buf = [];
      i += 1;
      while (i < lines.length && !new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      if (lang === 'math' || lang === 'latex' || lang === 'tex') {
        into.append(mathNode(buf.join('\n'), true, ctx, true));
      } else {
        const language = lang === 'py' || lang === 'python' || (!lang && ctx.defaultLanguage === 'python') ? 'python' : 'text';
        into.append(codeBlock(buf.join('\n'), { language, copy: ctx.copyCode }));
      }
      continue;
    }

    const math = line.match(RE.mathOpen);
    if (math) {
      const close = math[1] === '$$' ? '$$' : '\\]';
      const rest = line.trim().slice(math[1].length);
      if (rest.trimEnd().endsWith(close) && rest.trim().length > close.length - 1) {
        // 1 行で閉じている: $$ x^2 $$
        into.append(mathNode(rest.trimEnd().slice(0, -close.length), true, ctx, true));
        i += 1;
        continue;
      }
      const buf = [rest];
      i += 1;
      while (i < lines.length && !lines[i].trimEnd().endsWith(close)) {
        buf.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) buf.push(lines[i].trimEnd().slice(0, -close.length));
      i += 1;
      into.append(mathNode(buf.join('\n'), true, ctx, true));
      continue;
    }

    const heading = line.match(RE.heading);
    if (heading) {
      // ページの h1 は画面のタイトルなので、文書の # は h2 から始める。
      const level = Math.min(6, heading[1].length + 1);
      const anchor = slugify(heading[2]);
      into.append(el(`h${level}`, { dataset: { anchor } }, [inline(heading[2], ctx)]));
      i += 1;
      continue;
    }

    if (RE.hr.test(line)) {
      into.append(el('hr'));
      i += 1;
      continue;
    }

    if (RE.listItem.test(line)) {
      const [list, next] = parseList(lines, i, ctx);
      into.append(list);
      i = next;
      continue;
    }

    if (RE.quote.test(line)) {
      const buf = [];
      while (i < lines.length && lines[i].trim() && (RE.quote.test(lines[i]) || !startsBlock(lines[i]))) {
        buf.push(lines[i].replace(RE.quote, ''));
        i += 1;
      }
      const quote = el('blockquote');
      // GitHub の > [!NOTE] / > [!WARNING]
      const alert = buf[0] && buf[0].match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i);
      if (alert) {
        quote.className = `md-alert md-alert-${alert[1].toLowerCase()}`;
        quote.append(el('p', { class: 'md-alert-title', text: alert[1].toUpperCase() }));
        buf.shift();
      }
      parseBlocks(buf, ctx, quote);
      into.append(quote);
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && RE.tableDelim.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const [table, next] = parseTable(lines, i, ctx);
      into.append(table);
      i = next;
      continue;
    }

    // 段落: 空行か、別のブロックが始まるまで。
    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])
           && !(lines[i].includes('|') && i + 1 < lines.length && RE.tableDelim.test(lines[i + 1]))) {
      buf.push(lines[i]);
      i += 1;
    }
    into.append(el('p', {}, [inline(buf.map((l) => l.replace(/^\s+/, '')).join('\n'), ctx)]));
  }
}

/**
 * Markdown を描画する。
 *
 * @param {string} text
 * @param {object} [opts]
 * @param {string} [opts.base]  ノードのパス。相対パスの画像・リンクをそのノードのファイルとして解決する
 * @param {(name: string, anchor?: string) => void} [opts.onDocLink]  同じノードの別の .md へのリンク
 * @param {'python'|'text'} [opts.defaultLanguage]  言語指定の無いコードフェンスの扱い
 */
export function markdownBlock(text, { base = null, onDocLink = null, defaultLanguage = 'python' } = {}) {
  const wrap = el('div', { class: 'md' });
  const ctx = { base, onDocLink, defaultLanguage, copyCode: true, math: [], root: wrap };
  let source = String(text ?? '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ');

  // 先頭の YAML front matter は本文ではないので、畳んだ表にせず素のコードで見せる。
  const front = source.match(/^---\n([\s\S]*?)\n---\n/);
  if (front) {
    wrap.append(el('div', { class: 'md-front-matter' }, [codeBlock(front[1], { language: 'text', copy: false })]));
    source = source.slice(front[0].length);
  }

  parseBlocks(source.split('\n'), ctx, wrap);
  typeset(ctx.math);
  return wrap;
}

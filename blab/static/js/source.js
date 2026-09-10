// ソース・diff・README の表示。
//
// blab の中心は「run から構成要素のコードと日本語の説明にワンクリックで辿れる」ことなので、
// ここの読みやすさが機能そのものになる。外部ライブラリは使わず、DOM を直接組む
// （innerHTML を使わないので、記録されたソースが何であっても壊れない）。

import { icon } from './icons.js';
import { copyButton, el } from './util.js';

const PY_TOKENS = new RegExp(
  [
    '(#[^\\n]*)', // 1 コメント
    '("""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'|"(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')', // 2 文字列
    '(@[A-Za-z_][\\w.]*)', // 3 デコレータ
    '\\b(def|class|return|import|from|as|if|elif|else|for|while|in|not|and|or|is|None|True|False|try|except|finally|raise|with|lambda|yield|assert|break|continue|global|nonlocal|pass|del|async|await|self)\\b', // 4 キーワード
    '\\b(\\d+\\.?\\d*(?:[eE][-+]?\\d+)?)\\b', // 5 数値
  ].join('|'),
  'g',
);

const TOKEN_CLASS = ['', 'tok-comment', 'tok-string', 'tok-decorator', 'tok-keyword', 'tok-number'];

/** Python として軽く色付けする。判定できない部分はそのまま出す。 */
function highlight(text) {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of text.matchAll(PY_TOKENS)) {
    if (m.index > last) frag.append(document.createTextNode(text.slice(last, m.index)));
    const group = m.findIndex((v, i) => i > 0 && v !== undefined);
    frag.append(el('span', { class: TOKEN_CLASS[group] || '', text: m[0] }));
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.append(document.createTextNode(text.slice(last)));
  return frag;
}

/**
 * ソースを行番号付きで表示する。
 * @param {string} source
 * @param {{language?: string, maxHeight?: boolean}} opts
 */
export function codeBlock(source, { language = 'python', copy = true } = {}) {
  const text = String(source ?? '');
  const lines = text.split('\n');
  // 末尾の空行は行番号を増やすだけなので落とす。
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  const gutter = el('div', { class: 'code-gutter', text: lines.map((_, i) => i + 1).join('\n') });
  const code = el('code', { class: 'code-text' });
  code.append(language === 'python' ? highlight(lines.join('\n')) : document.createTextNode(lines.join('\n')));
  return el('div', { class: 'code-block' }, [
    copy ? el('div', { class: 'code-actions' }, [copyButton(text, { title: 'ソースをコピー' })]) : null,
    el('pre', { class: 'code-pre' }, [gutter, code]),
  ]);
}

/** unified diff を色分けして表示する。 */
export function diffBlock(diff) {
  const text = String(diff ?? '');
  const body = el('div', { class: 'diff-body' });
  for (const line of text.split('\n')) {
    let cls = 'diff-ctx';
    if (line.startsWith('+++') || line.startsWith('---')) cls = 'diff-file';
    else if (line.startsWith('@@')) cls = 'diff-hunk';
    else if (line.startsWith('+')) cls = 'diff-add';
    else if (line.startsWith('-')) cls = 'diff-del';
    body.append(el('div', { class: `diff-line ${cls}`, text: line || ' ' }));
  }
  return el('div', { class: 'diff-block' }, [
    el('div', { class: 'code-actions' }, [copyButton(text, { title: 'diff をコピー' })]),
    body,
  ]);
}

// ------------------------------------------------------------------ Markdown

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g;

function inlineNodes(text) {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of String(text).matchAll(INLINE)) {
    if (m.index > last) frag.append(document.createTextNode(text.slice(last, m.index)));
    const token = m[0];
    if (token.startsWith('`')) {
      frag.append(el('code', { text: token.slice(1, -1) }));
    } else if (token.startsWith('**')) {
      frag.append(el('strong', { text: token.slice(2, -2) }));
    } else {
      const close = token.indexOf('](');
      frag.append(el('a', {
        href: token.slice(close + 2, -1),
        text: token.slice(1, close),
        target: '_blank',
        rel: 'noreferrer',
      }));
    }
    last = m.index + token.length;
  }
  if (last < text.length) frag.append(document.createTextNode(text.slice(last)));
  return frag;
}

/**
 * README 用の最小 Markdown。見出し / 箇条書き / コードフェンス / 表 / 引用 / 段落。
 * 対応していない記法はそのまま素のテキストとして出す（勝手に消さない）。
 */
export function markdownBlock(text) {
  const wrap = el('div', { class: 'md' });
  const lines = String(text ?? '').split('\n');
  let i = 0;
  let list = null;
  const flushList = () => { list = null; };

  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      flushList();
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i += 1; }
      i += 1;
      wrap.append(codeBlock(buf.join('\n'), { language: fence[1] || 'python', copy: false }));
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushList();
      const tag = `h${Math.min(4, heading[1].length + 1)}`;
      wrap.append(el(tag, {}, [inlineNodes(heading[2])]));
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!list) { list = el('ul'); wrap.append(list); }
      list.append(el('li', {}, [inlineNodes(line.replace(/^\s*[-*]\s+/, ''))]));
      i += 1;
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      if (!list || list.tagName !== 'OL') { list = el('ol'); wrap.append(list); }
      list.append(el('li', {}, [inlineNodes(line.replace(/^\s*\d+\.\s+/, ''))]));
      i += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushList();
      wrap.append(el('blockquote', {}, [inlineNodes(line.replace(/^\s*>\s?/, ''))]));
      i += 1;
      continue;
    }
    if (line.trim().startsWith('|') && lines[i + 1] && /^\s*\|[-:\s|]+\|\s*$/.test(lines[i + 1])) {
      flushList();
      const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const header = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(cells(lines[i])); i += 1; }
      wrap.append(el('table', { class: 'grid' }, [
        el('thead', {}, [el('tr', {}, header.map((h) => el('th', {}, [inlineNodes(h)])))]),
        el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c) => el('td', {}, [inlineNodes(c)]))))),
      ]));
      continue;
    }
    if (!line.trim()) { flushList(); i += 1; continue; }

    // 段落: 空行までを 1 つにまとめる
    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|\s*>)/.test(lines[i])) {
      buf.push(lines[i]);
      i += 1;
    }
    flushList();
    wrap.append(el('p', {}, [inlineNodes(buf.join('\n'))]));
  }
  return wrap;
}

/** ファイル 1 件の見出し行（パスとハッシュ）。 */
export function fileHeading(name, hash) {
  return el('div', { class: 'file-path' }, [
    icon('file', { size: 13 }),
    el('code', { text: name }),
    hash ? el('code', { class: 'hash', text: String(hash).slice(7, 19), title: hash }) : null,
  ]);
}

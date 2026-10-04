// ソース・diff の表示（README などの Markdown は markdown.js）。
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

// Markdown は markdown.js（ノードのドキュメントと共用）。既存の import 先を変えないよう再エクスポートする。
export { markdownBlock } from './markdown.js';

/** ファイル 1 件の見出し行（パスとハッシュ）。 */
export function fileHeading(name, hash) {
  return el('div', { class: 'file-path' }, [
    icon('file', { size: 13 }),
    el('code', { text: name }),
    hash ? el('code', { class: 'hash', text: String(hash).slice(7, 19), title: hash }) : null,
  ]);
}

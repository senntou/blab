// ノード（experiment / group / run）に添付されたドキュメントの表示（layout.md §5.7）。
//
// ノードのディレクトリ直下の *.md を、左に一覧・右に本文で見せる。`meta.json` の notes
// （YAML の comment / `blab note`）もここに一緒に出す。どちらも「この実験は何のためか・
// 結果をどう読んだか」で、LLM が回した実験を UI だけで確認するための入口になる。

import { api, fileUrl } from './api.js';
import { icon } from './icons.js';
import { markdownBlock, scrollToAnchor } from './markdown.js';
import { clear, copyButton, el, fmtBytes, fmtRelative } from './util.js';

function emptyState(path, kind) {
  const target = path || '<node>';
  const lines = [
    `blab doc add ${target} README.md`,
    `echo "# 目的 ..." | blab doc add ${target} -`,
  ];
  return el('div', { class: 'docs-empty' }, [
    el('p', { class: 'muted' }, [
      icon('info', { size: 13 }),
      el('span', { text: `この ${kind} にドキュメントはありません。ディレクトリ直下に *.md を置くと、ここに描画されます（数式も使えます）。` }),
    ]),
    el('pre', { class: 'docs-hint' }, [el('code', {
      text: kind === 'run'
        ? `${lines.join('\n')}\n# 実験 YAML の docs: [intent.md] / component から run.log_doc("report.md", text)`
        : lines.join('\n'),
    })]),
  ]);
}

function notesBox(notes) {
  if (!notes || !String(notes).trim()) return null;
  return el('section', { class: 'docs-notes' }, [
    el('div', { class: 'docs-notes-head' }, [
      icon('note', { size: 13 }),
      el('span', { text: 'コメント' }),
      el('span', { class: 'muted', text: 'meta.json の notes（YAML の comment / blab note）' }),
    ]),
    markdownBlock(notes, { defaultLanguage: 'text' }),
  ]);
}

/**
 * ドキュメントタブの中身。
 *
 * @param {string} path  ノードのパス（ログルートからの相対）
 * @param {Array<{name: string, size: number, mtime: number}>} docs
 * @param {{notes?: string, kind?: string}} opts
 */
export function docsPanel(path, docs, { notes = '', kind = 'run' } = {}) {
  const host = el('div', { class: 'docs-panel' });
  const list = docs || [];
  const noteNode = notesBox(notes);

  if (!list.length) {
    if (noteNode) host.append(noteNode);
    host.append(emptyState(path, kind));
    return host;
  }

  const nav = el('nav', { class: 'docs-nav', 'aria-label': 'ドキュメント' });
  const body = el('article', { class: 'docs-body' });
  let current = null;

  async function show(name, anchor = null) {
    const doc = list.find((d) => d.name === name);
    if (!doc) return;
    current = name;
    for (const item of nav.children) item.classList.toggle('on', item.dataset.name === name);
    clear(body);
    body.append(el('p', { class: 'muted', text: '読み込み中…' }));
    try {
      const res = await api.doc(path, name);
      if (current !== name) return; // 読み込み中に別のものが選ばれた
      clear(body);
      const rendered = markdownBlock(res.text, { base: path, onDocLink: (next, a) => show(next, a), defaultLanguage: 'text' });
      body.append(
        el('div', { class: 'docs-file-head' }, [
          icon('file', { size: 13 }),
          el('code', { text: name }),
          el('span', { class: 'muted', title: new Date(doc.mtime * 1000).toLocaleString(), text: `${fmtBytes(doc.size)} · ${fmtRelative(new Date(doc.mtime * 1000).toISOString())}` }),
          el('span', { class: 'spacer' }),
          copyButton(res.text, { title: 'Markdown のソースをコピー' }),
          el('a', { class: 'icon-btn', href: fileUrl(path, name), target: '_blank', rel: 'noreferrer', title: '生のファイルを開く' }, [icon('external', { size: 14 })]),
        ]),
        rendered,
      );
      if (anchor) scrollToAnchor(rendered, anchor);
    } catch (e) {
      clear(body);
      body.append(el('p', { class: 'muted', text: `読み込めませんでした: ${e.message}` }));
    }
  }

  for (const doc of list) {
    nav.append(el('button', {
      class: 'docs-nav-item',
      type: 'button',
      dataset: { name: doc.name },
      title: doc.name,
      onclick: () => show(doc.name),
    }, [icon('file', { size: 13 }), el('span', { text: doc.name })]));
  }

  if (noteNode) host.append(noteNode);
  // 1 つしか無ければ一覧は出さない（本文の幅を取らない）。
  host.append(el('div', { class: `docs-layout${list.length > 1 ? '' : ' single'}` }, [list.length > 1 ? nav : null, body]));
  show(list[0].name);
  return host;
}

/** 名前の横に付ける「ドキュメントあり」の印。 */
export function docsMark(names) {
  if (!names || !names.length) return null;
  return el('span', { class: 'docs-mark', title: `ドキュメント: ${names.join(', ')}` }, [icon('note', { size: 12 })]);
}

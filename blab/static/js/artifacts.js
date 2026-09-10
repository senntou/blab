// アーティファクトの表示。run 詳細のブラウザと、比較ビューの並置画面で共有する。

import { api, fileUrl } from './api.js';
import { icon } from './icons.js';
import { getPref, setPref } from './prefs.js';
import { el, clear, colorFor, copyButton, fmtBytes, fmtTime } from './util.js';

const TYPE_ICON = { image: 'image', csv: 'table', text: 'file', other: 'file' };

/** root からの絶対パスを組み立てる（root 取得は 1 回だけキャッシュされる）。 */
async function absPath(...parts) {
  const root = await api.root();
  return [root, ...parts].filter(Boolean).join('/');
}

/** 1 件を種別に応じて描く（画像 / CSV / テキスト / その他）。 */
export async function renderArtifact(container, runPath, artifact, { compact = false } = {}) {
  clear(container);
  if (!artifact) {
    container.append(placeholder('file', 'ファイルを選ぶとここに表示されます'));
    return;
  }
  const url = fileUrl(runPath, artifact.path);
  container.append(
    el('div', { class: 'viewer-head' }, [
      icon(TYPE_ICON[artifact.type] || 'file', { size: 15 }),
      el('strong', { class: 'viewer-name', text: artifact.name, title: artifact.path }),
      el('span', { class: 'muted', text: fmtBytes(artifact.size) }),
      artifact.mtime ? el('span', { class: 'muted', text: fmtTime(new Date(artifact.mtime * 1000).toISOString()) }) : null,
      el('span', { class: 'spacer' }),
      copyButton(() => absPath(runPath, artifact.path), { title: 'ファイルのパスをコピー' }),
      el('a', { class: 'icon-btn', href: url, target: '_blank', rel: 'noopener', title: '別タブで開く' }, [icon('external')]),
      el('a', { class: 'icon-btn', href: url, download: artifact.name, title: 'ダウンロード' }, [icon('download')]),
    ]),
  );
  const bodyNode = el('div', { class: `viewer-body${compact ? ' compact' : ''}` });
  container.append(bodyNode);

  if (artifact.type === 'image') {
    const img = el('img', { class: 'artifact-image', src: url, alt: artifact.name, title: 'クリックで拡大' });
    img.addEventListener('click', () => lightbox(url, artifact.name));
    bodyNode.append(img);
    return;
  }
  if (artifact.type === 'text' || artifact.type === 'csv') {
    bodyNode.append(el('p', { class: 'muted', text: '読み込み中…' }));
    try {
      const res = await fetch(url);
      const text = await res.text();
      clear(bodyNode);
      if (artifact.type === 'csv') bodyNode.append(csvTable(text));
      else bodyNode.append(el('pre', { class: 'artifact-text', text: text.slice(0, 200_000) }));
    } catch (e) {
      clear(bodyNode);
      bodyNode.append(el('p', { class: 'error', text: `読み込めません: ${e.message}` }));
    }
    return;
  }
  bodyNode.append(placeholder('file', 'プレビューできない形式です'));
}

function placeholder(iconName, text) {
  return el('div', { class: 'viewer-placeholder' }, [icon(iconName, { size: 26 }), el('p', { text })]);
}

/** 画像を画面いっぱいに拡大するオーバーレイ。 */
export function lightbox(url, caption) {
  const close = () => {
    box.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
  };
  const box = el('div', { class: 'lightbox', onclick: close }, [
    el('div', { class: 'lightbox-bar' }, [
      el('span', { text: caption || '' }),
      el('button', { class: 'icon-btn', title: '閉じる (Esc)' }, [icon('x', { size: 18 })]),
    ]),
    el('img', { src: url, alt: caption || '', onclick: (e) => e.stopPropagation() }),
  ]);
  document.body.append(box);
  document.addEventListener('keydown', onKey);
  return box;
}

/**
 * run 詳細のアーティファクトブラウザ。左にファイル一覧、右にプレビュー。
 *
 * @param {string} runPath
 * @param {Array} files [{name, path, size, type}]
 */
export function artifactBrowser(runPath, files, { baseDir = 'artifacts', emptyText = 'アーティファクトがありません' } = {}) {
  const node = el('div', { class: 'browser' });
  if (!files.length) {
    node.append(el('div', { class: 'empty-state' }, [icon('folder', { size: 26, class: 'empty-icon' }), el('p', { text: emptyText })]));
    return node;
  }

  let query = '';
  let currentPath = (files.find((f) => f.type === 'image') || files[0]).path;
  const listNode = el('div', { class: 'browser-list' });
  const viewer = el('div', { class: 'browser-view' });

  const search = el('input', {
    type: 'search',
    placeholder: 'ファイル名で絞り込み',
    oninput: (e) => {
      query = e.target.value.toLowerCase();
      drawList();
    },
  });

  function drawList() {
    clear(listNode);
    listNode.append(
      el('div', { class: 'browser-head' }, [
        el('span', { class: 'search-box' }, [icon('search', { size: 13, class: 'search-icon' }), search]),
        el('span', { class: 'muted', text: `${files.length} 件` }),
        copyButton(() => absPath(runPath, baseDir), { title: 'ディレクトリのパスをコピー' }),
      ]),
    );
    const shown = files.filter((f) => !query || f.name.toLowerCase().includes(query));
    const ul = el('ul', { class: 'file-list' });
    for (const f of shown) {
      ul.append(
        el('li', {
          class: `file-item${f.path === currentPath ? ' on' : ''}`,
          onclick: () => {
            currentPath = f.path;
            drawList();
            renderArtifact(viewer, runPath, f);
          },
        }, [
          icon(TYPE_ICON[f.type] || 'file', { size: 15, class: `file-icon type-${f.type}` }),
          el('span', { class: 'file-name', text: f.name, title: f.name }),
          el('span', { class: 'file-size', text: fmtBytes(f.size) }),
        ]),
      );
    }
    if (!shown.length) ul.append(el('li', { class: 'muted', text: '一致するファイルがありません' }));
    listNode.append(ul);
  }

  drawList();
  renderArtifact(viewer, runPath, files.find((f) => f.path === currentPath));
  node.append(listNode, viewer);
  return node;
}

/**
 * 比較ビュー用のアーティファクト画面。
 * 左に「run 横断で存在するファイル名」の一覧、右に選んだ 1 件を run ごとに並べる。
 */
export function artifactCompare(details, { prefKey = null } = {}) {
  const names = [];
  for (const d of details) {
    for (const a of d.artifacts || []) if (!names.includes(a.name)) names.push(a.name);
  }
  names.sort();

  const node = el('div', { class: 'browser' });
  if (!names.length) {
    node.append(el('div', { class: 'empty-state' }, [
      icon('image', { size: 26, class: 'empty-icon' }),
      el('p', { text: '比較できるアーティファクトがありません' }),
    ]));
    return node;
  }

  const saved = prefKey ? getPref(prefKey, {}) : {};
  let current = names.includes(saved.name) ? saved.name : names[0];
  let cols = Number(saved.cols) || 0; // 0 = 自動

  const listNode = el('div', { class: 'browser-list' });
  const viewNode = el('div', { class: 'browser-view' });

  function save() {
    if (prefKey) setPref(prefKey, { name: current, cols });
  }

  function drawList() {
    clear(listNode);
    listNode.append(el('div', { class: 'browser-head' }, [
      icon('image', { size: 14 }),
      el('strong', { text: 'ファイル' }),
      el('span', { class: 'muted', text: `${names.length} 件` }),
    ]));
    const ul = el('ul', { class: 'file-list' });
    for (const name of names) {
      const have = details.filter((d) => (d.artifacts || []).some((a) => a.name === name)).length;
      ul.append(
        el('li', {
          class: `file-item${name === current ? ' on' : ''}`,
          onclick: () => {
            current = name;
            save();
            drawList();
            drawView();
          },
        }, [
          icon('image', { size: 15, class: 'file-icon' }),
          el('span', { class: 'file-name', text: name, title: name }),
          el('span', {
            class: `file-size${have < details.length ? ' warn' : ''}`,
            text: `${have}/${details.length}`,
            title: have < details.length ? '一部の run にしかありません' : 'すべての run にあります',
          }),
        ]),
      );
    }
    listNode.append(ul);
  }

  function drawView() {
    clear(viewNode);
    const sizes = [[0, '自動'], [1, '1 列'], [2, '2 列'], [3, '3 列'], [4, '4 列']];
    viewNode.append(
      el('div', { class: 'viewer-head' }, [
        icon('grid', { size: 15 }),
        el('strong', { class: 'viewer-name', text: current }),
        el('span', { class: 'spacer' }),
        el('span', { class: 'segmented' }, sizes.map(([v, label]) =>
          el('button', {
            class: `btn${cols === v ? ' on' : ''}`,
            text: label,
            onclick: () => {
              cols = v;
              save();
              drawView();
            },
          }),
        )),
      ]),
    );
    const grid = el('div', {
      class: 'compare-grid',
      style: cols ? `grid-template-columns: repeat(${cols}, minmax(0, 1fr))` : '',
    });
    details.forEach((d, i) => {
      const a = (d.artifacts || []).find((x) => x.name === current);
      const url = a ? fileUrl(d.path, a.path) : null;
      const media = a && a.type === 'image'
        ? el('img', { src: url, alt: `${d.name} ${current}`, loading: 'lazy', title: 'クリックで拡大' })
        : el('div', { class: 'placeholder' }, [
            icon(a ? (TYPE_ICON[a.type] || 'file') : 'x', { size: 20 }),
            el('span', { text: a ? `${a.type}（プレビュー対象外）` : 'この run にはありません' }),
          ]);
      if (a && a.type === 'image') media.addEventListener('click', () => lightbox(url, `${d.name} / ${current}`));
      grid.append(
        el('figure', { class: 'compare-cell' }, [
          el('figcaption', { class: 'compare-cap' }, [
            el('span', { class: 'swatch', style: `background:${colorFor(i)}` }),
            el('a', { href: `#/run/${d.path}`, text: d.name, title: d.path }),
            el('span', { class: 'spacer' }),
            a ? el('a', { class: 'icon-btn', href: url, target: '_blank', rel: 'noopener', title: '別タブで開く' }, [icon('external', { size: 13 })]) : null,
          ]),
          media,
        ]),
      );
    });
    viewNode.append(grid);
  }

  drawList();
  drawView();
  node.append(listNode, viewNode);
  return node;
}

function csvTable(text, maxRows = 500) {
  const lines = text.trim().split(/\r?\n/);
  const truncated = lines.length > maxRows;
  const rows = lines.slice(0, maxRows).map((l) => l.split(lines[0].includes('\t') ? '\t' : ','));
  const head = rows.shift() || [];
  return el('div', { class: 'csv-wrap' }, [
    el('div', { class: 'table-scroll' }, [
      el('table', { class: 'simple' }, [
        el('thead', {}, [el('tr', {}, head.map((h) => el('th', { text: h })))]),
        el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c) => el('td', { text: c }))))),
      ]),
    ]),
    truncated ? el('p', { class: 'muted', text: `先頭 ${maxRows} 行だけ表示しています（全 ${lines.length} 行）` }) : null,
  ]);
}

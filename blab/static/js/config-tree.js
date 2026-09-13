// 構成ツリー — blab の要（design.md §11）。
//
// resolved.yaml をそのまま木として描き、各ノードから component の詳細へ飛べるようにする。
// 引数は **YAML 由来と実行時由来を色で区別**する。`k: 100` が runtime 色で出ていれば、
// 「それは誰も宣言していないが、実際にそう渡された」と一目で分かる。
//
// 記録できなかったものは、記録できなかったと表示する（$unrecorded を省略しない）。

import { icon } from './icons.js';
import { el, fmtNumber } from './util.js';

const ORIGIN_LABEL = {
  yaml: 'YAML',
  override: '--set',
  runtime: '実行時',
  default: '既定値',
};

/** `sha256:db83d7...` を短く。 */
export function shortHash(hash) {
  const text = String(hash || '');
  return text.startsWith('sha256:') ? text.slice(7, 15) : text.slice(0, 8);
}

/** component の版の呼び名。ラベルがあればそれ、無ければ短いハッシュ。 */
export function versionLabel(node) {
  return node.label ? `@${node.label}` : `@${shortHash(node.hash)}`;
}

function markerChip(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  if ('$ref' in value) {
    return el('span', { class: 'chip chip-ref', title: '.build() が返したオブジェクト' }, [
      icon('link', { size: 12 }),
      el('span', { text: value.$ref }),
    ]);
  }
  if ('$child' in value) {
    return el('span', { class: 'chip chip-child', title: '子 component（下のツリーに出ている）' }, [
      icon('box', { size: 12 }),
      el('span', { text: value.$child }),
    ]);
  }
  if ('$data' in value) {
    return el('span', { class: 'chip chip-data', title: '外部ファイル（data.json に記録）' }, [
      icon('folder', { size: 12 }),
      el('span', { text: value.$data }),
    ]);
  }
  if ('$unrecorded' in value) {
    // 推測しない。記録できなかったことをそのまま出す。
    return el('span', { class: 'chip chip-unrecorded', title: '記録できない値だった' }, [
      icon('alert', { size: 12 }),
      el('span', { text: `記録なし (${value.$unrecorded})` }),
    ]);
  }
  if ('$unavailable' in value) {
    return el('span', { class: 'chip chip-unrecorded', title: '取得できなかった' }, [
      icon('alert', { size: 12 }),
      el('span', { text: value.$unavailable }),
    ]);
  }
  return null;
}

export function renderValue(value) {
  const chip = markerChip(value);
  if (chip) return chip;
  if (value === null || value === undefined) return el('code', { class: 'v-null', text: 'null' });
  if (typeof value === 'boolean') return el('code', { class: 'v-bool', text: String(value) });
  if (typeof value === 'number') return el('code', { class: 'v-num', text: fmtNumber(value) });
  if (typeof value === 'string') return el('code', { class: 'v-str', text: value });
  if (Array.isArray(value)) {
    const wrap = el('span', { class: 'v-list' });
    value.forEach((item, i) => {
      if (i) wrap.append(document.createTextNode(', '));
      wrap.append(renderValue(item));
    });
    return el('span', {}, ['[', wrap, ']']);
  }
  const wrap = el('span', { class: 'v-map' });
  Object.entries(value).forEach(([k, v], i) => {
    if (i) wrap.append(document.createTextNode(', '));
    wrap.append(el('span', { class: 'v-key', text: `${k}: ` }), renderValue(v));
  });
  return el('span', {}, ['{', wrap, '}']);
}

function argsTable(args, argsFrom, { title = null } = {}) {
  const names = Object.keys(args || {}).sort();
  if (!names.length) {
    return el('p', { class: 'muted tree-noargs', text: title ? `${title}: 引数なし` : '引数なし' });
  }
  const table = el('table', { class: 'args' });
  if (title) table.append(el('caption', { text: title }));
  const body = el('tbody');
  for (const name of names) {
    const origin = (argsFrom || {})[name] || null;
    body.append(
      el('tr', { class: origin ? `arg-${origin}` : '' }, [
        el('th', { text: name }),
        el('td', {}, [renderValue(args[name])]),
        el('td', { class: 'arg-origin' }, [
          origin
            ? el('span', {
                class: `origin origin-${origin}`,
                text: ORIGIN_LABEL[origin] || origin,
                title: `この値の出どころ: ${ORIGIN_LABEL[origin] || origin}`,
              })
            : null,
        ]),
      ]),
    );
  }
  table.append(body);
  return table;
}

function nodeHeader(name, node, { onSelect } = {}) {
  const head = el('div', { class: 'tree-head' });
  head.append(
    el('span', { class: 'tree-slot', text: name }),
    el('a', {
      class: 'tree-component',
      href: `#/component/${encodeURIComponent(node.use)}`,
      title: `${node.use} の詳細へ`,
      onclick: (e) => {
        if (onSelect) {
          e.preventDefault();
          onSelect(node);
        }
      },
    }, [
      icon('box', { size: 14 }),
      el('strong', { text: node.use }),
      el('span', { class: 'tree-version', text: versionLabel(node) }),
    ]),
  );
  if (node.entry) head.append(el('span', { class: 'tree-entry', text: node.entry }));
  head.append(el('code', { class: 'tree-hash', text: shortHash(node.hash), title: node.hash }));
  return head;
}

function renderNode(name, node, options) {
  const box = el('section', { class: 'tree-node' });
  box.append(nodeHeader(name, node, options));

  const body = el('div', { class: 'tree-body' });

  if (node.args) {
    // root は誰も .build() しない。blab が 1 度だけ実体化する。
    body.append(argsTable(node.args, node.args_from));
  } else {
    const builds = node.builds || [];
    if (!builds.length) {
      // 宣言されたのに一度も build されなかった。**黙って消さない。**
      body.append(
        el('p', { class: 'tree-unused' }, [
          icon('alert', { size: 14 }),
          el('span', { text: '宣言されましたが、一度も build されませんでした' }),
        ]),
      );
    } else {
      builds.forEach((build, i) => {
        body.append(
          argsTable(build.args, build.args_from, {
            title: builds.length > 1 ? `build #${i}` : null,
          }),
        );
      });
    }
  }

  const children = node.children || {};
  const names = Object.keys(children);
  if (names.length) {
    const kids = el('div', { class: 'tree-children' });
    for (const key of names) kids.append(renderNode(key, children[key], options));
    body.append(kids);
  }

  box.append(body);
  return box;
}

/**
 * resolved.yaml を木として描く。
 *
 * @param {object} resolved  resolved.yaml の中身
 * @param {object} opts      {onSelect} — component ノードを押したときの処理
 */
export function configTree(resolved, opts = {}) {
  const wrap = el('div', { class: 'config-tree' });
  if (!resolved || !resolved.run) {
    wrap.append(el('p', { class: 'muted', text: '構成の記録がありません' }));
    return wrap;
  }
  if (resolved.overrides && Object.keys(resolved.overrides).length) {
    const list = el('div', { class: 'overrides' });
    list.append(el('span', { class: 'overrides-label', text: '--set' }));
    for (const [path, value] of Object.entries(resolved.overrides)) {
      list.append(el('code', { class: 'override', text: `${path}=${JSON.stringify(value)}` }));
    }
    wrap.append(list);
  }
  wrap.append(renderNode('run', resolved.run, opts));
  return wrap;
}

/** 木を平らにして `{path: node}` にする（比較ビュー用）。 */
export function flattenTree(resolved) {
  const out = {};
  const walk = (path, node) => {
    if (!node) return;
    out[path] = node;
    for (const [key, child] of Object.entries(node.children || {})) {
      walk(path ? `${path}.${key}` : key, child);
    }
  };
  walk('run', resolved && resolved.run);
  return out;
}

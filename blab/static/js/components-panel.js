// 構成パネル — run 詳細の要（design.md §6.2-3）。
//
// binding を「role / id@version / args」で並べ、クリックで component 詳細へ飛ぶ。
// 木の骨格は `loaded_by`（呼び出しの包含）で描き、`$ref`（引数として渡された）は
// 横向きの辺として重ねる。この 2 つは別物なので、別の見せ方をする。

import { icon } from './icons.js';
import { el } from './util.js';

/** 引数の値を 1 セルに落とす。参照と未記録は潰さずに見せる。 */
export function argText(v) {
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'object' && !Array.isArray(v)) {
    if ('$ref' in v) return `→ #${v.$ref}`;
    if ('$unrecorded' in v) return `未記録 (${v.$unrecorded})`;
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) return JSON.stringify(v);
  if (typeof v === 'string') return v;
  return String(v);
}

function argChip(name, value) {
  const isRef = value && typeof value === 'object' && '$ref' in value;
  const isUnrecorded = value && typeof value === 'object' && '$unrecorded' in value;
  return el('code', {
    class: `arg${isRef ? ' ref' : ''}${isUnrecorded ? ' unrecorded' : ''}`,
    title: isUnrecorded
      ? `${name}: JSON にできない値だったので記録していません（型: ${value.$unrecorded}）`
      : `${name} = ${argText(value)}`,
  }, [
    el('span', { class: 'arg-key', text: name }),
    el('span', { class: 'arg-eq', text: '=' }),
    el('span', { class: 'arg-val', text: argText(value) }),
  ]);
}

/**
 * 構成パネルを組む。
 * @param {object} components  components.json の内容（bindings / entrypoints）
 * @param {{onCode?: (name: string) => void}} opts
 */
export function componentsPanel(components, { onCode = null } = {}) {
  const bindings = (components && components.bindings) || [];
  const panel = el('section', { class: 'panel' }, [
    el('div', { class: 'panel-head' }, [
      icon('grid', { size: 15 }),
      el('h2', { text: '構成' }),
      el('span', { class: 'muted', text: `${bindings.length} binding` }),
    ]),
  ]);

  if (!bindings.length) {
    panel.append(el('p', { class: 'empty-note' }, [
      el('span', { text: 'この run は component を load していません。' }),
      el('span', { class: 'muted', text: ' blab.load() で引いた構成要素だけが記録されます（run の外の load は記録されません）。' }),
    ]));
    return panel;
  }

  const byIndex = new Map(bindings.map((b) => [b.index, b]));
  const children = new Map();
  const roots = [];
  for (const b of bindings) {
    if (b.loaded_by === null || b.loaded_by === undefined || !byIndex.has(b.loaded_by)) roots.push(b);
    else children.set(b.loaded_by, [...(children.get(b.loaded_by) || []), b]);
  }

  const list = el('div', { class: 'binding-tree' });
  const walk = (binding, depth) => {
    list.append(bindingRow(binding, depth, byIndex));
    for (const child of children.get(binding.index) || []) walk(child, depth + 1);
  };
  for (const b of roots) walk(b, 0);
  panel.append(list);

  const entrypoints = (components && components.entrypoints) || [];
  if (entrypoints.length) panel.append(entrypointList(entrypoints, onCode));
  return panel;
}

function bindingRow(b, depth, byIndex) {
  const version = b.dirty ? 'dirty' : (b.version || '?');
  const args = Object.entries(b.args || {});
  const refs = args.filter(([, v]) => v && typeof v === 'object' && '$ref' in v);

  return el('div', { class: `binding${b.failed ? ' failed' : ''}`, style: `--depth:${depth}` }, [
    el('div', { class: 'binding-main' }, [
      el('span', { class: 'binding-index', text: `#${b.index}`, title: '$ref / loaded_by が指す添字' }),
      depth > 0
        ? el('span', {
            class: 'binding-nest',
            title: `#${b.loaded_by} の実体化中に load された`,
            text: '└',
          })
        : null,
      b.role
        ? el('span', { class: 'binding-role', text: b.role })
        : el('span', { class: 'binding-role muted', text: '(role なし)' }),
      el('a', { class: 'binding-id', href: `#/component/${b.id}` }, [
        icon('grid', { size: 13 }),
        el('span', { text: b.id }),
        el('code', { class: `ver${b.dirty ? ' dirty' : ''}`, text: `@${version}` }),
      ]),
      b.dirty
        ? el('span', {
            class: 'badge dirty',
            text: 'dirty',
            title: '登録されていない current/ を実行した。ソースは code/_components/ に残っている',
          })
        : null,
      b.failed
        ? el('span', { class: 'badge bad', text: 'load 失敗', title: 'entry が例外を投げた' })
        : null,
      el('span', { class: 'spacer' }),
      el('code', { class: 'hash', text: String(b.hash || '').slice(7, 19), title: b.hash }),
    ]),
    args.length
      ? el('div', { class: 'binding-args' }, args.map(([k, v]) => argChip(k, v)))
      : null,
    refs.length
      ? el('div', { class: 'binding-refs' }, [
          icon('external', { size: 12 }),
          el('span', {
            class: 'muted',
            text: refs
              .map(([k, v]) => {
                const target = byIndex.get(v.$ref);
                return `${k} ← #${v.$ref} ${target ? target.id : '?'}`;
              })
              .join(' / '),
          }),
        ])
      : null,
  ]);
}

function entrypointList(entrypoints, onCode) {
  const wrap = el('div', { class: 'entrypoints' }, [
    el('div', { class: 'panel-subhead' }, [
      icon('terminal', { size: 14 }),
      el('h3', { text: 'entrypoint' }),
      el('span', { class: 'muted', text: `${entrypoints.length} 本` }),
    ]),
  ]);
  for (const e of entrypoints) {
    const unresolved = e.unresolved_imports || [];
    const skipped = e.skipped || [];
    wrap.append(el('div', { class: 'entrypoint' }, [
      el('div', { class: 'entrypoint-main' }, [
        e.path && onCode
          ? el('button', { class: 'link-btn', onclick: () => onCode(e.path.replace(/^code\//, '')) }, [
              icon('file', { size: 13 }),
              el('span', { text: e.name || '(不明)' }),
            ])
          : el('span', {}, [icon('file', { size: 13 }), el('span', { text: e.name || '(不明)' })]),
        e.n_recorded > 1
          ? el('span', { class: 'chip', text: `${e.n_recorded} 回`, title: `最後の実行: ${e.last_recorded_at}` })
          : null,
        el('span', { class: 'spacer' }),
        el('code', { class: 'argv', text: e.argv || '', title: e.argv || '' }),
      ]),
      (e.first_party || []).length
        ? el('div', { class: 'entrypoint-files' }, [
            el('span', { class: 'muted', text: 'first-party:' }),
            ...(e.first_party || []).map((f) =>
              onCode
                ? el('button', { class: 'link-btn small', onclick: () => onCode(f.path.replace(/^code\//, '')) }, [
                    el('span', { text: f.module }),
                  ])
                : el('code', { text: f.module })),
          ])
        : null,
      unresolved.length || skipped.length
        ? el('div', { class: 'note warn' }, [
            icon('alert', { size: 13 }),
            el('div', {}, [
              el('strong', { text: '未記録の参照あり' }),
              el('ul', { class: 'tight' }, [...unresolved, ...skipped].map((u) => el('li', { text: u }))),
            ]),
          ])
        : null,
    ]));
  }
  return wrap;
}

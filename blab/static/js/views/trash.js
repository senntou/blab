// ゴミ箱 — experiment ごとの `_trash` に溜まった run と、完全に消すための `blab rm` コマンド。
//
// UI はファイルを消さない（書き込みは blab mv だけ）。ここは見せてコマンドを渡すだけで、
// 消すのは利用者がターミナルで行う。

import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, copyButton, fmtRelative, fmtTime, pathField, statusBadge } from '../util.js';

/** シェルにそのまま貼れるように、必要なときだけ単引用符で囲む。 */
function shellQuote(s) {
  return /^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
}

function rmCommand(paths) {
  return `blab rm ${paths.map(shellQuote).join(' ')}`;
}

function runTable(group) {
  const tbody = el('tbody');
  for (const r of group.runs) {
    tbody.append(
      el('tr', {}, [
        el('td', {}, [
          el('a', { class: 'cell-name', href: `#/run/${r.path}` }, [
            icon('flask', { size: 14 }),
            el('span', { text: r.name || r.path.split('/').pop() }),
          ]),
          el('div', { class: 'muted trash-dir', text: r.path.split('/').pop() }),
        ]),
        el('td', {}, [statusBadge(r)]),
        el('td', { title: r.created_at ? fmtTime(r.created_at) : '', text: r.created_at ? fmtRelative(r.created_at) : '-' }),
        el('td', { class: 'muted', text: (r.moved_from && r.moved_from.path) || '-' }),
        el('td', { class: 'col-num' }, [
          copyButton(rmCommand([r.abs_path]), { title: 'この run を消すコマンドをコピー' }),
        ]),
      ]),
    );
  }
  return el('div', { class: 'table-scroll' }, [
    el('table', { class: 'nodes trash-table' }, [
      el('thead', {}, [el('tr', {}, [
        el('th', { text: 'run' }),
        el('th', { text: '状態' }),
        el('th', { text: '作成' }),
        el('th', { text: '元の場所' }),
        el('th', { class: 'col-num', text: 'rm' }),
      ])]),
      tbody,
    ]),
  ]);
}

function experimentPanel(group) {
  const n = group.runs.length;
  const live = group.runs.filter((r) => r.status === 'running').length;
  return el('section', { class: 'panel' }, [
    el('div', { class: 'head-row' }, [
      el('h2', {}, [
        icon('flask', { size: 16 }),
        el('a', { href: `#/e/${encodeURIComponent(group.experiment)}`, text: group.name }),
        el('span', { class: 'muted', text: `${n} 件` }),
      ]),
      el('a', { class: 'btn', href: `#/group/${group.path}`, title: 'ゴミ箱の group を開く（「移動」で元に戻せる）' }, [
        icon('external', { size: 14 }),
        el('span', { text: '開く・戻す' }),
      ]),
    ]),
    live
      ? el('p', { class: 'empty-note' }, [
        icon('alert', { size: 14 }),
        el('span', { text: `${live} 件がまだ実行中です。止めてから消してください。` }),
      ])
      : null,
    el('div', { class: 'path-row' }, [
      pathField('この experiment のゴミ箱を空にする', rmCommand([group.abs_path]), 'terminal'),
    ]),
    n ? runTable(group) : el('p', { class: 'muted', text: '空です（_trash の group だけが残っています）' }),
  ]);
}

export async function trashView() {
  const node = el('div', { class: 'view' });

  async function load() {
    const { experiments } = await api.trash();
    const groups = experiments
      .filter((g) => g.runs.length)
      .sort((a, b) => b.runs.length - a.runs.length || a.name.localeCompare(b.name));
    const total = groups.reduce((n, g) => n + g.runs.length, 0);
    clear(node);
    node.append(
      el('h1', {}, [icon('trash', { size: 20 }), el('span', { text: 'ゴミ箱' })]),
      el('p', { class: 'muted' }, [
        'UI の「削除」は run を experiment ごとの ',
        el('code', { text: '_trash' }),
        ' group に移すだけで、ファイルは残っています。完全に消すときは、下のコマンドをターミナルで実行してください（元に戻せません。',
        el('code', { text: '-y' }),
        ' を付けると確認を省けます）。',
      ]),
    );
    if (!total) {
      node.append(
        el('div', { class: 'empty-state' }, [
          icon('trash', { size: 28, class: 'empty-icon' }),
          el('p', { text: 'ゴミ箱は空です。' }),
        ]),
      );
      return;
    }
    node.append(
      el('section', { class: 'panel' }, [
        el('h2', {}, [
          icon('layers', { size: 16 }),
          el('span', { text: `すべて（${groups.length} experiment・${total} 件）` }),
        ]),
        el('div', { class: 'path-row' }, [
          pathField('すべてのゴミ箱を空にする', rmCommand(groups.map((g) => g.abs_path)), 'terminal'),
        ]),
      ]),
      ...groups.map(experimentPanel),
    );
  }

  await load();
  return { node, refresh: load };
}

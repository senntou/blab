// ルーティングと手動更新。
//
// 自動更新はしない。refresh はビューを丸ごと作り直すので、定期的に回すと選択・入力中の欄・
// 開いたメニューが操作の途中で消える。最新にしたいときはトップバーの更新ボタンを押す。

import { api, setProject } from './api.js';
import { icon } from './icons.js';
import { loadPrefs } from './prefs.js';
import { el, clear, copyButton } from './util.js';
import { experimentsView } from './views/experiments.js';
import { runsView } from './views/runs.js';
import { runView } from './views/run.js';
import { groupView } from './views/group.js';
import { compareView } from './views/compare.js';
import { trashView } from './views/trash.js';
import { componentsView } from './views/components.js';
import { componentView } from './views/component.js';
import { projectHref, projectsView } from './views/projects.js';

const app = document.getElementById('app');
const banner = document.getElementById('banner');
const refreshBtn = document.getElementById('refresh-btn');
const refreshTime = document.getElementById('refresh-time');

let current = null;

// 選択（比較・削除・移動の対象）は run 一覧の中だけで完結させる（table.js が持つ）。
// ページをまたいで持ち回らない。比較へは選んだ時点で URL クエリに載せて渡す。
const ctx = {
  notify: showError,
};

function showError(message) {
  clear(banner);
  banner.hidden = false;
  banner.append(
    el('span', { class: 'banner-msg' }, [icon('alert', { size: 16 }), el('span', { text: message })]),
    el('button', { class: 'icon-btn', title: '閉じる', onclick: () => { banner.hidden = true; } }, [icon('x')]),
  );
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  if (!raw) return { name: 'experiments', query: new URLSearchParams() };
  const [hashPath, queryString] = raw.split('?');
  const query = new URLSearchParams(queryString || '');
  const [head, ...rest] = hashPath.split('/');
  const path = rest.join('/');
  if (head === 'e') return { name: 'runs', path, query };
  if (head === 'run') return { name: 'run', path, query };
  if (head === 'group') return { name: 'group', path, query };
  if (head === 'compare') return { name: 'compare', query };
  if (head === 'trash') return { name: 'trash', query };
  if (head === 'components') return { name: 'components', query };
  if (head === 'component') return { name: 'component', path, query };
  return { name: 'experiments', query };
}

function syncNav(route) {
  for (const a of document.querySelectorAll('.topbar nav a')) {
    const alias = (route.name === 'runs' && a.dataset.route === 'experiments')
      || (route.name === 'component' && a.dataset.route === 'components')
      || (route.name === 'group' && route.path.split('/').includes('_trash') && a.dataset.route === 'trash');
    a.classList.toggle('on', a.dataset.route === route.name || alias);
  }
}

/**
 * 読み込み前に出すタブ名（URL から作る仮のもの）。並んだタブを見分けられるよう、いちばん固有の名前を先頭に置く。
 * run / group は読み込み後にビューの title()（表示名）で付け直す。
 */
function pageTitle(r) {
  const parts = (r.path || '').split('/').filter(Boolean).map((s) => {
    try { return decodeURIComponent(s); } catch { return s; }
  });
  const leaf = parts[parts.length - 1];
  let head;
  if (r.name === 'run' || r.name === 'group') head = parts.length > 1 ? `${leaf} · ${parts[0]}` : leaf;
  else if (r.name === 'runs' || r.name === 'component') head = leaf;
  else if (r.name === 'compare') head = '比較';
  else if (r.name === 'trash') head = 'ゴミ箱';
  else if (r.name === 'components') head = 'Components';
  return head ? `${head} - blab` : 'blab';
}

function markUpdated() {
  const now = new Date();
  refreshTime.textContent = `最終更新 ${now.toLocaleTimeString()}`;
  refreshTime.title = now.toLocaleString();
}

async function route() {
  const r = parseRoute();
  if (current && current.destroy) current.destroy();
  syncNav(r);
  document.title = pageTitle(r);
  app.setAttribute('aria-busy', 'true');
  try {
    if (r.name === 'runs') current = await runsView(ctx, r.path);
    else if (r.name === 'run') current = await runView(ctx, r.path);
    else if (r.name === 'group') current = await groupView(ctx, r.path);
    else if (r.name === 'compare') current = await compareView(ctx, r.query);
    else if (r.name === 'trash') current = await trashView(ctx);
    else if (r.name === 'components') current = await componentsView(ctx);
    else if (r.name === 'component') current = await componentView(ctx, decodeURIComponent(r.path));
    else current = await experimentsView(ctx);
    if (current.title) document.title = `${current.title()} - blab`;
    banner.hidden = true;
    clear(app);
    app.append(current.node);
    app.scrollTo(0, 0);
    markUpdated();
  } catch (e) {
    current = null;
    clear(app);
    app.append(
      el('div', { class: 'empty-state' }, [
        icon('alert', { size: 28, class: 'empty-icon' }),
        el('p', { text: `読み込めませんでした: ${e.message}` }),
        el('button', { class: 'btn', text: '再読み込み', onclick: () => route() }),
      ]),
    );
  } finally {
    app.removeAttribute('aria-busy');
  }
}

async function refresh() {
  if (refreshBtn.disabled) return;
  // 読み込みに失敗した画面には refresh がないので、ルートから開き直す。
  if (!current) {
    await route();
    return;
  }
  refreshBtn.disabled = true;
  const scrollTop = app.scrollTop;
  try {
    await current.refresh();
    if (current.title) document.title = `${current.title()} - blab`;
    app.scrollTop = scrollTop;
    banner.hidden = true;
    markUpdated();
  } catch (e) {
    showError(`更新に失敗しました: ${e.message}`);
  } finally {
    refreshBtn.disabled = false;
  }
}

/** トップバーのプロジェクト欄。選ぶとそのプロジェクトのページへ移る（タブごとに別のプロジェクトを開ける）。 */
function projectSwitcher(projects, uid) {
  // 同名のプロジェクトがあると名前だけでは区別できないので、そのときだけパスを添える。
  const counts = {};
  for (const p of projects) counts[p.name] = (counts[p.name] || 0) + 1;
  const select = el('select', {
    class: 'project-name project-select',
    title: 'プロジェクトを切り替える',
    'aria-label': 'プロジェクト',
    onchange: (e) => { location.href = projectHref(e.target.value); },
  }, projects.map((p) => el('option', {
    value: p.uid,
    selected: p.uid === uid,
    disabled: !p.available,
    title: p.path,
    text: `${p.name}${counts[p.name] > 1 ? ` (${p.path})` : ''}${p.available ? '' : '（見つかりません）'}`,
  })));
  return select;
}

async function showRoot(projects, uid) {
  const label = document.getElementById('root-label');
  clear(label);
  label.append(icon('box', { size: 14 }), projectSwitcher(projects, uid));
  try {
    const status = await api.status();
    label.append(
      icon('folder', { size: 14 }),
      el('code', { class: 'root-text', text: status.runs_dir, title: status.runs_dir }),
      copyButton(status.runs_dir, { title: 'ログルートのパスをコピー' }),
    );
  } catch (e) {
    showError(`プロジェクトを読めません: ${e.message}`);
  }
}

/** 開くプロジェクトが決まらないときは、ルーティングせず選択画面だけを出す。 */
function showPicker(projects, message) {
  document.querySelector('.topbar nav').hidden = true;
  document.getElementById('root-label').hidden = true;
  refreshBtn.hidden = true;
  const view = projectsView(projects, { message });
  clear(app);
  app.append(view.node);
}

async function boot() {
  loadPrefs();
  refreshBtn.append(icon('refresh', { size: 14 }), el('span', { text: '更新' }));
  refreshBtn.addEventListener('click', refresh);

  let doc;
  try {
    doc = await api.projects();
  } catch (e) {
    showError('サーバに接続できません');
    return;
  }
  const requested = new URLSearchParams(location.search).get('p');
  const uid = requested || doc.default;
  const entry = doc.projects.find((p) => p.uid === uid);
  if (!uid) {
    showPicker(doc.projects, null);
    return;
  }
  if (!entry || !entry.available) {
    showPicker(doc.projects, entry
      ? `${entry.name}（${entry.path}）が見つかりません。移動したなら、新しい場所で blab link を実行してください。`
      : `プロジェクト ${uid} はグローバル索引にありません。`);
    return;
  }
  // 既定のプロジェクトで開いたときも URL に載せる（ブックマーク・共有したときに同じものが開く）。
  if (!requested) history.replaceState(null, '', `${projectHref(uid).split('#')[0]}${location.hash || '#/'}`);
  setProject(uid);
  showRoot(doc.projects, uid);
  window.addEventListener('hashchange', route);
  await route();
}

boot();

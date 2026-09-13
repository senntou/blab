// ルーティングと手動更新。
//
// 自動更新はしない。refresh はビューを丸ごと作り直すので、定期的に回すと選択・入力中の欄・
// 開いたメニューが操作の途中で消える。最新にしたいときはトップバーの更新ボタンを押す。

import { api } from './api.js';
import { icon } from './icons.js';
import { loadPrefs } from './prefs.js';
import { el, clear, copyButton } from './util.js';
import { experimentsView } from './views/experiments.js';
import { runsView } from './views/runs.js';
import { runView } from './views/run.js';
import { groupView } from './views/group.js';
import { compareView } from './views/compare.js';
import { componentsView } from './views/components.js';
import { componentView } from './views/component.js';

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
  if (head === 'components') return { name: 'components', query };
  if (head === 'component') return { name: 'component', path, query };
  return { name: 'experiments', query };
}

function syncNav(route) {
  for (const a of document.querySelectorAll('.topbar nav a')) {
    const alias = (route.name === 'runs' && a.dataset.route === 'experiments')
      || (route.name === 'component' && a.dataset.route === 'components');
    a.classList.toggle('on', a.dataset.route === route.name || alias);
  }
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
  app.setAttribute('aria-busy', 'true');
  try {
    if (r.name === 'runs') current = await runsView(ctx, r.path);
    else if (r.name === 'run') current = await runView(ctx, r.path);
    else if (r.name === 'group') current = await groupView(ctx, r.path);
    else if (r.name === 'compare') current = await compareView(ctx, r.query);
    else if (r.name === 'components') current = await componentsView(ctx);
    else if (r.name === 'component') current = await componentView(ctx, decodeURIComponent(r.path));
    else current = await experimentsView(ctx);
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
    app.scrollTop = scrollTop;
    banner.hidden = true;
    markUpdated();
  } catch (e) {
    showError(`更新に失敗しました: ${e.message}`);
  } finally {
    refreshBtn.disabled = false;
  }
}

async function showRoot() {
  const label = document.getElementById('root-label');
  try {
    const status = await api.status();
    clear(label);
    label.append(
      icon('box', { size: 14 }),
      el('span', { class: 'project-name', text: status.project }),
      icon('folder', { size: 14 }),
      el('code', { class: 'root-text', text: status.runs_dir, title: status.runs_dir }),
      copyButton(status.runs_dir, { title: 'ログルートのパスをコピー' }),
    );
  } catch (e) {
    showError('サーバに接続できません');
  }
}

async function boot() {
  loadPrefs();
  refreshBtn.append(icon('refresh', { size: 14 }), el('span', { text: '更新' }));
  refreshBtn.addEventListener('click', refresh);
  showRoot();
  await route();
}

window.addEventListener('hashchange', route);
boot();

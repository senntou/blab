// ルーティングとポーリング。ライブ更新は running な run があるときだけ回す。

import { api } from './api.js';
import { icon } from './icons.js';
import { getPref, loadPrefs, setPref } from './prefs.js';
import { el, clear, copyButton } from './util.js';
import { experimentsView } from './views/experiments.js';
import { runsView } from './views/runs.js';
import { runView } from './views/run.js';
import { groupView } from './views/group.js';
import { compareView } from './views/compare.js';
import { componentsView } from './views/components.js';
import { componentView } from './views/component.js';

const POLL_MS = 3000;
const SELECTION_KEY = 'selection';

const app = document.getElementById('app');
const selectionBar = document.getElementById('selection-bar');
const banner = document.getElementById('banner');

let current = null;
let timer = null;
let selection = [];

const ctx = {
  selection: () => selection,
  setSelection(paths) {
    selection = [...new Set(paths)];
    setPref(SELECTION_KEY, selection);
    renderSelectionBar();
  },
  notify: showError,
};

function renderSelectionBar() {
  clear(selectionBar);
  if (!selection.length) {
    selectionBar.hidden = true;
    return;
  }
  selectionBar.hidden = false;
  selectionBar.append(
    el('span', { class: 'sel-count' }, [icon('check', { size: 15 }), el('strong', { text: String(selection.length) }), ' run を選択中']),
    el('span', { class: 'sel-names', text: selection.map((p) => p.split('/').pop()).join(', ') }),
    el('span', { class: 'spacer' }),
    el('a', { class: 'btn primary', href: '#/compare' }, [icon('compare'), el('span', { text: '比較する' })]),
    el('button', { class: 'btn', onclick: () => ctx.setSelection([]) }, [icon('x'), el('span', { text: 'クリア' })]),
  );
}

function showError(message) {
  clear(banner);
  banner.hidden = false;
  banner.append(
    el('span', { class: 'banner-msg' }, [icon('alert', { size: 16 }), el('span', { text: message })]),
    el('button', { class: 'icon-btn', title: '閉じる', onclick: () => { banner.hidden = true; } }, [icon('x')]),
  );
}

function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, '');
  if (!hash) return { name: 'experiments' };
  const [head, ...rest] = hash.split('/');
  const path = rest.join('/');
  if (head === 'e') return { name: 'runs', path };
  if (head === 'run') return { name: 'run', path };
  if (head === 'group') return { name: 'group', path };
  if (head === 'compare') return { name: 'compare' };
  if (head === 'components') return { name: 'components' };
  if (head === 'component') return { name: 'component', path };
  return { name: 'experiments' };
}

function syncNav(route) {
  for (const a of document.querySelectorAll('.topbar nav a')) {
    const alias = (route.name === 'runs' && a.dataset.route === 'experiments')
      || (route.name === 'component' && a.dataset.route === 'components');
    a.classList.toggle('on', a.dataset.route === route.name || alias);
  }
}

async function route() {
  const r = parseRoute();
  clearInterval(timer);
  if (current && current.destroy) current.destroy();
  syncNav(r);
  app.setAttribute('aria-busy', 'true');
  try {
    if (r.name === 'runs') current = await runsView(ctx, r.path);
    else if (r.name === 'run') current = await runView(ctx, r.path);
    else if (r.name === 'group') current = await groupView(ctx, r.path);
    else if (r.name === 'compare') current = await compareView(ctx);
    else if (r.name === 'components') current = await componentsView(ctx);
    else if (r.name === 'component') current = await componentView(ctx, decodeURIComponent(r.path));
    else current = await experimentsView(ctx);
    banner.hidden = true;
    clear(app);
    app.append(current.node);
    app.scrollTo(0, 0);
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
  timer = setInterval(tick, POLL_MS);
}

async function tick() {
  if (!current || !current.live || !current.live()) return;
  if (document.hidden) return;
  try {
    await current.refresh();
    banner.hidden = true;
  } catch (e) {
    showError(`更新に失敗しました: ${e.message}`);
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
  const stored = getPref(SELECTION_KEY, []);
  selection = Array.isArray(stored) ? stored : [];
  renderSelectionBar();
  showRoot();
  await route();
}

window.addEventListener('hashchange', route);
boot();

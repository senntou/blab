// Run 詳細 — meta / 構成ツリー / metrics / アーティファクト / ログ / 環境 / 焼き込んだソース。

import { api, fileUrl } from '../api.js';
import { artifactBrowser } from '../artifacts.js';
import { lineChart, seriesPoints } from '../chart.js';
import { argsTable, configTree, flattenTree, renderValue, shortHash, versionLabel, versionTitle } from '../config-tree.js';
import { countLabel, filterBox, filteredTable, rankByQuery } from '../filter.js';
import { icon, statusDot } from '../icons.js';
import { getPref, setPref } from '../prefs.js';
import { codeBlock, markdownBlock } from '../source.js';
import { clear, el, fmtBytes, fmtDuration, fmtTime, pathField, tabs } from '../util.js';

function metaGrid(detail) {
  const meta = detail.meta || {};
  const items = [
    ['状態', el('span', { class: `status status-${detail.status}` }, [statusDot(detail.status), el('span', { text: detail.status })])],
    ['作成', meta.created_at ? fmtTime(meta.created_at) : '-'],
    ['所要', fmtDuration(meta.duration_sec)],
    ['ID', el('code', { text: meta.id || '-' })],
    ['YAML', meta.source ? el('code', { text: meta.source }) : '-'],
  ];
  if (meta.replay_of) {
    items.push(['再実行元', el('code', { text: String(meta.replay_of.path || '').split('/').pop() })]);
  }
  if (meta.moved_from) {
    // 黙って履歴を書き換えない。移動の事実は残っている。
    items.push(['移動元', el('code', { text: meta.moved_from.path })]);
  }

  const grid = el('dl', { class: 'meta-grid' });
  for (const [label, value] of items) {
    grid.append(
      el('div', {}, [
        el('dt', { text: label }),
        el('dd', {}, [value && value.nodeType ? value : el('span', { text: String(value) })]),
      ]),
    );
  }
  return grid;
}

function exitPanel(meta) {
  if (!meta.exit) return null;
  return el('section', { class: 'panel danger-panel' }, [
    el('h2', {}, [icon('alert', { size: 16 }), el('span', { text: `${meta.exit.type}: ${meta.exit.message}` })]),
    meta.exit.traceback ? codeBlock(meta.exit.traceback, { language: 'text' }) : null,
  ]);
}

/** summary タブ。値は数百になりうるので、カードではなく絞り込める表にする。 */
function summaryPanel(summary) {
  const names = Object.keys(summary || {}).sort();
  if (!names.length) return el('p', { class: 'muted pad', text: 'summary.json がありません' });
  return filteredTable({
    keys: names,
    head: ['名前', '値'],
    prefKey: 'filter.run.summary',
    className: 'args summary-table',
    row: (name) => el('tr', {}, [el('th', { text: name }), el('td', {}, [renderValue(summary[name])])]),
  });
}

// component の README は version が変わらない限り同じ内容。タブを行き来しても
// 何度も取りに行かないよう、run 詳細を開いている間だけ憶えておく。
const componentInfoCache = new Map();

function loadComponentInfo(id) {
  if (!componentInfoCache.has(id)) {
    componentInfoCache.set(id, api.component(id).catch(() => null));
  }
  return componentInfoCache.get(id);
}

/**
 * 選んだ 1 component の詳細（説明 + ハイパーパラメータ）。構成ツリーの右側に出す。
 *
 * @param {string} nodePath  木の位置（`run.dataset` など）
 * @param {object} node
 * @param {number|null} buildIndex  ツリーで特定の build 行を選んだ場合、その番号。
 *   `null` なら「宣言そのもの」を選んだ扱い（README + 全 build を並べる）。
 */
function configDetailPanel(nodePath, node, buildIndex = null) {
  const host = el('div', { class: 'config-detail' });
  const builds = node.builds || [];
  const build = buildIndex !== null ? builds[buildIndex] : null;

  host.append(
    el('div', { class: 'detail-head' }, [
      el('a', {
        class: 'detail-title',
        href: `#/component/${encodeURIComponent(node.use)}`,
        title: `${node.use} のコード・README へ`,
      }, [
        icon('box', { size: 16 }),
        el('strong', { text: node.use }),
        el('span', { class: 'tree-version', title: versionTitle(node), text: versionLabel(node) }),
      ]),
    ]),
    el('div', { class: 'detail-sub' }, [
      el('code', { class: 'muted', text: nodePath }),
      build ? el('span', { class: 'tree-entry', text: `build #${buildIndex}` }) : null,
      node.entry ? el('span', { class: 'tree-entry', text: node.entry }) : null,
      el('code', { class: 'tree-hash', title: node.hash, text: shortHash(node.hash) }),
    ]),
  );

  const desc = el('div', { class: 'detail-desc' }, [el('p', { class: 'muted', text: '説明を読み込み中…' })]);
  host.append(desc);
  loadComponentInfo(node.use).then((info) => {
    clear(desc);
    if (info && info.readme) {
      desc.append(markdownBlock(info.readme));
    } else {
      desc.append(
        el('p', { class: 'muted detail-no-readme' }, [
          icon('info', { size: 13 }),
          el('span', { text: `説明が保存されていません。components/${node.use}/README.md（か README.ja.md）を書くと、ここに出ます。` }),
        ]),
      );
    }
  });

  const args = el('div', { class: 'detail-args' });
  if (build) {
    // ツリーで特定の build 行を選んだ場合は、それだけを見せる（他の build と混ぜない）。
    args.append(argsTable(build.args, build.args_from));
  } else if (node.args) {
    args.append(argsTable(node.args, node.args_from));
  } else if (!builds.length) {
    args.append(
      el('p', { class: 'tree-unused' }, [
        icon('alert', { size: 14 }),
        el('span', { text: '宣言されましたが、一度も build されませんでした' }),
      ]),
    );
  } else if (builds.length === 1) {
    args.append(argsTable(builds[0].args, builds[0].args_from));
  } else {
    // 宣言そのものを選んだ場合の概観。個々の build はツリーの行から選べる。
    args.append(
      el('p', { class: 'muted' }, [
        icon('info', { size: 13 }),
        el('span', { text: `${builds.length} 回 build されました。左のツリーから 1 つずつ選べます。` }),
      ]),
    );
  }
  host.append(args);

  return host;
}

/** 構成タブ本体。左に木、右に選んだ component の詳細（説明・ハイパーパラメータ）。 */
function configPanel(resolved) {
  const flat = flattenTree(resolved);
  const layout = el('div', { class: 'config-layout' });
  const host = resolved && resolved.provisional
    ? el('div', {}, [
        el('p', { class: 'provisional-note' }, [
          icon('info', { size: 14 }),
          el('span', { text: '実行途中の仮の記録です。.build() が呼ばれるたびに更新され、終了時に確定します（まだ呼ばれていない build の引数は出ません）。' }),
        ]),
        layout,
      ])
    : layout;
  const treeHost = el('div', { class: 'config-tree-pane' });
  const detailHost = el('div', { class: 'config-detail-pane' });
  let selected = flat.run ? 'run' : null;

  function paint() {
    for (const box of treeHost.querySelectorAll('[data-path]')) {
      box.classList.toggle('is-selected', box.dataset.path === selected);
    }
    clear(detailHost);
    // build 行（`run.dataset#1` のように選ばれる）は、木の位置 + build 番号に分ける。
    const [basePath, buildIndexText] = selected ? selected.split('#') : [null];
    const node = basePath && flat[basePath];
    const buildIndex = buildIndexText === undefined ? null : Number(buildIndexText);
    detailHost.append(
      node
        ? configDetailPanel(basePath, node, buildIndex)
        : el('p', { class: 'muted pad', text: 'component を選ぶと詳細が出ます' }),
    );
  }

  treeHost.append(
    configTree(resolved, {
      onSelect: (path) => {
        selected = path;
        paint();
      },
    }),
  );
  layout.append(treeHost, detailHost);
  paint();
  return host;
}

async function metricsPanel(path, keys) {
  const host = el('div', { class: 'metrics' });
  if (!keys.length) {
    host.append(el('p', { class: 'muted pad', text: 'metrics がありません' }));
    return host;
  }
  const xKey = `run.x`;
  let xAxis = getPref(xKey, 'epoch');
  let logY = getPref('run.logy', false);

  const charts = el('div', { class: 'chart-grid' });
  const count = el('span', { class: 'filter-count' });
  const box = filterBox({ prefKey: 'filter.run.metrics', onInput: render });
  let series = {};

  async function draw() {
    ({ series } = await api.metrics(path, { max_points: 2000 }));
    render();
  }

  // 絞り込み・軸の切り替えは取り直さず、手元の系列から描き直す。
  function render() {
    const names = Object.keys(series).sort();
    const shown = rankByQuery(names, box.input.value);
    count.textContent = countLabel(shown.length, names.length);
    clear(charts);
    if (!shown.length) {
      charts.append(el('p', { class: 'empty-note' }, [icon('info', { size: 14 }), el('span', { text: '一致する名前がありません' })]));
      return;
    }
    for (const name of shown) {
      const points = seriesPoints(series[name], xAxis);
      charts.append(
        lineChart({
          title: name,
          series: [{ label: name, color: 'var(--accent)', points }],
          xLabel: xAxis,
          logY,
          legend: false,
        }),
      );
    }
  }

  host.append(
    el('div', { class: 'chart-controls' }, [
      box,
      count,
      el('label', {}, [
        el('span', { text: 'x 軸' }),
        el('select', {
          onchange: (e) => {
            xAxis = e.target.value;
            setPref(xKey, xAxis);
            render();
          },
        }, ['epoch', 'step', 'time'].map((v) => el('option', { value: v, selected: v === xAxis, text: v }))),
      ]),
      el('label', {}, [
        el('input', {
          type: 'checkbox',
          checked: logY,
          onchange: (e) => {
            logY = e.target.checked;
            setPref('run.logy', logY);
            render();
          },
        }),
        el('span', { text: 'log y' }),
      ]),
    ]),
    charts,
  );
  await draw();
  return host;
}

function envPanel(env) {
  if (!env) return el('p', { class: 'muted pad', text: 'env.json がありません' });
  const wrap = el('div', { class: 'env' });
  const table = el('table', { class: 'args' });
  const body = el('tbody');
  for (const key of Object.keys(env).sort()) {
    if (key === 'packages' || key === 'schema_version') continue;
    body.append(el('tr', {}, [el('th', { text: key }), el('td', {}, [renderValue(env[key])])]));
  }
  table.append(body);
  wrap.append(table);

  const packages = env.packages;
  if (packages && typeof packages === 'object' && !('$unavailable' in packages)) {
    const details = el('details', { class: 'packages' });
    details.append(el('summary', { text: `パッケージ ${Object.keys(packages).length} 件` }));
    const list = el('table', { class: 'args' });
    const tbody = el('tbody');
    for (const name of Object.keys(packages).sort()) {
      tbody.append(el('tr', {}, [el('th', { text: name }), el('td', {}, [el('code', { text: packages[name] })])]));
    }
    list.append(tbody);
    details.append(list);
    wrap.append(details);
  } else if (packages) {
    wrap.append(el('p', { class: 'muted' }, [renderValue(packages)]));
  }
  return wrap;
}

const HASH_KIND_LABEL = {
  partial: '先頭のみ（巨大ファイルなので全バイトは読まない）',
  manifest: 'ファイル一覧のみ（中身は読まない）',
};

/** 小さいファイル（run にコピー済み）の中身をその場に出す。 */
function dataFilePreview(url) {
  const host = el('div', { class: 'data-preview' });
  fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.text();
    })
    .then((text) => host.append(codeBlock(text, { language: 'text' })))
    .catch((e) => host.append(el('p', { class: 'muted', text: `プレビューを読み込めませんでした: ${e.message}` })));
  return host;
}

function dataEntry(name, entry, runPath) {
  const box = el('div', { class: 'data-entry' });
  box.append(
    el('div', { class: 'data-entry-head' }, [
      icon('folder', { size: 14 }),
      el('strong', { text: name }),
      entry.hash ? el('code', { class: 'muted', title: entry.hash, text: shortHash(entry.hash) }) : null,
      HASH_KIND_LABEL[entry.hash_kind]
        ? el('span', { class: 'chip', title: HASH_KIND_LABEL[entry.hash_kind], text: entry.hash_kind })
        : null,
    ]),
    // 実パスは「今このマシンでどこを指しているか」。YAML には論理名しか書かないので、
    // これが分からないと「そもそもどのファイルの話か」を追えない。
    el('div', { class: 'data-entry-path muted', title: '実パス（このマシンでの場所）' }, [
      el('code', { text: entry.path || '-' }),
    ]),
  );

  if (entry.$unavailable) {
    box.append(renderValue({ $unavailable: entry.$unavailable }));
  } else if (entry.copied_to) {
    const url = fileUrl(runPath, entry.copied_to);
    box.append(
      el('div', { class: 'data-entry-copy' }, [
        icon('check', { size: 13 }),
        el('span', { text: '小さいファイルなので run にコピー済み: ' }),
        el('code', { text: `<run>/${entry.copied_to}` }),
        el('a', { class: 'data-open', href: url, target: '_blank', title: '生ファイルを新しいタブで開く' }, [
          icon('download', { size: 13 }),
          el('span', { text: '生ファイルを開く' }),
        ]),
      ]),
      dataFilePreview(url),
    );
  } else {
    box.append(
      el('p', { class: 'muted' }, [
        icon('info', { size: 13 }),
        el('span', { text: '大きいファイル / ディレクトリなので run にはコピーせず、同一性（ハッシュ）だけを記録' }),
      ]),
    );
  }
  return box;
}

function dataPanel(data, runPath) {
  const names = Object.keys(data || {}).filter((k) => k !== 'schema_version');
  if (!names.length) {
    return el('p', { class: 'muted pad', text: '外部ファイル（{data: ...}）は使われていません' });
  }
  const host = el('div', { class: 'data-panel' });
  host.append(el('h3', { text: '外部ファイル' }));
  for (const name of names) {
    host.append(dataEntry(name, data[name] || {}, runPath));
  }
  return host;
}

async function sourcePanel(path, components) {
  const host = el('div', { class: 'source-panel' });
  if (!components.length) {
    host.append(el('p', { class: 'muted pad', text: 'components/ がありません' }));
    return host;
  }
  const files = [];
  for (const component of components) {
    for (const name of component.files) files.push(`${component.id}/${name}`);
  }
  const picker = el('select', {
    onchange: (e) => show(e.target.value),
  }, files.map((f) => el('option', { value: f, text: f })));

  const body = el('div', { class: 'source-body' });

  async function show(file) {
    clear(body);
    try {
      const doc = await api.runSource(path, file);
      const lang = file.endsWith('.md') ? 'md' : file.endsWith('.py') ? 'python' : 'text';
      body.append(lang === 'md' ? markdownBlock(doc.text) : codeBlock(doc.text, { language: lang }));
    } catch (e) {
      body.append(el('p', { class: 'muted', text: e.message }));
    }
  }

  host.append(
    el('div', { class: 'source-head' }, [
      el('p', { class: 'muted' }, [
        icon('box', { size: 14 }),
        el('span', { text: 'この run に焼き込まれた実体。レジストリを消しても読める。' }),
      ]),
      picker,
    ]),
    body,
  );
  await show(files[0]);
  return host;
}

async function yamlPanel(path, meta) {
  const host = el('div', { class: 'source-panel' });
  const source = meta.source;
  if (!source) {
    host.append(el('p', { class: 'muted pad', text: 'この run が生まれた yaml が記録されていません' }));
    return host;
  }
  host.append(
    el('p', { class: 'muted' }, [
      icon('file', { size: 14 }),
      el('span', { text: `この run を定義した宣言。` }),
      el('code', { text: source }),
    ]),
  );
  try {
    const doc = await api.experimentSource(path);
    host.append(codeBlock(doc.text, { language: 'yaml' }));
  } catch (e) {
    host.append(el('p', { class: 'muted', text: e.message }));
  }
  return host;
}

async function logsPanel(path, logs) {
  const host = el('div', { class: 'logs' });
  if (!logs.length) {
    host.append(el('p', { class: 'muted pad', text: 'ログがありません' }));
    return host;
  }
  const body = el('div');
  const picker = el('select', { onchange: (e) => show(e.target.value) },
    logs.map((l) => el('option', { value: l.name, text: `${l.name} (${fmtBytes(l.size)})` })));

  async function show(name) {
    clear(body);
    const doc = await api.log(path, name, 2000);
    body.append(codeBlock(doc.text || '(空)', { language: 'text' }));
  }

  host.append(el('div', { class: 'source-head' }, [picker]), body);
  await show(logs[0].name);
  return host;
}

export async function runView(ctx, path) {
  const node = el('div', { class: 'view' });
  let detail = null;

  async function load() {
    detail = await api.detail(path);
    clear(node);

    const experiment = path.split('/')[0];
    node.append(
      el('div', { class: 'crumbs' }, [
        el('a', { href: '#/' }, [el('span', { text: 'Experiments' })]),
        el('span', { class: 'sep', text: '/' }),
        el('a', { href: `#/e/${encodeURIComponent(experiment)}` }, [el('span', { text: experiment })]),
      ]),
      el('div', { class: 'page-head' }, [
        el('h1', { text: detail.name || path.split('/').pop() }),
      ]),
      metaGrid(detail),
      pathField('run', path, 'folder'),
    );

    const exit = exitPanel(detail.meta || {});
    if (exit) node.append(exit);

    const summaryNames = Object.keys(detail.summary || {});
    const dataNames = Object.keys(detail.data || {}).filter((k) => k !== 'schema_version');
    node.append(
      tabs(
        [
          {
            id: 'yaml',
            label: 'YAML',
            icon: 'file',
            render: () => {
              const host = el('div');
              yamlPanel(path, detail.meta || {}).then((panel) => {
                clear(host);
                host.append(panel);
              });
              return host;
            },
          },
          {
            id: 'config',
            label: '構成',
            icon: 'box',
            render: () => configPanel(detail.resolved),
          },
          {
            id: 'summary',
            label: 'summary',
            icon: 'table',
            count: summaryNames.length || null,
            render: () => summaryPanel(detail.summary),
          },
          {
            id: 'metrics',
            label: 'metrics',
            icon: 'chart',
            render: () => {
              const host = el('div');
              api.metrics(path, { max_points: 1 })
                .then((doc) => metricsPanel(path, doc.keys))
                .then((panel) => { clear(host); host.append(panel); })
                .catch((e) => host.append(el('p', { class: 'muted', text: e.message })));
              return host;
            },
          },
          {
            id: 'artifacts',
            label: 'アーティファクト',
            icon: 'image',
            count: (detail.artifacts || []).length,
            render: () => artifactBrowser(path, detail.artifacts || []),
          },
          {
            id: 'source',
            label: 'ソース',
            icon: 'code',
            render: () => {
              const host = el('div');
              sourcePanel(path, detail.components || []).then((panel) => {
                clear(host);
                host.append(panel);
              });
              return host;
            },
          },
          {
            id: 'data',
            label: '外部ファイル',
            icon: 'folder',
            count: dataNames.length || null,
            render: () => dataPanel(detail.data, path),
          },
          {
            id: 'logs',
            label: 'ログ',
            icon: 'terminal',
            count: (detail.logs || []).length,
            render: () => {
              const host = el('div');
              logsPanel(path, detail.logs || []).then((panel) => {
                clear(host);
                host.append(panel);
              });
              return host;
            },
          },
          {
            id: 'env',
            label: '環境',
            icon: 'server',
            render: () => envPanel(detail.env),
          },
        ],
        { prefKey: 'run.tab' },
      ),
    );
  }

  await load();
  return {
    node,
    refresh: load,
    live: () => detail && (detail.status === 'running' || detail.status === 'stale'),
  };
}

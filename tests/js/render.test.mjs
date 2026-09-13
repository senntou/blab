// 構成ツリーと run テーブルの描画テスト。
// 「引数の出どころを色で区別する」「記録できなかったものを黙って省略しない」といった
// 設計上の約束が、実際に DOM に出ているかを見る。

import assert from 'node:assert/strict';
import { flatten } from './dom-shim.mjs';

const { argsTable, configTree, flattenTree } = await import('../../blab/static/js/config-tree.js');
const { nodeTable } = await import('../../blab/static/js/table.js');

const RESOLVED = {
  schema_version: 2,
  experiment: 'cifar100',
  overrides: { 'run.lr': 0.0003 },
  run: {
    use: 'standard_trainer',
    hash: 'sha256:db83d7b3aaaaaaaa',
    entry: 'StandardTrainer',
    args: { epochs: 30, lr: 0.0003, log_every: 1 },
    args_from: { epochs: 'yaml', lr: 'override', log_every: 'default' },
    children: {
      dataset: {
        use: 'two_moons',
        hash: 'sha256:415672acbbbbbbbb',
        entry: 'TwoMoons',
        builds: [
          { args: { split: 'train', n: 600 }, args_from: { split: 'runtime', n: 'yaml' } },
          { args: { split: 'val', n: 600 }, args_from: { split: 'runtime', n: 'yaml' } },
        ],
      },
      model: {
        use: 'linear_classifier',
        hash: 'sha256:c93122b2cccccccc',
        label: 'v1',
        entry: 'LinearClassifier',
        builds: [
          {
            args: { k: 2, opt: { $unrecorded: 'Adam' }, src: { $ref: 'dataset#0' } },
            args_from: { k: 'runtime', opt: 'runtime', src: 'runtime' },
          },
        ],
      },
      unused: { use: 'never_built', hash: 'sha256:dddddddd', entry: 'X', builds: [] },
    },
  },
};

function tokens(node) {
  return flatten(node).join(' ');
}

// --- 構成ツリー -----------------------------------------------------------
// ハイパーパラメータそのものは持たない。「どんな component が、どんな階層で
// 使われているか」だけを見渡せる、押せる箱の並びであることを見る
// （値と出どころは選んだ 1 component だけを見せる argsTable の役目 — run.js 側）。
{
  const text = tokens(configTree(RESOLVED));

  assert.ok(text.includes('standard_trainer'), 'root component が出る');
  assert.ok(text.includes('two_moons'), '子 component が出る');
  assert.ok(text.includes('@v1'), 'ラベルがあれば版はラベルで出る');
  assert.ok(text.includes('@415672ac'), 'ラベルが無ければ短いハッシュで出る');

  // 押して選ぶ箱であって、リンクとして直接 component ページには飛ばない。
  assert.ok(text.includes('.tree-head'), '箱の見出しがある');
  assert.ok(!text.includes('.arg-yaml') && !text.includes('.origin.origin-yaml'), 'ハイパラの値は木の中には出ない');

  // 宣言されたのに build されなかった子は、黙って消さない。
  assert.ok(text.includes('一度も build されませんでした'), '未使用の子を明示する');

  // --set の記録。
  assert.ok(text.includes('run.lr=0.0003'), 'overrides を出す');

  // 同じ Builder から 2 回 build した dataset は、`×2` に畳まず 1 行 1 build で
  // 別々に見える（存在するものを畳んで隠さない）。
  assert.ok(!text.includes('×2'), '`×N` には畳まない');
  assert.ok(text.includes('.tree-build') && text.includes('.tree-builds'), 'build ごとの行がある');
  assert.ok(text.includes('split="train"'), '各 build の見分けラベルが出る');
  assert.ok(text.includes('split="val"'), 'train と val の両方が別行で出る');

  console.log('config-tree: ok');
}

// --- argsTable（選んだ component の詳細パネルが使う） ----------------------
// 「引数は YAML 由来と実行時由来を色で区別する」「記録できなかったものを黙って
// 省略しない」という約束は、値そのものを描く argsTable に移った。
{
  const rootText = tokens(argsTable(RESOLVED.run.args, RESOLVED.run.args_from));
  for (const origin of ['yaml', 'override', 'default']) {
    assert.ok(rootText.includes(`.arg-${origin}`), `arg-${origin} の行がある`);
    assert.ok(rootText.includes(`.origin.origin-${origin}`), `origin-${origin} のバッジがある`);
  }

  const modelBuild = RESOLVED.run.children.model.builds[0];
  const modelText = tokens(argsTable(modelBuild.args, modelBuild.args_from));
  assert.ok(modelText.includes('.arg-runtime'), 'arg-runtime の行がある');
  assert.ok(modelText.includes('.origin.origin-runtime'), 'origin-runtime のバッジがある');
  assert.ok(modelText.includes('記録なし (Adam)'), '$unrecorded を出す');
  assert.ok(modelText.includes('.chip.chip-ref'), '$ref を参照チップで出す');
  assert.ok(modelText.includes('dataset#0'), '$ref の参照先を出す');

  console.log('argsTable: ok');
}

// --- flattenTree（比較ビューが使う） -----------------------------------
{
  const flat = flattenTree(RESOLVED);
  assert.deepEqual(
    Object.keys(flat).sort(),
    ['run', 'run.dataset', 'run.model', 'run.unused'],
  );
  assert.equal(flat['run.model'].use, 'linear_classifier');
  console.log('flattenTree: ok');
}

// --- run テーブル -------------------------------------------------------
{
  const rows = [
    {
      path: 'cifar100/20260913-1_a_baseline', kind: 'run', name: 'baseline', id: 'A',
      status: 'finished', created_at: '2026-09-13T09:00:00+09:00', duration_sec: 12,
      summary: { acc: 0.9 },
      config: { run: 'standard_trainer@db83d7b3', model: 'linear_classifier@v1', 'model.k': 2 },
    },
    {
      path: 'cifar100/cv5', kind: 'group', name: 'cv5', id: 'G',
      status: '', created_at: '2026-09-13T09:10:00+09:00', duration_sec: null,
      summary: {}, config: {}, n_runs: 2,
      aggregate: { acc: { n: 2, mean: 0.85, std: 0.05, min: 0.8, max: 0.9 } },
    },
    {
      path: 'cifar100/cv5/20260913-2_b_fold0', kind: 'run', name: 'fold0', id: 'B',
      status: 'running', created_at: '2026-09-13T09:11:00+09:00', duration_sec: null,
      summary: { acc: 0.8 },
      config: { run: 'standard_trainer@db83d7b3', model: 'mlp_classifier@2c2621ec' },
    },
  ];

  const table = nodeTable(rows, { prefKey: 'test-fold' });
  let text = tokens(table);

  assert.ok(text.includes('baseline') && text.includes('cv5'), 'run と group が出る');
  assert.ok(!text.includes('fold0'), 'group は既定で畳まれている');
  assert.ok(text.includes('linear_classifier@v1'), '構成が列になる');
  assert.ok(text.includes('2 runs'), 'group は run 数を出す');
  assert.ok(text.includes('± '), 'group の集計に ± が出る');

  function find(node, className) {
    if (!node) return null;
    if (String(node.className || '').split(/\s+/).includes(className)) return node;
    for (const child of node.children || []) {
      const hit = find(child, className);
      if (hit) return hit;
    }
    return null;
  }

  const twisty = find(table, 'twisty');
  assert.ok(twisty, '畳んだ group には開閉ボタンがある');
  twisty.listeners.click[0]();
  text = tokens(table);
  assert.ok(text.includes('fold0'), '開くと group 配下の run が折りたたみ行として出る');
  assert.ok(text.includes('.status.status-running'), '開いた子 run の状態が出る');

  console.log('table: ok');
}

// --- 名前の絞り込み（summary / metrics / 列が数百になるとき） -------------
{
  const { nameMatcher, filteredTable } = await import('../../blab/static/js/filter.js');
  const names = ['test/accuracy', 'test/classwise/auc_DLBCL', 'train/classwise/auc_DLBCL', 'val/auc'];

  assert.deepEqual(names.filter(nameMatcher('')), names, '空なら全部通す');
  assert.deepEqual(
    names.filter(nameMatcher('AUC dlbcl')),
    ['test/classwise/auc_DLBCL', 'train/classwise/auc_DLBCL'],
    '空白区切りは AND、大文字小文字は区別しない',
  );
  // auc は a-cc-u-ra-c-y と順に拾えるので test/accuracy にも一致する。
  assert.deepEqual(names.filter(nameMatcher('auc -train')), ['test/accuracy', 'test/classwise/auc_DLBCL', 'val/auc'], '-語 は除外');
  assert.deepEqual(names.filter(nameMatcher('  -  ')), names, '- だけの語は無視する');
  assert.deepEqual(names.filter(nameMatcher('traiauc')), ['train/classwise/auc_DLBCL'], '連続していなくても文字の順に拾う');
  assert.deepEqual(names.filter(nameMatcher('tac')).length, 3, '文字が順に出てくれば一致する');
  assert.deepEqual(names.filter(nameMatcher('cat')), [], '順番が逆なら一致しない');
  assert.deepEqual(names.filter(nameMatcher('cauq')), [], '無い文字があれば一致しない');
  assert.deepEqual(
    names.filter(nameMatcher('auc -tst')),
    names,
    '除外は連続した部分文字列だけ（散らばった文字では除かない）',
  );
  assert.ok(!nameMatcher('ab')(['xa', 'bx']), '欄をまたいで文字を拾わない');
  assert.ok(nameMatcher('ab')(['xa', 'axb']), 'どれか 1 つの欄で一致すればよい');

  // サブシーケンスはゆるいので、一致度の高い順に並べる。
  const { rankByQuery } = await import('../../blab/static/js/filter.js');
  const metricNames = ['lr', 'train/accuracy', 'train/balanced_accuracy', 'train/classwise/auc_CHL'];
  assert.deepEqual(rankByQuery(metricNames, ''), metricNames, '語が無ければ元の順');
  assert.equal(rankByQuery(metricNames, 'traiacc')[0], 'train/accuracy', '区切りの直後・連続の一致が上に来る');
  assert.equal(rankByQuery(metricNames, 'lr')[0], 'lr', '散らばった一致より、ひと続きの一致が上');
  assert.deepEqual(rankByQuery(['b/x', 'a/x'], 'x'), ['b/x', 'a/x'], '同点は元の順を保つ');

  function findTag(node, tag) {
    if (!node || node.nodeType !== 1) return null;
    if (node.tagName === tag) return node;
    for (const child of node.children) {
      const hit = findTag(child, tag);
      if (hit) return hit;
    }
    return null;
  }
  const row = (k) => {
    const tr = document.createElement('tr');
    tr.textContent = k;
    return tr;
  };

  const table = filteredTable({ keys: names, head: ['名前', '値'], prefKey: 'test.filter', row });
  assert.ok(tokens(table).includes('4 件'), '件数が出る');
  const input = findTag(table, 'INPUT');
  input.value = 'val';
  input.listeners.input[0]();
  const text = tokens(table);
  assert.ok(text.includes('val/auc') && !text.includes('test/accuracy'), '入力で行が絞られる');
  assert.ok(text.includes('1 / 4 件'), '絞り込み後の件数が出る');

  // 作り直されても（実行中の定期更新）、入力は残る。
  const again = filteredTable({ keys: names, prefKey: 'test.filter', row });
  assert.ok(tokens(again).includes('1 / 4 件'), '絞り込み文字列が prefs から復元される');

  console.log('filter: ok');
}

console.log('\nすべて通りました');

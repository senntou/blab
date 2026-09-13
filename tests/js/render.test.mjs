// 構成ツリーと run テーブルの描画テスト。
// 「引数の出どころを色で区別する」「記録できなかったものを黙って省略しない」といった
// 設計上の約束が、実際に DOM に出ているかを見る。

import assert from 'node:assert/strict';
import { flatten } from './dom-shim.mjs';

const { configTree, flattenTree } = await import('../../blab/static/js/config-tree.js');
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

// --- 構成ツリー ---------------------------------------------------------
{
  const text = tokens(configTree(RESOLVED));

  assert.ok(text.includes('standard_trainer'), 'root component が出る');
  assert.ok(text.includes('two_moons'), '子 component が出る');
  assert.ok(text.includes('@v1'), 'ラベルがあれば版はラベルで出る');
  assert.ok(text.includes('@415672ac'), 'ラベルが無ければ短いハッシュで出る');

  // 引数の出どころを色（クラス）で区別する。
  for (const origin of ['yaml', 'override', 'runtime', 'default']) {
    assert.ok(text.includes(`.arg-${origin}`), `arg-${origin} の行がある`);
    assert.ok(text.includes(`.origin.origin-${origin}`), `origin-${origin} のバッジがある`);
  }

  // 記録できなかったものを黙って省略しない。
  assert.ok(text.includes('記録なし (Adam)'), '$unrecorded を出す');
  assert.ok(text.includes('.chip.chip-ref'), '$ref を参照チップで出す');
  assert.ok(text.includes('dataset#0'), '$ref の参照先を出す');

  // 宣言されたのに build されなかった子。
  assert.ok(text.includes('一度も build されませんでした'), '未使用の子を明示する');

  // --set の記録。
  assert.ok(text.includes('run.lr=0.0003'), 'overrides を出す');

  // 同じ Builder から 2 回 build した dataset。
  assert.ok(text.includes('build #0') && text.includes('build #1'), '複数 build を並べる');

  console.log('config-tree: ok');
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

  const text = tokens(nodeTable(rows, { prefKey: 'test' }));

  assert.ok(text.includes('baseline') && text.includes('cv5'), 'run と group が出る');
  assert.ok(text.includes('fold0'), 'group 配下の run が折りたたみ行として出る');
  assert.ok(text.includes('linear_classifier@v1'), '構成が列になる');
  assert.ok(text.includes('.status.status-running'), '状態が出る');
  assert.ok(text.includes('2 runs'), 'group は run 数を出す');
  assert.ok(text.includes('± '), 'group の集計に ± が出る');

  console.log('table: ok');
}

console.log('\nすべて通りました');

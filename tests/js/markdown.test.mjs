// Markdown の描画テスト（ノードのドキュメントと component の README）。
// LLM や人間が書いた説明を UI だけで読めるか、危ないリンクを辿らせないか、
// 数式が KaTeX に渡る形になっているかを見る。

import assert from 'node:assert/strict';
import './dom-shim.mjs';

const { markdownBlock } = await import('../../blab/static/js/markdown.js');

function all(node, pred, out = []) {
  if (!node || node.nodeType !== 1) return out;
  if (pred(node)) out.push(node);
  for (const child of node.children) all(child, pred, out);
  return out;
}
const byTag = (node, tag) => all(node, (n) => n.tagName === tag.toUpperCase());
const byClass = (node, cls) => all(node, (n) => String(n.className).split(/\s+/).includes(cls));
const text = (node) => node.textContent;

// ---------------------------------------------------------------- ブロック
{
  const md = markdownBlock([
    '# 目的',
    '',
    'lr を変えて *収束の速さ* と **最終精度** を見る。~~旧案~~',
    '',
    '## 手順',
    '',
    '1. ベースライン',
    '2. 変更',
    '   - 子項目 A',
    '   - 子項目 B',
    '',
    '- [x] 実装',
    '- [ ] 評価',
    '',
    '---',
    '',
    '> [!NOTE]',
    '> 引用の中も **強調** できる',
    '',
    '| metric | 値 |',
    '|:--|--:|',
    '| `val|acc` | 0.91 |',
    '',
    '```bash',
    'blab run experiments/a.yaml',
    '```',
    '',
    '<!-- 書き手のメモ -->',
    '最後の段落',
  ].join('\n'));

  assert.equal(byTag(md, 'h2')[0].dataset.anchor, '目的', '見出しにアンカーが付く');
  assert.equal(text(byTag(md, 'em')[0]), '収束の速さ');
  assert.equal(text(byTag(md, 'strong')[0]), '最終精度');
  assert.equal(text(byTag(md, 'del')[0]), '旧案');

  const ol = byTag(md, 'ol')[0];
  assert.equal(ol.children.length, 2, '番号付きリストは 2 項目');
  assert.equal(byTag(ol, 'ul')[0].children.length, 2, '字下げした項目は入れ子のリストになる');

  const tasks = byClass(md, 'task');
  assert.equal(tasks.length, 2, 'タスクリスト');
  assert.ok('checked' in byTag(tasks[0], 'input')[0].attributes, '[x] はチェック済み');
  assert.ok(!('checked' in byTag(tasks[1], 'input')[0].attributes), '[ ] は未チェック');

  assert.equal(byTag(md, 'hr').length, 1);
  assert.ok(byClass(md, 'md-alert-note').length === 1, 'GitHub の [!NOTE] は注記になる');

  const cells = byTag(md, 'td');
  assert.equal(text(cells[0]), 'val|acc', 'コード中の | ではセルを分けない');
  assert.equal(cells[1].attributes.style, 'text-align:right', '列の揃えを反映する');

  assert.ok(byClass(md, 'code-block').length === 1, 'コードフェンス');
  assert.ok(!JSON.stringify(text(md)).includes('書き手のメモ'), 'HTML コメントは描画しない');
  assert.equal(text(byTag(md, 'p').at(-1)), '最後の段落');
  console.log('blocks: ok');
}

// ---------------------------------------------------------------- 数式
{
  const md = markdownBlock([
    '損失は $L = -\\sum_i y_i \\log p_i$ で、$x$ も 1 文字で書ける。',
    '費用は $5 と $10 で、これは数式ではない。\\$ はエスケープ。',
    '',
    '$$',
    '\\mathrm{acc} = \\frac{TP + TN}{N}',
    '$$',
    '',
    '$$ a^2 + b^2 = c^2 $$',
    '',
    '```math',
    'E = mc^2',
    '```',
    '',
    '括弧形式 \\(\\alpha\\) と \\[\\beta\\]',
    '',
    'snake_case_name と `$not math$` はそのまま',
  ].join('\n'));

  const inline = byClass(md, 'math-inline');
  assert.deepEqual(
    inline.map((n) => n.dataset.tex),
    ['L = -\\sum_i y_i \\log p_i', 'x', '\\alpha'],
    'インライン数式（通貨の $ は拾わない）',
  );
  const display = byClass(md, 'math-display');
  assert.deepEqual(
    display.map((n) => n.dataset.tex),
    ['\\mathrm{acc} = \\frac{TP + TN}{N}', 'a^2 + b^2 = c^2', 'E = mc^2', '\\beta'],
    'ディスプレイ数式（$$ ブロック / 1 行 / ```math / \\[ \\]）',
  );
  assert.ok(text(md).includes('$5 と $10'), '通貨はそのまま');
  assert.ok(text(md).includes('snake_case_name'), 'snake_case を強調にしない');
  assert.ok(byTag(md, 'code').some((c) => text(c) === '$not math$'), 'コード中の $ は数式にしない');
  console.log('math: ok');
}

// ---------------------------------------------------------------- リンクと画像
{
  const opened = [];
  const md = markdownBlock([
    '![混同行列](artifacts/cm.png) [結果](report.md#考察) [外](https://example.com)',
    '[危ない](javascript:alert(1)) [上](../group.md) [見出し](#目的) <https://a.example/x>',
    '本文中の https://b.example/y も辿れる。',
  ].join('\n'), { base: 'cifar100/cv5/run1', onDocLink: (name, anchor) => opened.push([name, anchor]) });

  const img = byTag(md, 'img')[0];
  assert.equal(img.attributes.src, '/files/cifar100/cv5/run1/artifacts/cm.png', '相対パスの画像はノードのファイルとして解決する');

  const links = byTag(md, 'a').filter((a) => !String(a.className).includes('md-image'));
  const hrefs = links.map((a) => a.attributes.href);
  assert.ok(hrefs.includes('https://example.com'));
  assert.ok(hrefs.includes('/files/cifar100/cv5/group.md'), '.. を畳んで解決する');
  assert.ok(hrefs.includes('https://a.example/x'), '<URL> の自動リンク');
  assert.ok(hrefs.includes('https://b.example/y'), '本文中の URL');
  assert.ok(!hrefs.some((h) => String(h).startsWith('javascript')), 'javascript: は辿らせない');
  assert.equal(byClass(md, 'md-link-disabled').length, 1, '辿らせないリンクは文字だけ残す');

  const sibling = links.find((a) => text(a) === '結果');
  sibling.listeners.click[0]({ preventDefault() {} });
  assert.deepEqual(opened, [['report.md', '考察']], '同じノードの .md はその場で切り替える');
  console.log('links: ok');
}

// ---------------------------------------------------------------- base なし（component の README）
{
  const md = markdownBlock('```\nx = 1\n```\n![a](img.png)');
  assert.ok(byClass(md, 'tok-number').length >= 1, '言語指定の無いフェンスは従来どおり Python として色付け');
  assert.equal(byTag(md, 'img')[0].attributes.src, 'img.png', 'base が無ければ相対パスはそのまま');
  console.log('readme: ok');
}

console.log('\nすべて通りました');

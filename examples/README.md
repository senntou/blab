# blab のサンプルプロジェクト

このディレクトリ自体が 1 つの blab プロジェクト（`blab.json` がある）である。
**標準ライブラリだけで動く** two moons の 2 値分類で、blab の主な機能を一通り使っている。

```
examples/
├── blab.json                      プロジェクト ID
├── components/                    レジストリ（git 管理下）
│   ├── standard_trainer/            root component。execute(run) を持つ
│   │   ├── main.py
│   │   └── loops.py                 ディレクトリ内は相対 import 自由
│   ├── two_moons/                   dataset
│   ├── linear_classifier/           model
│   ├── mlp_classifier/              model（差し替え用）
│   │   ├── main.py
│   │   └── layers.py
│   └── accuracy/                    metric
└── experiments/
    ├── baseline.yaml
    ├── mlp.yaml                     baseline と model だけ違う
    └── noisy.yaml                   --set で振るためのもの
```

## 触ってみる

```bash
cd examples

blab check experiments/baseline.yaml    # 実行せずに構成を検査する
blab run   experiments/baseline.yaml    # 事前検証 → 凍結 → 実行
blab run   experiments/mlp.yaml         # model だけ違う構成
blab ls                                 # run と group の一覧
blab components                         # component の一覧（作業コピーのハッシュ付き）
```

記録された run を覗く。

```bash
blab show runs/two-moons/2026*_baseline/
```

`resolved.yaml` に `k: 2` が `runtime` 由来として入っている。**YAML のどこにも
書かれていないのに記録されている** — dataset を作って初めて分かる値だからである。

`blab check` は**学習を 1 秒でも始める前に**、参照された component が全部あるか、
import できるか、引数名が合っているかを見る。試しに壊してみるとよい。

```bash
# 引数名を間違える → 「もしかして 'epochs'?」
sed -i 's/epochs:/epocs:/' experiments/baseline.yaml && blab check experiments/baseline.yaml
```

## この例が見せていること

**trainer も component である。** `standard_trainer/` がハッシュで凍結されるので、
学習ループのコードも run から復元できる。

**子は Builder として渡る。** `standard_trainer` の `__init__` が受け取る `dataset` は
dataset ではなく、dataset の**作り方**である。

```python
train = self.dataset.build(split="train")      # YAML の引数と合体して実体化
val   = self.dataset.build(split="val")        # 同じ Builder から 2 個作れる
model = self.model.build(k=train.n_classes)    # 実行時にしか分からない値を渡す
```

`k`（クラス数）は YAML のどこにも書かれていない。dataset を作るまで分からないからで、
それでも記録には `k: 2` が `runtime` 由来として残る。**「YAML にそう書いてある」と
「実際にそうである」がズレない**。

**構成の差し替えが YAML の 1 か所で済む。** `baseline.yaml` と `mlp.yaml` の違いは
`model:` だけで、UI の比較ビューはこれを「構成の diff」として見せる。

**`--set` は値だけを振る。**

```bash
for s in 0.1 0.2 0.3; do
  blab run experiments/noisy.yaml --group noise-sweep --set run.dataset.noise=$s --name n$s
done
```

component の差し替え（`--set run.model.use=mlp_classifier`）は**わざと禁止**してある。
許すと YAML を書かずに実験できてしまい、「実験の定義が git log に残る」という利点が
薄れるため。

group の集計はファイルに書かれない。`blab ls` が読むときに導出する。

```
  noise-sweep/  (3 runs)  val/acc=0.8756 ± 0.0142 (n=3)
```

**ラベルを貼っても、その component のハッシュは変わらない。**

```bash
blab tag linear_classifier v1 --note "初版"
blab components          # ハッシュはそのまま。@v1 が付くだけ
blab diff linear_classifier@v1 linear_classifier
```

**過去 run は、その当時のコードでそのまま再実行できる。**

```bash
$EDITOR components/linear_classifier/main.py   # 作業コピーをいじってから
blab run runs/two-moons/2026*_baseline/        # 焼き込まれた当時のコードで動く
```

run に焼き込まれた `components/` を書き換えてから再実行すると、ハッシュが合わないので
止まる。記録と実体が食い違ったまま動くことはない。

## UI で見る

`blab ui` を実行して http://127.0.0.1:8420 を開くと、構成ツリーや比較ビューが見られる（`blab[ui]` のインストールが必要）。

# blab

機械学習の実験を YAML で組み立てて実行し、使ったコード・引数・結果を run ごとの
ディレクトリに保存するローカル実験管理ツール。

```bash
blab run experiments/baseline.yaml
```

## 必要なもの

- Python 3.10 以上
- Linux で開発・テストしている（macOS / Windows は未確認）

実行に必要な依存は PyYAML のみ。UI（`blab ui`）を使う場合は FastAPI と uvicorn が入る。

## インストール

blab は component（学習コードの部品）を自身と**同じ Python プロセス**で import して実行する。
そのため、torch などの学習コードの依存と**同じ環境**にインストールする。

```bash
# uv
uv add "blab[ui] @ git+https://github.com/senntou/blab.git"

# pip
pip install "blab[ui] @ git+https://github.com/senntou/blab.git"
```

`[ui]` は `blab ui` を使わないなら不要。`pipx` や `uv tool install` のように隔離された環境に
入れると、component から学習コードの依存を import できない。

## クイックスタート

### 1. プロジェクトを作る

git リポジトリのルートで実行する。

```bash
blab init
```

```
my-research/
├── blab.json      プロジェクト ID と設定（コミットする）
├── components/    component を置く（コミットする）
├── experiments/   実験 YAML を置く（コミットする）
└── .gitignore     .blab/ と runs/ と blab.local.json が追記される
```

### 2. component を書く

component は `components/<id>/` のディレクトリ 1 つで、`main.py` の中の 1 つに `@blab.entry` を付ける。
`blab new <id>` で雛形ができる。

```bash
blab new two_moons
blab new linear_classifier
blab new standard_trainer
```

```python
# components/linear_classifier/main.py
import blab


@blab.entry
class LinearClassifier:
    def __init__(self, k: int, lr: float = 0.1):
        ...
```

YAML の root に置いた component は `execute(run)` が呼ばれる。子の component は
`blab.Builder` として渡され、`.build()` を呼んだ時点で実体化される。

```python
# components/standard_trainer/main.py
import blab


@blab.entry
class StandardTrainer:
    def __init__(self, dataset, model, epochs=20, seed=0):
        self.dataset, self.model = dataset, model   # どちらも blab.Builder
        self.epochs, self.seed = epochs, seed

    def execute(self, run):
        train = self.dataset.build(split="train")
        val = self.dataset.build(split="val")
        model = self.model.build(k=train.n_classes)  # 実行時に決まる値を渡す

        for epoch in range(self.epochs):
            loss = model.fit_one_epoch(train)
            run.log({"train/loss": loss}, epoch=epoch)

        run.log_summary({"val/acc": model.evaluate(val)})
```

### 3. 実験 YAML を書く

```yaml
# experiments/baseline.yaml
experiment: two-moons
name: baseline

run:
  use: standard_trainer    # use を持つマッピングが component 参照
  epochs: 30               # 残りのキーはその component の引数
  dataset: {use: two_moons, noise: 0.2}
  model: {use: linear_classifier, lr: 0.1}
```

### 4. 検査して実行する

```bash
blab check experiments/baseline.yaml   # 実行せずに検査だけする
blab run   experiments/baseline.yaml
```

`blab run` は実行前に、component が存在するか、import できるか、YAML の引数名が entry の
引数と合っているか、参照したデータファイルがあるかを検査し、問題があれば実行しない。

結果は `runs/two-moons/<日時>_<ID>_baseline/` に保存される。

### 5. 結果を見る

```bash
blab ls    # run / group の一覧
blab ui    # http://127.0.0.1:8420
```

標準ライブラリだけで動くサンプルプロジェクトが [examples/](examples/) にある。

```bash
cd examples
blab run experiments/baseline.yaml
```

## よく使う操作

```bash
# YAML の値を 1 か所だけ変えて実行する
blab run experiments/baseline.yaml --set run.model.lr=0.01 --name lr0.01

# 5-fold CV。同じ group 名の run が 1 つにまとまり、mean ± std が表示される
for f in 0 1 2 3 4; do
  blab run experiments/cv.yaml --group cv5 --set run.dataset.fold=$f --name fold$f
done

# 過去の run を、そのとき使ったコードで再実行する
blab run runs/two-moons/20260913-063012_a1b2_baseline/

# component の今の版にラベルを付け、版の差分を見る
blab tag linear_classifier v1 --note "初版"
blab diff linear_classifier@v1 linear_classifier
```

## 保存されるもの

```
runs/two-moons/20260913-063012_a1b2_baseline/
├── meta.json        状態、開始・終了時刻、実行時間
├── resolved.yaml    構成。各 component のハッシュと、実際に渡された引数
├── components/      使った component のソースのコピー
├── env.json         Python、パッケージの版、git commit、GPU
├── data.json        {data: ...} で参照した外部ファイルの記録
├── metrics.jsonl    run.log() の値
├── summary.json     run.log_summary() の値
├── artifacts/       run.log_artifact() でコピーしたファイル
└── logs/            stdout.log / stderr.log
```

## できること・できないこと

できること:

- `resolved.yaml` には YAML に書いた値に加えて、コードから `.build()` に渡した値も記録され、
  それぞれの由来（YAML / `--set` / 実行時 / デフォルト値）が区別される
- 使った component のソースが run にコピーされるので、component を編集・削除したあとでも
  `blab run <run のパス>` で当時のコードのまま再実行できる
- component は内容のハッシュで版が区別される。編集して実行するだけで新しい版として記録され、
  登録の操作は要らない
- UI で、run 同士の構成の差分と、版が違う component のソースの差分を見られる
- 保存形式は JSON / JSONL / YAML のファイルだけで、データベースを使わない

できないこと:

- 実行環境の再現。パッケージの版は `env.json` に記録するが、環境を揃える機能は無い
- 数値の完全な再現。乱数シードや CUDA の非決定性の扱いは学習コード側で行う
- ハイパーパラメータ探索。値を振るときはシェルのループなどで `blab run` を複数回呼ぶ
- 既存のスクリプトに記録 API だけを組み込む使い方。実行は `blab run` 経由のみ
- 複数ユーザでのサーバ運用。UI に認証は無く、既定では 127.0.0.1 でのみ待ち受ける

## ドキュメント

| | |
| --- | --- |
| [推奨する構成と運用](docs/best-practices.md) | プロジェクトの構成、component の分け方、CV やデータの扱い |
| [component](docs/components.md) | component の書き方、Builder、記録 API、ラベル |
| [実験 YAML と実行](docs/experiments.md) | YAML の書式、事前検証、group / CV、外部ファイル、再実行 |
| [UI](docs/ui.md) | 画面と操作 |
| [CLI](docs/cli.md) | コマンドと環境変数の一覧 |
| [保存形式](docs/layout.md) | ディレクトリとファイル形式の仕様 |

## 開発

```bash
git clone https://github.com/senntou/blab.git
cd blab
uv sync --extra ui --extra dev
uv run pytest
```

`node` があればフロントエンドの描画テストも実行される。

## ライセンス

[MIT](LICENSE)

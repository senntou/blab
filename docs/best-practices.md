# 推奨する構成と運用

blab が強制するものではないが、使ううえで勧める構成と運用をまとめる。

## プロジェクトの構成

```
my-research/                 git リポジトリ = blab プロジェクト
├── pyproject.toml           依存パッケージ
├── uv.lock                  あると env.json にハッシュが記録される
├── blab.json
├── blab.local.json          マシンごとの設定（コミットしない）
├── src/
│   └── mylib/               複数の component で共有するコード
├── components/
│   ├── cifar100/            dataset
│   ├── resnet18/            model
│   ├── top1_accuracy/       metric
│   └── standard_trainer/    trainer
├── experiments/
│   ├── baseline.yaml
│   └── distill.yaml
├── scripts/                 CV や sweep を回すシェルスクリプト
└── runs/                    実行結果（コミットしない。別の場所に置いてもよい）
```

- 1 つの git リポジトリに 1 つの blab プロジェクトを置き、`blab.json` はリポジトリのルートに置く
- 依存は `pyproject.toml` とロックファイルで管理する。プロジェクトルートに `uv.lock` があると、
  そのハッシュが `env.json` の `packages_hash` に記録され、run 同士で依存が同じだったかを比べられる。
  無い場合は import されているパッケージの版が記録される
- blab 自体もこのプロジェクトの依存としてインストールする（component は blab と同じプロセスで動く）

### コミットするもの

| パス | コミット | |
| --- | --- | --- |
| `blab.json` | する | |
| `components/`（`.meta/` と `.frozen/` を含む） | する | |
| `experiments/` | する | 実験の定義の変更履歴が git に残る |
| `blab.local.json` | しない | データのパスなど、マシン固有の設定 |
| `.blab/` | しない | キャッシュ |
| `runs/` | しない | |

コミットしないものは `blab init` が `.gitignore` に追記する。

## component の分け方

- **YAML で差し替えたい単位**を 1 つの component にする。典型的には dataset / model / metric / trainer
- 差し替える予定の無い補助コード（レイヤの定義、学習ループの部品など）は、使う component の
  ディレクトリに別ファイルとして置き、相対 import する
- 複数の component で使うコードは、プロジェクト内のパッケージ（上の `src/mylib/`）にして
  editable install し、普通に import する
- component の中で他の component を import しない。子として使う場合は YAML に書いて Builder で受け取る
- 各 component に `README.ja.md`（または `README.md`）を書く。UI の component 詳細と `blab show` に表示される

共有パッケージのコードは run にコピーされない。run に残るのは `env.json` の git commit
（未コミットの変更があったかを含む）とパッケージの版だけである。結果に影響するコードを頻繁に
変えるなら、component の中に置いたほうが run から当時のコードを追える。

## trainer の書き方

- 子 component は `__init__` で受け取って保持するだけにし、`execute` の中で `.build()` する
- クラス数や入力次元のように、他の component を作ってから決まる値は YAML に書かず、
  `.build(k=...)` で渡す。YAML に書くと、dataset を変えたときに書き換え漏れが起きる
- 乱数シードは trainer の引数にして、`execute` の最初で設定する。blab はシードを設定しない
- metric 名は `train/loss`、`val/acc` のように `/` で区切る（`.` は使えない）
- 比較したい最終的な値は `run.log_summary()` で記録する。UI の run 一覧の列と group の集計は
  `summary.json` から作られる
- 進捗バーやデバッガを使うときは `blab run --no-capture` で実行する

## 実験 YAML

- 比較の基準にする実験（baseline など）では、component を `use: resnet18@v1` のようにラベルで
  固定しておくと、作業コピーを編集しても構成が変わらない
- 値だけを変えるときは `--set` を使い、component を差し替えるときは YAML を分ける
  （`--set` では component を差し替えられない）
- `experiment` はデータセットや課題の単位、`group` は CV や sweep の単位にすると、UI の一覧と
  集計がそのまま使える

## CV と sweep

1 回の `blab run` が 1 つの run になる。CV や sweep は、シェルのループやジョブスケジューラから
`blab run` を複数回呼ぶ。

```bash
# scripts/cv5_lr3e-4.sh
for f in 0 1 2 3 4; do
  blab run experiments/cv.yaml --group cv5-lr3e-4 \
    --set run.lr=3e-4 --set run.dataset.fold=$f --name fold$f
done
```

```bash
# Slurm に投げる場合
for f in 0 1 2 3 4; do
  sbatch --wrap "blab run experiments/cv.yaml --group cv5 --set run.dataset.fold=$f --name fold$f"
done
```

- group 名には条件（学習率など）を入れ、条件ごとに別の group にする
- 同じ group 名の run を並列に実行してよい
- group の集計は表示のたびに、正常終了した run の `summary.json` から計算される。失敗した fold は
  集計に含まれないので、その fold だけを回し直せばよい

## データ

- データのパスは YAML に直接書かず、`{data: NAME}` で論理名を書き、実パスは `blab.local.json` に書く。
  YAML にマシン固有のパスが入らない
- component のコードの中にパスを直接書くと、どのファイルを使ったかが記録されない
- fold の分割や正解ラベルのような小さいファイル（1 MB 未満）は run にコピーされる。
  分割を毎回コードで生成するより、ファイルにして `{data: ...}` で渡すと run に中身が残る

詳細は [外部ファイル](experiments.md#外部ファイル)。

## 保存先

- 大きいディスクに置く場合は `blab.local.json` に `runs_dir` を書く。`runs_dir` はプロジェクトごとに
  別のディレクトリにする（blab はプロジェクト名のサブディレクトリを作らない）
- ジョブスケジューラで実行するときは環境変数 `BLAB_RUNS` でも指定できる
- 動作確認のための実行は `--runs-dir` で一時ディレクトリに出すと、通常の一覧に混ざらない

```bash
blab run experiments/baseline.yaml --set run.epochs=1 --runs-dir /tmp/blab-smoke
```

## ラベル

- 報告に使う版や、baseline として固定したい版に `blab tag` でラベルを付ける。
  ラベルを付けた版だけが `components/.frozen/` に入り、git で管理される
- 後から結果が良かったと分かった版には、その run を指定して `blab tag <id> <label> --from-run <run>` で付けられる
- ラベルは付け替えられないので、変更したら `v2`, `v3` のように新しい名前を付ける

## run の整理

- group を付け忘れた・間違えた run は `blab mv <run> --group <name>` で移す
- UI の「削除」は run を `_trash` group に移すだけで、ファイルは残る。完全に消すときは `blab rm <run>`
- `.blab/` はキャッシュで、git には入れない

## 注意点

- component 内で定義したクラスのオブジェクトを、start method が `spawn` / `forkserver` の子プロセスに
  渡すと（例: `DataLoader(num_workers>0)`）、子プロセスで component のモジュールを import できず
  エラーになる。`fork` では起きない。Linux では Python 3.13 までは `fork` が既定だが、3.14 からは
  `forkserver` が既定なので、必要なら `multiprocessing_context="fork"` を指定する
- README だけを変更しても component のハッシュは変わり、新しい版として記録される

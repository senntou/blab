# blab

**実験を、再現できるコードの木として記録する**ローカル実験管理ツール。

既存の実験管理ツールは**値**（params / metrics）を記録するが、**その実験が何で組まれて
いたのか**は git commit という不透明な塊に丸投げしている。理論上は再現できるが、半年後に
`a1b2c3d` を checkout して `models/` を読む人はいない。

blab は実験の**構成そのもの**を記録の中心に据える。run から**ワンクリックで構成要素の
コードと日本語の説明に辿れる**こと、そして逆に**その部品を使った全 run** を引けること。
さらに、run のディレクトリさえあれば**そのとき動いたコードを完全に復元して再実行できる**こと。

```bash
blab run experiments/distill.yaml
```

実験は YAML で宣言し、blab が組み立てて実行する。**学習を 1 秒でも始める前に**、参照された
部品が全部存在するか、import できるか、引数名が合っているかを検査する。8 時間学習してから
タイポに気づく、ということが起きない。

> 設計の詳細は [docs/design.md](docs/design.md)、ディレクトリ仕様は
> [docs/layout.md](docs/layout.md)。

## 原則

**「沈黙による嘘をしない」** — blab の全機構を貫く原則。捕まえられない情報があること自体は
許容するが、**捕まえられなかったことを黙って隠さない**。記録できなかった引数、解決できな
かったデータ、取得できなかった環境情報は、すべて理由付きで UI に出る。

| | |
| --- | --- |
| 約束する | **コードの再現** — run から、実際に動いたコードを完全に復元できる |
| 約束する | 表示している情報が、**実際に動いたもの**であること |
| 約束しない | 実行環境の再現（パッケージ版は**記録するが**、揃えるのはあなたの責任） |
| 約束しない | 数値レベルの一致（CUDA の非決定性・乱数の扱いはプログラム側の問題） |

DB を持たない。ファイルシステムが唯一の真実なので、保存済みディレクトリは `rsync` / `scp` で
そのまま持ち運べる。

## インストール

```bash
uv pip install -e '.[ui]'   # UI 込み。実行層だけなら extras 不要
```

## プロジェクトを作る

**1 git リポジトリ = 1 プロジェクト**である。

```bash
cd ~/work/cifar-research
blab init
```

`blab.json`（プロジェクト ID）を作り、`.gitignore` を書き、blab のグローバル索引に登録する。
以後どのディレクトリからでも、このプロジェクトの部品を id で参照できる。

```
cifar-research/
├── blab.json          ★ プロジェクト ID
├── components/        ★ 部品（あなたが編集する）
├── experiments/       ★ 実験の YAML
├── blab.local.json    ✗ このマシンだけの設定
├── .blab/             ✗ キャッシュ。いつ消しても安全
└── runs/              ✗ 実験ログ
```

★ = git 管理下 / ✗ = `.gitignore`。**実験ログはコードと別扱い**で、置き場所も移せる。

## component — 実験の構成要素

model / dataset / metric / augmentation、そして **trainer も** component である。blab は
種類を区別しない。

```bash
blab new resnet18
$EDITOR components/resnet18/main.py
```

component は **1 ディレクトリ**。中は普通の Python パッケージなので、相対 import で分割できる。

```
components/
├── resnet18/          # ← ここがコンポーネント本体。丸ごと凍結される
│   ├── README.ja.md   #     人が書く説明
│   ├── main.py        #     @blab.entry はここ
│   └── blocks.py      #     相対 import で使える
└── .meta/             # ← ラベル・タグ。blab が管理する
    └── resnet18.json
```

ラベルを本体の中に置かないのは、**ラベルを貼る行為がそのコンポーネントのハッシュを
変えてしまう**ためである（コードを 1 行も変えていないのに別の版になる）。

```python
# components/resnet18/main.py
import torch.nn as nn
import blab

from .blocks import BasicBlock


@blab.entry                                # 入口はちょうど 1 つ
class Resnet18(nn.Module):
    def __init__(self, k: int, pretrained: bool = False):
        super().__init__()
        self.layers = nn.Sequential(*[BasicBlock(64) for _ in range(8)])
        self.head = nn.Linear(512, k)

    def forward(self, x):
        return self.head(self.layers(x))
```

規則は 2 つだけ。

| | |
| --- | --- |
| ディレクトリ内で自己完結する | 外への import はサードパーティのパッケージ（**あなた自身の pip パッケージを含む**）のみ |
| 入口は `main.py` の `@blab.entry` で 1 つ | `blab run` が実行前に検査する |

component をまたいで共有したいコードは、レジストリに入れず自分の pip パッケージ
（`pip install -e`）へ。安定して育つフレームワーク的なコードは blab に入れるものではない。

## 実験 YAML

```yaml
# experiments/distill.yaml
experiment: cifar100
name: distill

run:
  use: distill_trainer          # "use" があれば component 参照
  epochs: 100                   # 残りは全部その引数
  lr: 0.0003
  seed: 0

  dataset:                      # 引数の位置に、そのまま子を書く
    use: cifar100
    augment: randaug

  student:
    use: resnet18
    pretrained: false

  teacher:
    use: resnet50@v3            # ラベルで版を固定できる

  metric: {use: top1_accuracy}  # 引数が無くても {use: ...} で書く

  transforms:                   # リストも書ける
    - {use: random_flip, p: 0.5}
    - {use: randaug, n: 2}
```

規則は 1 つ。**`use` キーを持つマッピングは component 参照、それ以外は普通の値。**
文字列単体の省略形（`metric: top1_accuracy`）は認めない。認めると `optimizer: adam` の
ような普通の文字列引数と区別できず、**component を新規作成した瞬間に既存 YAML の意味が
黙って変わる**ことになるためである。

参照は 4 通り書ける。

```yaml
use: resnet18                   # 今の作業コピー（実行時に自動で凍結される）
use: resnet18@v3                # ラベルで固定
use: resnet18@sha256:3f9a1c     # ハッシュで完全固定
use: seg-baseline/unet@v2       # 別プロジェクトの component
```

**YAML に制御構文は無い。** 参照（`${...}`）も、四則演算も、条件分岐もループも持たない。
分岐やループが欲しくなったら、それは component の Python に書く。

## trainer を書く

root の component だけが特別で、blab はそれを実体化して `execute(run)` を呼ぶ。

```python
# components/standard_trainer/main.py
import torch
import blab


@blab.entry
class StandardTrainer:
    def __init__(self, dataset, model, metric, epochs, lr, seed):
        # dataset / model / metric は blab.Builder。まだ実体化されていない
        self.dataset, self.model, self.metric = dataset, model, metric
        self.epochs, self.lr, self.seed = epochs, lr, seed

    def execute(self, run):
        torch.manual_seed(self.seed)

        train = self.dataset.build(split="train")      # ここで初めて実体化
        val   = self.dataset.build(split="val")        # 同じ Builder から 2 個作れる
        model = self.model.build(k=train.n_classes)    # ← 実行時の値を渡せる
        metric = self.metric.build()

        for epoch in range(self.epochs):
            ...
            run.log({"train/loss": loss, "val/acc": acc}, epoch=epoch)

        run.log_summary({"val/acc": acc})
        run.log_artifact("outputs/confusion_matrix.png")
```

### なぜ `.build()` なのか

model を作るには `k`（クラス数）が要るが、その値は dataset を作るまで分からない。
これを YAML に手で書くと（`k: 100`）、dataset を差し替えたとき**黙ってズレる**。
クラス数が多いぶんには行列の形が合ってしまうので、落ちずに間違ったまま学習が走る。

そこで blab は子 component を実体化せず、**作り方（Builder）だけを渡す**。`.build()` を
呼んだ瞬間に、YAML に書かれた引数とコードから渡された引数が合体する。

そして blab が記録するのは、**合体後の最終形**である。

```jsonc
{"args": {"pretrained": false, "k": 100},
 "args_from": {"pretrained": "yaml", "k": "runtime"}}
```

`k: 100` は誰も宣言していない。**実際にそう渡されたから、そう記録されている。**
YAML が嘘をつく余地が構造上ない。

> **YAML は「構成の形」を宣言する。「最終的な値」は観測する。**

## 実行する

```bash
blab run experiments/distill.yaml
```

1. **事前検証** — component が存在するか / import できるか / `@blab.entry` が 1 つか /
   引数名が合っているか / データファイルがあるか。ここで見つかったものは全部エラーで止める
2. **自動凍結** — 参照された component をハッシュ化し、未凍結なら凍結する
3. **実行** — root を実体化して `execute(run)` を呼ぶ

実行せずに検査だけしたいときは `blab check experiments/distill.yaml`。

主なオプション：`--name`（run 名）、`--group`（所属 group）、`--set run.lr=1e-4`（YAML の
1 か所を上書き）、`--runs-dir`（ログの置き場所）。

## 交差検証（CV）

group は宣言するだけ。**同じ名前を名乗った run が同じ group に集まる。**

```bash
for f in 0 1 2 3 4; do
  blab run experiments/cv.yaml --group cv5-lr3e4 --set run.fold=$f --name fold$f
done
```

fold が独立プロセスになるので、複数 GPU に撒けるし、3 fold 目だけ落ちたらそれだけ回し直せる。
trainer は CV を一切知らなくてよい。

集計（mean ± std）は**読むときに導出する**ので、「group がいつ完成するか」を考えなくてよい。
3 fold 回した時点では 3 つぶんが見え、5 つ揃えば 5 つぶんになる。

group を付け忘れたり打ち間違えたりしたら、あとから移せる（UI からもできる）。

```bash
blab mv runs/cifar100/20260913-063012_a1b2_fold0/ --group cv5-lr3e4
```

## 凍結とラベル

**登録作業は無い。** 編集して実行すれば、その中身がハッシュで自動的に凍結され、run に記録
される。1 日 40 回編集しても、**全部の run が再現可能**である。

区切りが付いたときだけ、人間が名前を付ける。

```bash
blab tag resnet18 v3 --note "BN を外した"
```

ラベルを貼った版だけが git 管理下（`components/.frozen/`）へ昇格する。ラベルの無い自動凍結版は
`.blab/` に溜まるだけなので、**smoke test が何百回走っても git は汚れない**。

あの実験のモデルが良かった、と後から気づいたときは run から貼れる。

```bash
blab tag resnet18 v3 --from-run runs/cifar100/20260913-063012_a1b2_distill/
```

## データなどの外部ファイル

パスはマシンごとに違うので、YAML には**論理名**だけを書く。

```yaml
dataset:
  use: imagenet_dir
  path:  {data: IMAGENET_ROOT}
  folds: {data: FOLD_SPLIT_CSV}
```

```jsonc
// blab.local.json — マシンごとに書く。git に載らない
{
  "runs_dir": "/data/experiments/cifar-research",
  "data": {
    "IMAGENET_ROOT":  "/mnt/nvme/datasets/imagenet",
    "FOLD_SPLIT_CSV": "/home/you/work/cifar/splits/fold5.csv"
  }
}
```

component には実パス（`pathlib.Path`）が渡る。blab が記録するのは**同一性であって実体**ではない。

- 小さいファイル（既定 1 MB 未満）は run にコピーする。fold split や label CSV は一番失われやすい
- 大きいものはパス・サイズ・更新時刻・ハッシュだけ
- 巨大なディレクトリはファイル一覧からハッシュを作り、キャッシュする

論理名が未設定だったり実パスが無かったりすれば、**学習を始める前に**エラーになる。

## run を再実行する

```bash
blab run runs/cifar100/20260913-063012_a1b2_distill/
```

run のディレクトリには、そのとき使った component のソースが丸ごと焼き込まれている。
したがって**レジストリもプロジェクトも参照せずに**再実行できる。

- component を編集したあとでも、過去 run は当時のコードで動く
- プロジェクトを消したあとでも、run さえあれば動く
- 共同研究者から run をもらえば、そのまま動く

```
runs/cifar100/20260913-063012_a1b2_distill/
├── meta.json         status / 実行時刻 / 実行時間
├── resolved.yaml     全部ハッシュに解決済みの構成（= ロックファイル）
├── env.json          Python / パッケージ / git commit / GPU
├── components/       ★ 実際に使ったコードの実体
├── metrics.jsonl
├── summary.json
├── artifacts/
└── logs/             stdout.log / stderr.log（自動で取れる）
```

## CLI

| コマンド | 内容 |
| --- | --- |
| `blab init` | プロジェクトを作る |
| `blab link <path>` | 別のプロジェクトをグローバル索引に登録する |
| `blab run <yaml>` | 事前検証 → 凍結 → 実行 |
| `blab run <run のパス>` | 過去 run の再実行 |
| `blab check <yaml>` | 事前検証だけして実行しない |
| `blab new <id>` | component の雛形を作る |
| `blab tag <id> <label> [--note] [--from-run]` | ラベルを貼り、git 管理下へ昇格させる |
| `blab components [--tags model]` | component 一覧 |
| `blab show <id>[@<label>]` | 説明・ソース・ラベル履歴 |
| `blab diff <id>@v1 <id>@v3` | 版間の diff |
| `blab ls [experiment]` | run / group の一覧 |
| `blab show <run のパス>` | 単一 run の meta / 構成 / summary |
| `blab mv <run のパス> --group <name>` | 所属 group を変える |
| `blab rm <run のパス>` | run / group を削除（確認あり） |
| `blab ui [--port 8420] [--open]` | ローカル UI（既定で 127.0.0.1 のみ listen） |

## 実装の進み具合

[docs/design.md §12](docs/design.md) のフェーズ順に入れている。**Phase 1-10 すべて実装済み。**

| Phase | 内容 | |
| --- | --- | --- |
| 1-3 | プロジェクト / component の凍結と import / YAML と事前検証（`blab check`） | 済 |
| 4-6 | Builder と `.build()` の観測 / `blab run` と記録 / `env.json` と `{data:}` | 済 |
| 7-8 | Group と CV / `blab tag` / `ls` / `show` / `diff` / `mv` / `rm` | 済 |
| 10 | 再実行（`blab run <run>`）と実行時引数の食い違い検出 | 済 |
| 9 | UI を `resolved.yaml` ベースに移植（構成ツリーと比較ビュー） | 済 |

触ってみるなら [examples/](examples/) にサンプルプロジェクト一式がある（標準ライブラリ
だけで動く）。

## UI

`blab ui` で以下が見られる。

1. **Experiment 一覧** — run 数 / 実行中の数 / 最終更新
2. **Run テーブル** — 列は `resolved.yaml` の**構成**と `summary.*` から動的構成。
   「どの版の resnet18 を使った run か」でソート・フィルタできる。group は折りたたみ行
3. **Run 詳細** — 構成ツリー / metrics チャート / アーティファクト / ログ / 環境
   - 構成ツリーが blab の要。各ノードをクリックで component 詳細へ。引数は
     **YAML 由来と実行時由来を色で区別**する
   - run に焼き込まれたソースをその場で表示できる
4. **Component 一覧** — id / タグ / ラベル / 使用 run 数 / 最終使用日
5. **Component 詳細** — README、ソース、ラベル履歴と任意 2 版の diff、そして
   **逆引き: この component を使った全 run**（版別に分け、metric の分布付き）
6. **Group 詳細** — mean ± std の集計表、fold 重ね描き、run の所属付け替え
7. **比較ビュー** — **構成の diff + 版が違う component のソース diff** / metrics 重ね描き /
   summary 表 / アーティファクト並置。run と group を混ぜて比較できる

比較が「数値の diff」ではなく「**構成の diff + 変わった部分のコード diff**」になる。
研究者の頭の中は常に「baseline と同じ、ただし loss だけ新しいやつ」という形をしているので、
UI がその形で答える。

学習中の run はライブ更新される（`running` があるときのみ 3 秒間隔でポーリング）。
heartbeat が 60 秒以上途絶えた `running` は `stale` と表示される。

## 環境変数

| 変数 | 内容 |
| --- | --- |
| `BLAB_RUNS` | 実験ログの置き場所（`blab.local.json` の `runs_dir` より優先） |
| `BLAB_STRICT` | `1` で記録の失敗を握らずに送出する（既定は警告に落とす） |

`BLAB_DIR` と `BLAB_DEV` は v2 で廃止された。前者はプロジェクトが `blab.json` で自分を
名乗るようになったため、後者は凍結が自動になって逃げ道が要らなくなったため。

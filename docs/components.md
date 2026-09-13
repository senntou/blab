# component

component は実験の部品（model / dataset / metric / trainer など）で、`components/` の下の
ディレクトリ 1 つが 1 つの component になる。blab は部品の種類を区別しない。

## ディレクトリ

```
components/
├── resnet18/            component 本体（ディレクトリ名 = id）
│   ├── README.ja.md     説明。UI と blab show に表示される
│   ├── main.py          @blab.entry を置く
│   └── blocks.py        相対 import で使える
├── standard_trainer/
├── .meta/               ラベルとタグ（blab が管理する）
└── .frozen/             ラベルを付けた版のコピー（blab が管理する）
```

- id はディレクトリ名。Python の識別子として有効な名前にする（`-` は使えない）
- `__init__.py` は不要
- ディレクトリ内のファイルは相対 import（`from .blocks import BasicBlock`）で使える
- ディレクトリの外から import してよいのは、インストール済みのパッケージだけ。
  他の component を直接 import しない（子として使う場合は YAML に書く）
- 説明は `README.ja.md` または `README.md` に書く

雛形は `blab new <id>` で作れる。

## entry

`main.py` の中の 1 つに `@blab.entry` を付ける。クラスでも関数でもよい。blab は引数を渡して
それを呼び出し、戻り値を使う。

```python
# components/resnet18/main.py
import torch.nn as nn
import blab

from .blocks import BasicBlock


@blab.entry
class Resnet18(nn.Module):
    def __init__(self, k: int, pretrained: bool = False):
        super().__init__()
        ...
```

`@blab.entry` が付いていないクラスや関数は、同じファイルに置いてもよい。

## 子 component と Builder

YAML で引数の位置に書いた component は、実体ではなく `blab.Builder` として `__init__` に渡される。
`.build()` を呼ぶと実体化される。

```python
def execute(self, run):
    train = self.dataset.build(split="train")
    val = self.dataset.build(split="val")          # 同じ Builder から何度でも作れる
    model = self.model.build(k=train.n_classes)    # 実行時に決まる値を渡す
    metric = self.metric.build()                   # 渡す値が無くても .build() を呼ぶ
```

- `.build(**kwargs)` の引数は、YAML に書いた引数と合わせて entry に渡される。同じ名前があれば
  `.build()` に渡した値が使われる
- YAML にもデフォルト値にも `.build()` にも無い必須引数があると、`.build()` の時点でエラーになる
- entry に無い名前を `.build()` に渡すとエラーになる（entry が `**kwargs` を受け取る場合を除く）
- `builder.id` で component の id、`builder.path` で YAML 上の位置（`run.dataset` など）が取れる

子 component のクラスそのものは渡されないので、継承はできない。共通の処理は合成で書くか、
共有パッケージに置く（[推奨する構成と運用](best-practices.md#component-の分け方)）。

### 記録される引数

`.build()` のたびに、最終的に渡された引数とその由来が `resolved.yaml` に記録される。

```yaml
student:
  use: resnet18
  hash: sha256:3f9a1c…
  builds:
    - args: {pretrained: false, k: 100}
      args_from: {pretrained: yaml, k: runtime}
```

| `args_from` | 意味 |
| --- | --- |
| `yaml` | 実験 YAML に書かれた値 |
| `override` | `--set` で上書きされた値 |
| `runtime` | `.build()` にコードから渡された値 |
| `default` | entry のデフォルト値 |

JSON にできない値は、`.build()` の戻り値であれば `{"$ref": "dataset#0"}`、それ以外は
`{"$unrecorded": "Tensor"}` のように記録される。

同じ引数で複数回 `.build()` した場合は 1 件にまとめられる。一度も `.build()` されなかった子は
`builds: []` として残り、実行の終了時に警告が出る。

## root component と `execute`

YAML の `run:` の直下に置いた component（root）は、実体化されたあと `execute(run)` が呼ばれる。
root に `execute` が無いと事前検証でエラーになる。子の component の `execute` は呼ばれない。

## 記録 API

`execute` が受け取る `run`（`blab.Run`）で記録する。

```python
run.log({"train/loss": 0.31, "val/acc": 0.72}, epoch=3)   # 時系列 → metrics.jsonl
run.log({"train/loss": 0.29}, step=1200)                  # step を指定してもよい
run.log_summary({"test/acc": 0.87})                       # run ごとの値 → summary.json
run.log_artifact("outputs/confusion_matrix.png")          # ファイル / ディレクトリを artifacts/ にコピー
run.log_artifact("outputs/cm.png", name="cm_final.png")   # 保存名を指定する
run.path                                                  # run ディレクトリ（pathlib.Path）
run.id                                                    # run の ULID
```

- `log()` の `step` を省略すると、0 から順に自動で振られる
- metric 名に `.` は使えない（警告のうえ `_` に置き換えられる）。区切りには `/` を使う
- `log_summary()` を複数回呼ぶと、同じキーは後の値で上書きされる
- 記録系のエラーは警告として出力され、学習は止まらない。環境変数 `BLAB_STRICT=1` で例外にできる

stdout / stderr は `logs/stdout.log` / `logs/stderr.log` に自動で保存される（端末にも表示される）。

## 版とハッシュ

component の版は、ディレクトリの中身から計算した SHA-256 で区別される。

- ディレクトリ内の全ファイル（README を含む）が対象。ドットで始まるファイル・ディレクトリ、
  `__pycache__`、`*.pyc` は除く
- `blab run` のたびに作業コピーのハッシュを計算し、初めてのハッシュなら
  `.blab/frozen/<id>/<hash>/` にコピーする。実行時はこのコピーから import される
- 登録の操作は無い。編集して実行すれば新しい版として記録される

README だけを変更した場合もハッシュは変わる。

## ラベル

版に名前を付ける。

```bash
blab tag resnet18 v3 --note "BN を外した"
blab tag resnet18 v4 --from-run runs/cifar100/20260913-063012_a1b2_distill/
```

- ラベルを付けた版は `components/.frozen/<id>/<label>/` にコピーされ、git で管理できる。
  ラベルの無い版は `.blab/`（git 管理外）と、その版を使った run の中にだけ置かれる。
  どちらも消すと復元できない（[再現性](best-practices.md#再現性)）
- `--from-run` を付けると、作業コピーではなく、その run にコピーされていたソースにラベルを付ける
- 一度付けたラベルを別の版に付け替えることはできない
- YAML では `use: resnet18@v3` のように参照する。`@latest` は最後に付けたラベルを指す
- ラベルを付けてもハッシュは変わらない（ラベルは `components/.meta/<id>.json` に保存される）

```bash
blab components                     # 一覧（作業コピーのハッシュとラベル）
blab show resnet18                  # 説明、ラベル履歴、この component を使った run
blab diff resnet18@v1 resnet18@v3   # 版の差分
blab diff resnet18@v3 resnet18      # ラベルの版と作業コピーの差分
```

## タグ

`components/.meta/<id>.json` の `tags` に文字列を書くと、`blab components --tags model` で絞り込める。

```json
{
  "schema_version": 2,
  "id": "resnet18",
  "tags": ["model", "classifier"],
  "labels": []
}
```

`blab.json` の `require_tags` にタグを書くと、そのタグを持つ component が構成に 1 つも含まれない
実験は、事前検証でエラーになる。

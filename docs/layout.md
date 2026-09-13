# 保存形式

blab が読み書きするディレクトリとファイルの仕様。`schema_version` は `2`。

UI（`blab ui`）はこの仕様だけを前提にディレクトリを読むので、`blab run` を使わずに
この形式どおりのディレクトリを作っても UI で閲覧できる。

## 1. プロジェクト

**1 git リポジトリ = 1 プロジェクト。** `blab.json` があるディレクトリがプロジェクトルート。

```
<project>/
├── blab.json              ★ プロジェクト ID + 既定設定
├── components/            ★ component（§2）
├── experiments/           ★ 実験 YAML（§3）
├── blab.local.json        ✗ このマシンだけの設定
└── .blab/                 ✗ キャッシュ
    ├── frozen/            ✗   ラベルの無い版
    └── index/             ✗   解決結果・データのハッシュ
```

★ = git 管理下 / ✗ = `.gitignore`

```jsonc
// blab.json — git 管理下
{
  "schema_version": 2,
  "project": "cifar-research",        // 表示名。別プロジェクトからの参照に使う
  "project_uid": "01JQ8R7X4K9M2N",    // blab init が生成する ID
  "components_dir": "components",
  "experiments_dir": "experiments",
  "runs_dir": null,                   // null なら <project>/runs/
  "require_tags": []                  // 事前検証で要求するタグ（§3.2）
}
```

```jsonc
// blab.local.json — git に載らない。マシンごとに書く
{
  "runs_dir": "/data/experiments/cifar-research",
  "data": {                           // 外部ファイルの論理名 → 実パス（§5.6）
    "IMAGENET_ROOT":  "/mnt/nvme/datasets/imagenet",
    "FOLD_SPLIT_CSV": "/home/you/work/cifar/splits/fold5.csv"
  }
}
```

プロジェクトの同一性はディレクトリ名ではなく `project_uid` で決まる。ディレクトリを移動・改名したり、
別の名前で clone したりしても変わらない。

### 1.1 保存先の決まり方

| 優先 | 指定 |
| --- | --- |
| 1 | 環境変数 `BLAB_RUNS` |
| 2 | `blab run --runs-dir <path>` |
| 3 | `blab.local.json` の `runs_dir` |
| 4 | `blab.json` の `runs_dir` |
| 5 | `<project>/runs/` |

相対パスはプロジェクトルートからの相対。`runs_dir` はそのプロジェクト専用として扱い、
blab が中にプロジェクト名のサブディレクトリを作ることはない。

### 1.2 グローバル索引

```jsonc
// ~/.blab/projects.json（BLAB_HOME で場所を変えられる）
{
  "schema_version": 2,
  "projects": {
    "01JQ8R7X4K9M2N": {"name": "cifar-research", "path": "/home/you/work/cifar"},
    "01JQ9T2P8V5W1Q": {"name": "seg-baseline",   "path": "/home/you/work/seg"}
  }
}
```

`blab init` / `blab link` が書く。別プロジェクトの component 参照（§3.1）の解決に使う。
索引に無いプロジェクトを参照した場合は事前検証でエラーになる。

## 2. component（`components/`）

```
components/
├── resnet18/                    # ディレクトリ名 == component id。ここがハッシュ対象
│   ├── README.ja.md
│   ├── main.py                  # 入口モジュール
│   └── blocks.py
├── standard_trainer/
├── .meta/                       # ラベルとタグ（git 管理下・ハッシュ対象外）
│   ├── resnet18.json
│   └── standard_trainer.json
└── .frozen/                     # ラベルの付いた版（git 管理下・ハッシュ対象外）
    └── resnet18/
        ├── v1/
        └── v3/
```

- **component id はディレクトリ名**で、Python の識別子に限る（合成モジュール名の一部になるため。`-` は不可）
- `components/` 直下の**ドットで始まるエントリは component ではない**（`.meta` / `.frozen`）
- **`main.py` が入口モジュール。** `__init__.py` は不要
- `main.py` の名前空間に `@blab.entry` がちょうど 1 つ
- ディレクトリ内の相対 import は自由。外への import はインストール済みのパッケージのみ

ラベルやタグを component 本体の中に置かないのは、ラベルを付ける操作でハッシュが変わらないようにするためである。

### 2.1 ハッシュ

```
sha256( ソートした (相対パス, ファイルの生バイト) の列 )
除外: ドットで始まるエントリ、__pycache__、*.pyc
```

正規化はしない（空白の違いも別の内容として扱う）。README を含むディレクトリ内の全ファイルが
対象になるので、README だけを変更しても新しい版になる。

### 2.2 凍結版

| 置き場所 | 何が入るか | git |
| --- | --- | --- |
| `.blab/frozen/<id>/<hash>/` | 実行時に自動でコピーされた版。すべての版がここに入る | ✗ |
| `components/.frozen/<id>/<label>/` | `blab tag` でラベルを付けた版だけ | ★ |

凍結ディレクトリの中身は作業コピーのコピーそのもので、メタ情報は含まない。

### 2.3 `.meta/<id>.json`

```jsonc
// components/.meta/resnet18.json — git 管理下
{
  "schema_version": 2,
  "id": "resnet18",                    // ディレクトリ名との一致を検査するために持つ
  "tags": ["model", "classifier"],     // フィルタ用の自由文字列
  "labels": [
    {"name": "v1", "hash": "sha256:3f9a1c…", "note": "初版",
     "tagged_at": "2026-09-01T10:00:00+09:00"},
    {"name": "v3", "hash": "sha256:8b2d40…", "note": "BN を外した",
     "tagged_at": "2026-09-08T14:20:00+09:00"}
  ]
}
```

- **配列の順序が登録順**で、`latest` は配列の末尾を指す
- **ラベルは不変**で、一度付けた名前を別のハッシュに付け替えることはできない
- このファイルは無くてもよい。無ければラベルもタグも無いとみなす。`blab tag` が必要に応じて作る

### 2.4 合成モジュール名

凍結ディレクトリは次の名前で `sys.modules` に登録される。

```
blab._c.<id>__<hash の先頭 8 桁>
```

パッケージとして登録するので、`__init__.py` 無しで相対 import が解決する。ハッシュが名前に
入っているため、同じ id の別の版を 1 プロセス内で同時にロードしても衝突しない。

この名前は `blab run` のプロセスにしか登録されないので、`spawn` / `forkserver` で起動した
子プロセスからは解決できない。

## 3. 実験 YAML（`experiments/`）

```yaml
schema_version: 2           # 省略時は 2
experiment: cifar100        # 必須。Experiment ディレクトリ名になる
group: cv5-lr3e4            # 任意。同名の run が同じ group に入る
name: fold0                 # 任意。run ディレクトリ名の末尾になる

run:                        # 必須。root component
  use: standard_trainer
  epochs: 100
  lr: 0.0003
  dataset:
    use: cifar100
    augment: randaug
  metric: {use: top1_accuracy}
  transforms:
    - {use: random_flip, p: 0.5}
    - {use: randaug, n: 2}
```

**`use` キーを持つマッピングは component 参照、それ以外は普通の値。**

- **文字列だけの省略形は認めない。** `metric: top1_accuracy` は文字列の引数として扱う。
  「その id の component があれば参照」という規則にすると、component を新しく作ったときに
  既存の YAML の意味が変わってしまうため
- **参照は引数の値のどの深さにも書ける。** リストの要素でも、マッピングの値でもよい。
  `resolved.yaml` での位置は `transforms[0]` / `metrics.acc` のようなパスで表す
- **予約キーは `use` と `data`（§5.6）。** これらを含む普通のマッピングを引数に渡したい場合は
  `{$raw: {...}}` で包む。`$` で始まるキーは blab が予約する。`$raw` で包まれずに `use` / `data`
  を含むマッピングは、参照として解釈したうえで警告を出す

変数参照（`${...}`）・計算・条件分岐・ループの構文は持たない。

### 3.1 参照の形式

```
<id>                             作業コピー（実行時に自動で凍結）
<id>@<label>                     ラベルで指定
<id>@sha256:<hash>               ハッシュで指定（先頭の一部でもよい）
<project>/<id>[@...]             別プロジェクト。<project> は相手の blab.json の "project"
```

`/` はプロジェクトの区切りとして予約されている。`@` は 1 つだけ。

### 3.2 事前検証

```
YAML 読み込み → 参照解決 → ハッシュ → 凍結 → 凍結版から import → 検査 → 実行
```

検査には import が必要で、import 元は凍結版なので、凍結は検査より先に行われる。
構文エラーを含む component も凍結されるが、ラベルを付けない限り `.blab/` に留まる。

`blab run` / `blab check` は実行前に次を検査し、**違反があれば実行しない**。

1. 参照された component がすべて存在するか（別プロジェクト参照を含む）
2. 各 component を凍結版から import できるか（構文エラー・import エラー）
3. `main.py` に `@blab.entry` がちょうど 1 つあるか
4. root の component が `execute` を持つか
5. YAML の引数名が entry の実際の引数と合っているか
6. YAML にもデフォルト値にも無い必須引数の一覧（実行時に渡される想定として記録する）
7. `{data: NAME}` の論理名が解決でき、実パスが存在するか
8. `blab.json` の `require_tags` を満たすか（構成の中に、要求されたタグを持つ component が 1 つ以上あるか）

## 4. 保存先のノード

Experiment / Group / Run はすべてディレクトリで、種別は直下の `meta.json` の `kind`
（`"experiment" | "group" | "run"`）で判別する。階層とディレクトリ構造は 1:1 で対応する。

```
<runs_dir>/
└── cifar100/                               # Experiment（kind: experiment）
    ├── meta.json
    ├── 20260913-063012_a1b2_distill/       # Run（kind: run）
    │   ├── meta.json
    │   ├── resolved.yaml
    │   ├── env.json
    │   ├── data.json                       # {data: ...} を使った場合のみ
    │   ├── components/
    │   ├── metrics.jsonl
    │   ├── summary.json
    │   ├── artifacts/
    │   ├── data/                           # run にコピーした外部ファイル
    │   └── logs/
    └── cv5-lr3e4/                          # Group（kind: group）
        ├── meta.json
        ├── summary.json                    # 任意。group 自身の値だけ（§6.2）
        ├── 20260913-070001_e5f6_fold0/
        └── 20260913-070032_g7h8_fold1/
```

**Group ディレクトリ名は YAML の `group` を slug 化したもの**で、日時や ID は付けない。
別のプロセスの run が同じ名前を指定することで同じ group に入るため、名前が決定的である必要がある。

### 4.1 ディレクトリ名

Run は `{YYYYMMDD-HHMMSS}_{短ID}_{slug}`（名前が無ければ末尾を省く）。

- 日時は **UTC**。タイムゾーンの異なるマシン間でコピーしても名前の順序が保たれる。
  ローカル時刻での表示は UI が `created_at` から行う
- 短 ID は ULID の**末尾 4 文字（小文字）**
- 作成は `os.mkdir`（exist_ok なし）。既にあれば ULID を採番し直して再試行する
- Experiment ディレクトリ名は slug 化した experiment 名

## 5. run のファイル

### 5.1 `meta.json`

```jsonc
{
  "schema_version": 2,
  "kind": "run",
  "id": "01J8XK7Q2N4M0...",            // ULID（26 文字）
  "name": "distill",
  "status": "running",                 // running | finished | failed | killed
  "project_uid": "01JQ8R7X4K9M2N",
  "source": "experiments/distill.yaml",// 実行に使った YAML（プロジェクトからの相対）
  "created_at": "2026-09-13T15:30:12+09:00",
  "finished_at": null,
  "duration_sec": null,
  "heartbeat_at": "2026-09-13T15:41:03+09:00",
  "exit": null,                        // finished 以外のとき {"type": "...", "message": "...", "traceback": "..."}
  "tags": [],
  "notes": "",
  "moved_from": null                   // blab mv で移された run にだけ入る（§6.3）
}
```

| status | 意味 |
| --- | --- |
| `running` | 実行中 |
| `finished` | 正常終了 |
| `failed` | 例外で終了。`exit` にトレースバックが入る |
| `killed` | シグナル（`SIGTERM` / `SIGINT`）で中断 |

`heartbeat_at` は実行中に **15 秒ごと**に更新される。`status == "running"` のまま `heartbeat_at` が
**60 秒**以上古い run は、UI で `stale` と表示される（プロセスが強制終了された場合など）。

### 5.2 `resolved.yaml`

**その run の構成の記録で、そのまま再実行の入力にもなる**（§7）。

```yaml
schema_version: 2
experiment: cifar100
group: cv5-lr3e4
name: distill
project_uid: 01JQ8R7X4K9M2N
source: experiments/distill.yaml
overrides:                                  # --set で上書きされた箇所
  run.lr: 0.0003

run:
  use: distill_trainer
  project_uid: 01JQ8R7X4K9M2N
  hash: sha256:c41e9a…
  args: {epochs: 100, lr: 0.0003, seed: 0}
  args_from: {epochs: yaml, lr: override, seed: yaml}
  children:
    dataset:
      use: cifar100
      project_uid: 01JQ8R7X4K9M2N
      hash: sha256:77b2f0…
      builds:                               # .build() が呼ばれた回数ぶん並ぶ
        - args: {augment: randaug, split: train}
          args_from: {augment: yaml, split: runtime}
        - args: {augment: randaug, split: val}
          args_from: {augment: yaml, split: runtime}
    student:
      use: resnet18
      project_uid: 01JQ8R7X4K9M2N
      hash: sha256:3f9a1c…
      builds:
        - args: {pretrained: false, k: 100}
          args_from: {pretrained: yaml, k: runtime}
```

- **`hash` が版の同一性**。ラベルは表示のために `label` として併記されることがある
- `builds` は `.build()` が呼ばれた回数ぶん並ぶ。同じ引数の重複は 1 件にまとめる
- **一度も `.build()` されなかった子は `builds: []` として残す。** エラーにはしないが、
  終了時に警告を出し、UI にも表示する
- `args_from` の値は `yaml` | `override` | `runtime` | `default`。
  **シグネチャのデフォルト値が使われた引数も、値を埋めて `default` として記録する**
- 例外などで途中で終わった場合も、それまでの `builds` は残す
- **実行中は `provisional: true` の付いた仮の記録になる。** 学習を始める前に書かれ、
  新しい build が記録されるたびに書き直され、終了時に `provisional` の無い確定版で置き換わる。
  まだ呼ばれていない `.build()` の分は欠けているので、仮の記録の run は再実行できない
  （強制終了などで確定版を書けなかった run も同じ）

引数の値は次のように記録する。

| 引数の種類 | 記録 |
| --- | --- |
| JSON にできる値 | そのまま（`{"k": 100}`） |
| `.build()` が返したオブジェクト | 同一性から検出して参照（`{"$ref": "dataset#0"}`） |
| それ以外のオブジェクト | `{"$unrecorded": "Tensor"}` |
| 外部ファイル | `{"$data": "FOLD_SPLIT_CSV"}`（実体の記録は §5.6） |

### 5.3 `components/`

その run で使った component の凍結ディレクトリのコピー。`resolved.yaml` の `hash` と 1:1 で対応する。

```
components/
├── distill_trainer/
├── resnet18/
└── cifar100/
```

保存先はプロジェクトの外に置かれることがあるので、run のディレクトリだけでコードが読め、再実行できるようにしている。

### 5.4 `env.json`

記録するだけで、検証はしない。

```jsonc
{
  "schema_version": 2,
  "python": "3.12.4",
  "platform": "Linux-7.0.0-30-generic-x86_64",
  "hostname": "gpu03",
  "pid": 1234,
  "argv": ["blab", "run", "experiments/distill.yaml"],
  "packages_hash": "sha256:…",                 // プロジェクトルートの uv.lock のハッシュ
  "packages": {"torch": "2.4.0", "numpy": "2.1.0"},
  "git": {"commit": "2227cb0…", "branch": "main", "dirty": true},
  "cuda": {"torch_cuda": "12.4", "cudnn": "9.1.0", "gpu": "NVIDIA A100-SXM4-80GB"}
}
```

取得に失敗した項目は `null` にせず、理由を残す。

```jsonc
{"cuda": {"$unavailable": "torch を import できない"}}
```

### 5.5 `metrics.jsonl` / `summary.json` / `artifacts/` / `logs/`

`metrics.jsonl` — 時系列の値。1 行 1 レコードの JSON で、**追記のみ**。

```jsonl
{"_step": 0, "_time": 1786702284.5, "_epoch": 0, "train/loss": 2.31}
{"_step": 100, "_time": 1786702301.2, "_epoch": 0, "train/loss": 0.84, "val/acc": 0.72}
```

- `_` で始まるキーは予約（`_step` / `_time` / `_epoch`）。`_time` は unix 秒（float）
- すべての行が同じキーを持つ必要はない
- `step` を省略すると run 内のカウンタで自動採番する（0 始まりで +1。指定があればカウンタはその値に追従する）
- UI はネストしたキーをドット区切りで列にするため、**キーにドットは使えない**
  （既定では警告して `_` に置換、`BLAB_STRICT=1` ならエラー）

`summary.json` — run ごとの値。`log_summary()` を複数回呼ぶと shallow merge（同じキーは後勝ち）。

```json
{"test/acc": 0.9312, "test/loss": 0.221}
```

`artifacts/` — 自由なファイル置き場。サブディレクトリ可。UI は拡張子から画像 / テキスト /
CSV / その他を判別して表示する。

`logs/` — `stdout.log` と `stderr.log`。`blab run` が自動で書く。

component は `blab run` と同じプロセスで動く。出力の捕捉は `sys.stdout` の差し替えではなく
**ファイルディスクリプタの複製**（`os.dup2`）で行い、C 拡張が書く出力も捕捉する。
端末にも同じ出力が表示される。`blab run --no-capture` で無効にできる。

### 5.6 外部ファイルの記録

YAML の `{data: NAME}` は `blab.local.json` の `data` で実パスに解決され、component には
`pathlib.Path` が渡る。記録は `data.json` に入る。

```jsonc
// <run>/data.json
{
  "schema_version": 2,
  "FOLD_SPLIT_CSV": {
    "path": "/home/you/work/cifar/splits/fold5.csv",
    "kind": "file", "size": 48213, "mtime": 1786702284.5,
    "hash": "sha256:9c1f…",
    "hash_kind": "bytes",
    "copied_to": "data/fold5.csv"            // 小さいので run にコピーした
  },
  "IMAGENET_ROOT": {
    "path": "/mnt/nvme/datasets/imagenet",
    "kind": "dir", "n_files": 1281167, "size": 147000000000,
    "hash": "sha256:41ab…",
    "hash_kind": "manifest",
    "copied_to": null
  }
}
```

| 対象 | `hash_kind` | 記録 |
| --- | --- | --- |
| 1 MB 未満のファイル | `bytes` | 全バイトのハッシュ。**`<run>/data/` にコピーする** |
| 64 MB 未満のファイル | `bytes` | 全バイトのハッシュ |
| 64 MB 以上のファイル | `partial` | 先頭 1 MB とサイズから作ったハッシュ |
| ディレクトリ | `manifest` | 配下のファイルの相対パス・サイズ・更新時刻から作ったハッシュ。`.blab/index/` にキャッシュする |

`partial` と `manifest` は中身の全バイトを比較していない。厳密な一致を確認したい場合は、
そのファイルを直接ハッシュする。

## 6. group のファイル

### 6.1 `meta.json`（group）

run と同じ `kind` / `id` / `name` / `created_at` / `tags` / `notes` を持つ。
group を実行するプロセスは無いので、`status` と `heartbeat_at` は無い。

```jsonc
{
  "schema_version": 2,
  "kind": "group",
  "id": "01J8XK7Q2N4M0...",
  "name": "cv5-lr3e4",
  "created_at": "2026-09-13T16:00:00+09:00",
  "tags": [], "notes": ""
}
```

### 6.2 集計は保存しない

**group の集計値（mean ± std）はファイルに書かず、読むときに計算する。**

UI と `blab ls` は、group 配下の**葉の run のみ**を再帰的に集め（内側の group の集計は使わない）、
`status == "finished"` の run の `summary.json` から計算する。`std` は標本標準偏差（n-1）。

group の `summary.json` がある場合、そこに入るのは**その group 自身の値だけ**である
（fold 平均では作れない out-of-fold スコアなど）。集計値とは混ぜない。

```json
{"schema_version": 2, "values": {"oof/macro_f1": 0.681}}
```

現在の blab にはこの値を書くコマンドは無い。ファイルを書けば UI に表示される。

### 6.3 run の移動

`blab mv <run> --group <name>` は run ディレクトリを移動し、run の `meta.json` に `moved_from` を残す。

```jsonc
{"moved_from": {"path": "cifar100/20260913-063012_a1b2_fold0",
                "at": "2026-09-13T18:00:00+09:00"}}
```

## 7. 再実行

`blab run <run のパス>` は、その run の `resolved.yaml` と `components/` を読んで実行する。
component はプロジェクトの `components/` ではなく、run の中のコピーから読み込まれる。

- 新しい run が作られる。元の run は変更しない。保存先は §1.1 の規則で決まる
- 新しい run の `meta.json` に `replay_of`（元の run の ULID とパス）が入る
- `.build()` に渡される実行時の値は、記録を使わずコードが再び計算する。記録と異なる場合は警告を出す
- `--set` は使えない

## 8. 書き込みの約束

- `meta.json` / `summary.json` / `resolved.yaml` / `env.json` / `data.json` は、同じディレクトリの
  一時ファイルに書いてから `os.replace` で置き換える。読み手が書きかけのファイルを見ることはない
- `metrics.jsonl` は 1 行を 1 回の `write` で追記して `flush` する。読み手は**最終行が不完全なら捨てる**
- `logs/*.log` は追記のみ
- **ロックは持たない。1 つの run ディレクトリに書くのは 1 プロセスだけ**という前提。
  分散学習では rank 0 だけが書く
- **group ディレクトリの作成は複数プロセスで競合しうる**（同じ group 名の run を並列に実行する場合）。
  `os.makedirs(exist_ok=True)` と、`meta.json` の「無ければ書く」（`O_CREAT | O_EXCL`）で扱う
- 凍結（`.blab/frozen/<id>/<hash>/`）も競合しうる。一時ディレクトリに展開してから `os.rename` で
  移し、既にあれば捨てる。名前が内容のハッシュなので、衝突した中身は同一である

## 9. UI の読み書き

- ディレクトリを**読むだけ**。例外は run の group の付け替えで、`blab mv` と同じ処理を呼ぶ
- **`_trash` は予約された group 名。** UI の「削除」は run をこの group へ移す操作である。
  一覧 API は既定でこの配下を返さない（`include_trash=1` で含める）ので、通常の run 一覧と
  experiment の集計には出ない。ファイルは残るので、`_trash` から移し戻せば元に戻る
- `meta.json` / `summary.json` / `resolved.yaml` は mtime が変わったものだけ読み直す
- `metrics.jsonl` は読み終えた位置を保持し、追記分だけ読む
- component の逆引き（その component を使った run の一覧）は、`resolved.yaml` のハッシュから作る
- `/files/{path}` はルート外へのパス（`..`、絶対パス、symlink 経由）を拒否する
- `{"$unrecorded": ...}` や `{"$unavailable": ...}` など、記録できなかった値は省略せずに表示する

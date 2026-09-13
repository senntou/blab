# blab レイアウト仕様 v2

blab の**唯一のインターフェースはディレクトリ**である。実行層（`blab run`）はこの仕様の
参照実装にすぎず、閲覧層（`blab ui`）はこの仕様だけを前提に読む。したがって blab を使わずに
手でこの規約どおりのディレクトリを作っても、UI から閲覧できる。

`schema_version` は `2`。設計の背景は [design.md](design.md)、v1 の仕様は
[design.v1.md](design.v1.md) を参照。

v1 からの主な変更点。

| | v1 | v2 |
| --- | --- | --- |
| ルート | `.blab/` に component もログも同居 | **プロジェクト（コード）とログルート（ログ）に分離** |
| 構成の記録 | `components.json`（観測した binding の配列） | `resolved.yaml`（構成の木） |
| ハイパラ | `params.json` | 廃止。component の引数として `resolved.yaml` に入る |
| コードの実体 | `code/`（entrypoint と first-party の snapshot） | `components/`（使った component の実体） |
| 環境情報 | `meta.json` の `env` | `env.json` に独立 |
| group の集計 | `summary.json` に書き込む | **保存しない。読むときに導出する** |
| 版の識別 | 人間が付けた version 文字列 | 内容ハッシュ。ラベルは任意の別名 |

## 1. プロジェクト

**1 git リポジトリ = 1 プロジェクト。** `blab.json` があるディレクトリがプロジェクトルート。

```
<project>/
├── blab.json              ★ プロジェクト ID + 既定設定
├── components/            ★ レジストリ（§2）
├── experiments/           ★ 実験 YAML（§3）
├── blab.local.json        ✗ このマシンだけの設定
└── .blab/                 ✗ キャッシュ。いつ消しても安全
    ├── frozen/            ✗   ラベルの無い自動凍結版
    └── index/             ✗   解決結果・データのハッシュ
```

★ = git 管理下 / ✗ = `.gitignore`

```jsonc
// blab.json — git 管理下。既定値も明示して書く（挙動を隠さない）
{
  "schema_version": 2,
  "project": "cifar-research",        // 人間が読む名前。YAML からの参照に使う
  "project_uid": "01JQ8R7X4K9M2N",    // blab init が生成。これが主キー
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

**`.blab/` には真実を 1 つも置かない。** 消しても作業コピーと run から再構築できるものだけを
置く。これにより、キャッシュの不整合が実験の再現性を脅かすことが原理的に起きない。

### 1.1 ログルートの決まり方

| 優先 | 指定 |
| --- | --- |
| 1 | 環境変数 `BLAB_RUNS` |
| 2 | `blab run --runs-dir <path>` |
| 3 | `blab.local.json` の `runs_dir` |
| 4 | `blab.json` の `runs_dir` |
| 5 | `<project>/runs/` |

相対パスはプロジェクトルートからの相対。`runs_dir` は常にそのプロジェクト専用であり、
blab が中にプロジェクト名のサブディレクトリを掘ることはしない。

### 1.2 グローバル索引

```jsonc
// ~/.blab/projects.json — ただのキャッシュ。壊れても再スキャンで直る
{
  "schema_version": 2,
  "projects": {
    "01JQ8R7X4K9M2N": {"name": "cifar-research", "path": "/home/you/work/cifar"},
    "01JQ9T2P8V5W1Q": {"name": "seg-baseline",   "path": "/home/you/work/seg"}
  }
}
```

`blab init` / `blab link` が書く。別プロジェクトの component 参照（§3.1）の解決に使う。
索引に無いプロジェクトを参照した場合は事前検証でエラーにする。

## 2. レジストリ（`components/`）

```
components/
├── resnet18/                    # ディレクトリ名 == component id。ここがハッシュ対象
│   ├── README.ja.md             #   人が書く説明
│   ├── main.py                  #   入口モジュール
│   └── blocks.py
├── standard_trainer/
├── .meta/                       # blab が管理するメタ情報（git 管理下・ハッシュ対象外）
│   ├── resnet18.json
│   └── standard_trainer.json
└── .frozen/                     # ラベルの付いた版だけ（git 管理下・ハッシュ対象外）
    └── resnet18/
        ├── v1/
        └── v3/
```

**コンポーネント本体のディレクトリには、コードと README しか置かない。** ラベル・タグを
中に置くと、ラベルを貼る行為がそのコンポーネントのハッシュを変えてしまう。`.frozen/` を
外に出したのと同じ理由である。

- **component id はディレクトリ名**であり、Python の識別子に限る（合成モジュール名の一部に
  なるため。`-` は不可）。**`id` フィールドを別途の真実にはしない**（二重の真実を作らない）
- `components/` 直下の**ドットで始まるエントリは component ではない**（`.meta` / `.frozen`）。
  id が Python の識別子に限られるので、この判定と id の規則は衝突しない
- **`main.py` が入口モジュール。** `__init__.py` は不要（実行層がパッケージとして合成する）
- `main.py` の名前空間に `@blab.entry` がちょうど 1 つ
- ディレクトリ内の相対 import は自由。外への import はサードパーティのパッケージのみ

### 2.1 ハッシュ

```
sha256( ソートした (相対パス, ファイルの生バイト) の列 )
除外: ドットで始まるエントリ、__pycache__、*.pyc
```

正規化はしない（空白の違いも別内容）。`.meta/` と `.frozen/` はコンポーネント本体の外に
あるので、そもそも対象にならない。

**そのディレクトリの中身は全部入る。README も入る。** したがって README の誤字を直すと
新しいハッシュの版が 1 つ増える。これは意図的で、「凍結ディレクトリの中身とハッシュが
1:1 である」ことを崩さないための割り切りである（除外するファイルがあると、同じハッシュで
中身が違う凍結版が作れてしまう）。増えた版はラベルが無いので `.blab/` に留まり、git は
汚れない。閲覧層は「差分は README だけ」と表示してよい。

### 2.2 凍結版

| 置き場所 | 何が入るか | git |
| --- | --- | --- |
| `.blab/frozen/<id>/<hash>/` | 自動凍結された版。**全部ここに入る** | ✗ |
| `components/.frozen/<id>/<label>/` | `blab tag` でラベルが付いた版だけ | ★ |

凍結ディレクトリの中身は作業コピーのコピーそのもの。メタ情報を二重に持たない。

境界の基準は「参照されているか」ではなく**「人間が意味を認めたか」**である。自動凍結は
1 日に何十回も起き、smoke test も run を作るので、参照の有無を基準にするとゴミが参照に
よって守られてしまう。

### 2.3 `.meta/<id>.json`

```jsonc
// components/.meta/resnet18.json — git 管理下。コンポーネント本体の外にある
{
  "schema_version": 2,
  "id": "resnet18",                    // ディレクトリ名との一致を検査するためだけに持つ
  "tags": ["model", "classifier"],     // 分類ではなくフィルタ用の自由文字列
  "labels": [
    {"name": "v1", "hash": "sha256:3f9a1c…", "note": "初版",
     "tagged_at": "2026-09-01T10:00:00+09:00"},
    {"name": "v3", "hash": "sha256:8b2d40…", "note": "BN を外した",
     "tagged_at": "2026-09-08T14:20:00+09:00"}
  ]
}
```

**配列の順序が登録順**であり、それが唯一の順序の定義である（ラベルは自由文字列なので
大小比較ができない）。`latest` は配列の末尾を指す予約語。**ラベルは不変**で、一度貼った
名前を別のハッシュに付け替えることはできない。

**このファイルは無くてもよい。無ければ「ラベルもタグも無い」とみなす。** 手で作った
コンポーネントのディレクトリがそのまま動くほうが、ファイルシステムを真実とする方針に合う。
`blab tag` が初めて必要になった時点で実行層が作る。

entry の名前（`@blab.entry` が付いていたクラス名）はここに置かない。**コードから導出できる
のでキャッシュである。** `.blab/index/` に置き、消えても import し直せば分かる。

### 2.4 合成モジュール名

凍結ディレクトリは次の名前で `sys.modules` に載る。

```
blab._c.<id>__<hash の先頭 8 桁>
```

`spec_from_file_location` に `submodule_search_locations` を与えてパッケージとして登録する
ので、`__init__.py` 無しで相対 import が解決する。ハッシュが名前に入っているため、同一 id の
別バージョンを 1 プロセス内で同時にロードしても衝突しない。

## 3. 実験 YAML（`experiments/`）

```yaml
schema_version: 2           # 省略時は 2
experiment: cifar100        # 必須。Experiment ディレクトリ名になる
group: cv5-lr3e4            # 任意。同名を名乗った run が同じ group に集まる
name: fold0                 # 任意。run ディレクトリ名の末尾になる

run:                        # 必須。root component
  use: standard_trainer
  epochs: 100
  lr: 0.0003
  dataset:
    use: cifar100
    augment: randaug
  metric: {use: top1_accuracy}
  transforms:                 # リストも書ける
    - {use: random_flip, p: 0.5}
    - {use: randaug, n: 2}
```

**`use` キーを持つマッピングは component 参照、それ以外は普通の値。**

- **文字列単体の省略形は認めない。** `metric: top1_accuracy` を参照とみなすと
  `optimizer: adam` のような普通の文字列引数と区別が付かず、「その id の component が
  存在すれば参照」という規則になってしまう。component を新規作成した瞬間に既存 YAML の
  意味が黙って変わるため、§9 の原則に反する
- **参照は引数の値の任意の深さに書ける。** リストでも、マッピングの値でもよい。
  `resolved.yaml` での位置は `transforms[0]` / `metrics.acc` のようなパスで表す
- **予約キーは `use` と `data`（§5.6）の 2 つだけ。** これらを含む普通のマッピングを引数に
  渡したい場合は `{$raw: {...}}` で包む。`$` で始まるキーは blab が予約する。
  `$raw` で包まれずに `use` / `data` を含むマッピングは、参照として解釈したうえで
  **警告を出す**（黙って解釈しない）

制御構文は持たない（参照 `${...}`・四則演算・条件分岐・ループ）。

### 3.1 参照の形式

```
<id>                             作業コピー（実行時に自動凍結）
<id>@<label>                     ラベルで固定
<id>@sha256:<hash>               ハッシュで完全固定
<project>/<id>[@...]             別プロジェクト。<project> は blab.json の "project"
```

`/` は プロジェクト区切りとして予約。`@` は 1 つだけ。

### 3.2 事前検証

検査には import が要り、import 元は凍結版でなければならない（§2.2）ので、**凍結が検査に
先立つ**。

```
YAML 読み込み → 参照解決 → ハッシュ → 凍結 → 凍結版から import → 検査 → 実行
```

構文エラーを含む component も凍結される。凍結は内容アドレスなので中身と名前が食い違うことは
なく、ラベルを貼らない限り `.blab/` に留まって git には載らないため、無害である。

`blab run` / `blab check` は、実行前に次を検査し、**違反があれば止める**。

1. 参照された component が全部存在するか（別プロジェクト参照を含む）
2. 各 component を凍結版から import できるか（構文エラー・import エラーはここで出る）
3. `main.py` に `@blab.entry` がちょうど 1 つあるか
4. root の component が `execute` を持つか
5. YAML の引数名が entry の実際の引数と合っているか
6. YAML にもデフォルト値にも無い必須引数の一覧（実行時に渡される想定として記録）
7. `{data: NAME}` の論理名が解決でき、実パスが存在するか
8. `blab.json` の `require_tags` を満たすか（**木の中に**、要求されたタグを持つ component が
   1 つ以上あるか）

v1 と違い、**v2 の検査は遠慮なく止めてよい**。構成が実行前に全部分かっているので、
「まだ load されていないだけ」との区別が付かない、という v1 の問題が存在しない。

## 4. ログルートとノード

Experiment / Group / Run はすべてディレクトリで、種別は直下の `meta.json` の `kind`
（`"experiment" | "group" | "run"`）で判別する。階層構造とディレクトリ構造は 1:1 で対応し、
Group はネストできる。

```
<runs_dir>/
└── cifar100/                               # Experiment（kind: experiment）
    ├── meta.json
    ├── 20260913-063012_a1b2_distill/       # Run（kind: run）
    │   ├── meta.json
    │   ├── resolved.yaml
    │   ├── env.json
    │   ├── components/
    │   ├── metrics.jsonl
    │   ├── summary.json
    │   ├── artifacts/
    │   └── logs/
    └── cv5-lr3e4/                          # Group（kind: group）
        ├── meta.json
        ├── summary.json                    # 任意。group 自身の値だけ（§6）
        ├── artifacts/
        ├── 20260913-070001_e5f6_fold0/
        └── 20260913-070032_g7h8_fold1/
```

**Group ディレクトリ名は YAML の `group` を slug 化したものそのもの**（日時や ID を付けない）。
別プロセスの run が「同じ名前を名乗る」ことで同じ group に入る、という仕組みのため、名前が
決定的でなければならない。

### 4.1 ディレクトリ名

Run は `{YYYYMMDD-HHMMSS}_{短ID}_{slug}`（名前がなければ末尾を省く）。

- 日時は **UTC 固定**。TZ の異なるマシン間で `rsync` しても名前の時系列順が保たれる。
  ローカル時刻での表示は UI が `created_at` から行う
- 短 ID は ULID の**末尾 4 文字（小文字）**。識別子は実質 ULID ひとつで、ディレクトリ名は
  その省略表記
- 作成は `os.mkdir`（exist_ok なし）。既存で失敗したら ULID ごと採番し直してリトライする。
  同一秒・同名の連投でも衝突しない
- Experiment ディレクトリ名は slug 化した experiment 名そのもの

## 5. run のファイル

### 5.1 `meta.json`

```jsonc
{
  "schema_version": 2,
  "kind": "run",
  "id": "01J8XK7Q2N4M0...",            // ULID（26 文字）
  "name": "distill",
  "status": "running",                 // running | finished | failed | killed
  "project_uid": "01JQ8R7X4K9M2N",     // どのプロジェクトから回されたか
  "source": "experiments/distill.yaml",// 実行に使った YAML（プロジェクトからの相対）
  "created_at": "2026-09-13T15:30:12+09:00",
  "finished_at": null,
  "duration_sec": null,
  "heartbeat_at": "2026-09-13T15:41:03+09:00",
  "exit": null,                        // finished 以外のとき {"type": "...", "message": "...", "traceback": "..."}
  "tags": [],
  "notes": "",
  "moved_from": null                   // blab mv で移された run にだけ入る（§6.2）
}
```

`heartbeat_at` は実行層のデーモンスレッドが **15 秒毎**に更新する（`meta.json` 全体を
原子的に置換）。`status == "running"` のまま `heartbeat_at` が **60 秒**以上古い run は、
閲覧層が `stale` として表示する（プロセス強制終了の検出）。

v1 にあった `policy_violations` は無い。ポリシー違反は事前検証で止まるので、違反したまま
完走した run が存在しない。

### 5.2 `resolved.yaml`

**この run が何で組まれていたかの記録であり、同時に再実行可能な入力**である（§7）。

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
      builds:                               # 実際に build された回数ぶん並ぶ
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

- **`hash` が版の同一性**。ラベルは表示のためだけに `label` として併記してよい
- `builds` は `.build()` が呼ばれた回数ぶん並ぶ。同一引数の重複は 1 件に畳む
- **一度も `.build()` されなかった子は `builds: []` として残す。** エラーにはしない
  （条件次第で使わない子は正当にありうる）が、終了時に警告を出し、UI にも明示する。
  宣言されたのに使われなかったことを黙って消さないため
- `args_from` の値は `yaml` | `override` | `runtime` | `default`。
  **シグネチャのデフォルト値が使われた引数も、値を埋めて `default` として記録する**
  （何が渡されたかを残すのが原則。component の版が変わってデフォルトが動いても追える）
- `execute` が呼ばれずに終わった（例外など）場合も、それまでの `builds` は残す

**引数の記録は 3 通りに分ける。**

| 引数の種類 | 記録 |
| --- | --- |
| JSON 化できる値 | そのまま（`{"k": 100}`） |
| `.build()` が返したオブジェクト | 同一性から検出して参照（`{"$ref": "dataset#0"}`） |
| それ以外の live object | `{"$unrecorded": "Tensor"}`。**推測しない** |
| 外部ファイル | `{"$data": "FOLD_SPLIT_CSV"}`（実体の記録は §5.6） |

同一性の検出は weakref で行う（`id()` の再利用による誤参照を避ける）。weakref を張れない
オブジェクトだけ強参照で抱える。

### 5.3 `components/`

この run で実際に使った component の実体。`resolved.yaml` の `hash` と 1:1 で対応する。

```
components/
├── distill_trainer/        # 凍結ディレクトリのコピーそのもの
├── resnet18/
└── cifar100/
```

**run がログルートに置かれ、プロジェクトの外に出るための措置である。** リポジトリが無くても、
プロジェクトを消しても、`rsync` でもらっただけでも、run 単体でコードが読めて再実行できる。

1 つ数 KB〜数十 KB なので、毎回のコピーは無視できるコスト。

### 5.4 `env.json`

**記録するだけ。検証も強制もしない。**

```jsonc
{
  "schema_version": 2,
  "python": "3.12.4",
  "platform": "Linux-7.0.0-30-generic-x86_64",
  "hostname": "gpu03",
  "pid": 1234,
  "argv": ["blab", "run", "experiments/distill.yaml"],
  "packages_hash": "sha256:…",                 // uv.lock があればその中身のハッシュ
  "packages": {"torch": "2.4.0", "numpy": "2.1.0"},
  "git": {"commit": "2227cb0…", "branch": "main", "dirty": true},
  "cuda": {"torch_cuda": "12.4", "cudnn": "9.1.0", "gpu": "NVIDIA A100-SXM4-80GB"}
}
```

取得に失敗した項目は `null` にせず、**理由を残す**。

```jsonc
{"cuda": {"$unavailable": "torch を import できない"}}
```

### 5.5 `metrics.jsonl` / `summary.json` / `artifacts/` / `logs/`

`metrics.jsonl` — 各 step / epoch の時系列。1 行 1 レコードの JSON、**追記のみ**。

```jsonl
{"_step": 0, "_time": 1786702284.5, "_epoch": 0, "train/loss": 2.31}
{"_step": 100, "_time": 1786702301.2, "_epoch": 0, "train/loss": 0.84, "val/acc": 0.72}
```

- `_` 始まりは予約キー（`_step` / `_time` / `_epoch`）。`_time` は unix 秒（float）
- 疎で良い。全行が同じキーを持つ必要はない
- `step` 省略時は run 内部のカウンタで自動採番する（0 始まりで +1。明示指定があれば
  カウンタはその値に追従する）
- UI は `optim.lr` のようにドット区切りでフラット化して列にするため、**キーにドットは
  使えない**（既定で警告して `_` に置換、`BLAB_STRICT=1` ならエラー）

`summary.json` — 1 run につき 1 つのスカラ値。`log_summary` を複数回呼ぶと shallow merge
（同キーは後勝ち）。

```json
{"test/acc": 0.9312, "test/loss": 0.221}
```

`artifacts/` — 自由なファイル置き場。サブディレクトリ可。UI は拡張子から画像 / テキスト /
CSV / その他を判別して表示する。symlink は作らない（`/files` のルート外脱出禁止と矛盾するため）。

`logs/` — `stdout.log` と `stderr.log`。**実行層が自動で書く**（v2 は実行を所有しているため）。

component はサブプロセスではなく `blab run` と**同一プロセス**で動く（事前検証で import 済みの
ものをそのまま使うため）。捕捉は `sys.stdout` の差し替えではなく **fd レベルの複製**
（`os.dup2` でパイプに向け、読んだものをファイルと元の fd の両方へ流す）で行う。torch や
CUDA が C レベルで書く出力を取りこぼさないためである。端末には元どおり出る（tee）。

fd の差し替えはデバッガや進捗バーと相性が悪いので、`blab run --no-capture` で無効にできる。

### 5.6 外部ファイルの記録

YAML の `{data: NAME}` は `blab.local.json` の `data` で実パスに解決され、component には
`pathlib.Path` が渡る。**記録するのは同一性であって実体ではない。**

```jsonc
// meta.json ではなく resolved.yaml と同階層の data.json
{
  "schema_version": 2,
  "FOLD_SPLIT_CSV": {
    "path": "/home/you/work/cifar/splits/fold5.csv",
    "size": 48213, "mtime": 1786702284.5,
    "hash": "sha256:9c1f…",
    "copied_to": "data/fold5.csv"            // 小さいので run にコピーした
  },
  "IMAGENET_ROOT": {
    "path": "/mnt/nvme/datasets/imagenet",
    "kind": "dir", "n_files": 1281167, "size": 147000000000,
    "hash": "sha256:41ab…",                  // ファイル一覧から作った manifest ハッシュ
    "hash_kind": "manifest",                 // manifest | bytes
    "copied_to": null
  }
}
```

- 小さいファイル（既定 1 MB 未満）は `<run>/data/` にコピーする。fold split や label CSV は
  一番失われやすく、一番復元したい
- 大きいものはパス・サイズ・更新時刻・ハッシュだけ
- ディレクトリは既定で **manifest ハッシュ**（相対パス・サイズ・更新時刻の列から作る）。
  全バイトハッシュは明示的に要求されたときだけ。結果は `.blab/index/` にキャッシュする

## 6. group のファイル

### 6.1 `meta.json`（group）

run と同じ `kind` / `id` / `name` / `created_at` / `tags` / `notes`。`heartbeat_at` は無い。
`status` も無い — **group を所有するプロセスが存在しない**ので、終わったかどうかを誰も
知らないためである。

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

**group の集計値（mean ± std）はファイルに書かない。読むときに導出する。**

閲覧層と `blab ls` は、group 配下の**葉の run のみ**を再帰的に集め（内側の group の集計は
見ない = 二重集計しない）、`status == "finished"` な run の `summary.json` からその場で
計算する。`std` は標本標準偏差（n-1）。

これによって「group がいつ完成するのか」という問いが消える。3 fold 回した時点では 3 つぶんが
見え、5 つ揃えば 5 つぶんになり、1 つ消しても整合する。v1 が `blab reindex` で解いていた
問題が発生しない。

`summary.json`（group）が存在する場合、そこに入るのは**その group 自身に記録された値だけ**
である（fold 平均では作れない out-of-fold スコアや検定の p 値など）。集計と混ざらない。

```json
{"schema_version": 2, "values": {"oof/macro_f1": 0.681, "wilcoxon_p": 0.020}}
```

> **未決:** group を所有するプロセスが無いので、この `values` を誰が書くかは決まっていない。
> design.md §14 を参照。

### 6.3 run の移動

`blab mv <run> --group <name>` は run ディレクトリを移動し、run の `meta.json` に
`moved_from` を残す。

```jsonc
{"moved_from": {"path": "cifar100/20260913-063012_a1b2_fold0",
                "at": "2026-09-13T18:00:00+09:00"}}
```

group を宣言で決める以上、付け忘れと打ち間違いは必ず起きるので救済手段を用意する。
**黙って履歴を書き換えない**ため、移動の事実は記録に残す。

## 7. 再実行

`blab run <run のパス>` は、その run の `resolved.yaml` と `components/` **だけ**を読んで実行する。
レジストリもプロジェクトも `blab.json` も参照しない。

- 新しい run が別に作られる。元の run は変更しない
- 新しい run の `meta.json` に `replay_of`（元 run の ULID とパス）が入る
- `.build()` に渡された実行時引数は**再現しない**。コードが再び計算する。記録と食い違った
  場合は警告を出して `resolved.yaml` の両方を残す（データが変わった、ライブラリの挙動が
  変わった、などの検出になる）

**記録を再生するのではなく、コードを動かして一致を確かめる**という立場を取る。

## 8. 書き込みの約束

- `meta.json` / `summary.json` / `resolved.yaml` / `env.json` / `data.json` は同一ディレクトリの
  一時ファイル → `os.replace` で原子的に差し替える。読み手が半端なファイルを見ることはない
- `metrics.jsonl` は 1 行を単一 `write` で追記して `flush`。読み手は**最終行が不完全なら
  捨てる**（次回にその行の先頭から読み直す）
- `logs/*.log` は追記のみ
- **ロックは持たない。1 run ディレクトリに書くのは 1 プロセスだけ**という前提。分散学習では
  rank 0 のみが書く。fork した子プロセスからは `meta.json` を書かない
- **group ディレクトリの作成だけは複数プロセスが競合しうる**（同じ group 名を名乗る run を
  並列で回す場合）。`os.makedirs(exist_ok=True)` と `meta.json` の
  「無ければ書く」（`O_CREAT | O_EXCL`）で扱い、既にあれば黙って使う
- 凍結（`.blab/frozen/<id>/<hash>/`）も同様に競合しうる。一時ディレクトリに展開してから
  `os.rename` で所定の位置へ移し、既にあれば捨てる。**内容ハッシュで名前が決まるので、
  衝突した中身は必ず同一である**

## 9. 閲覧層が保証すること

- ディレクトリを**読むだけ**。例外は `blab mv`（run の group 付け替え）だけで、これも
  UI が直接ディレクトリを触るのではなく実行層の同じ操作を呼ぶ
- `meta` / `summary` / `resolved.yaml` は mtime が変わったものだけ読み直す
- `metrics.jsonl` は読み終えたバイトオフセットを保持し、追記分だけ読む
- component の逆引き（この component を使った run 一覧）は、読み込み済みの `resolved.yaml` から
  ハッシュ → run の逆写像を張って作る
- 消えたディレクトリは索引から除去する
- `/files/{path}` はルート外へのパス脱出（`..`、絶対パス、symlink 経由）を拒否する
- **記録できなかったものは、記録できなかったと表示する。** `{"$unrecorded": ...}`、
  `{"$unavailable": ...}`、解決できなかったデータ、これらを黙って省略しない

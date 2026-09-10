# blab レイアウト仕様 v1

blab の**唯一のインターフェース**はディレクトリである。記録層（`blab` パッケージ）は
この仕様の参照実装にすぎず、閲覧層（`blab ui`）はこの仕様だけを前提に読む。したがって
blab を使わずに手でこの規約どおりのディレクトリを作っても、UI から閲覧できる。

`schema_version` は `1`。

## 1. ルート

```
<root>/
├── blab.json      ルートの目印 + ポリシー
└── components/    レジストリ（§5）
```

```jsonc
// blab.json
{
  "schema_version": 1,
  "require_components": false,     // binding ゼロの run を許すか
  "require_tags": [],              // これらのタグを持つ component が 1 つ以上必要
  "on_missing": "warn",            // error | warn | ignore
  "project_root": null             // first-party 判定の基準（省略時は §6 の順で決める）
}
```

`blab.json` はルートの目印。ルートの決め方は `blab.init(dir=...)` → 環境変数 `BLAB_DIR`
→ cwd から上位に `blab.json`（または `.blab/blab.json`）を探索 → `./.blab` を新規作成、の順。
プロジェクトをまたいで 1 つのルートを共有する使い方は `BLAB_DIR` で行う（既定の
`./.blab` はプロジェクト単位に閉じる）。
探索でヒットした場合、記録層は解決したパスを 1 行ログ出力する。

## 2. ノード

Experiment / Group / Run はすべてディレクトリで、種別は直下の `meta.json` の `kind`
（`"experiment" | "group" | "run"`）で判別する。階層構造とディレクトリ構造は 1:1 で対応し、
Group はネストできる。

```
<root>/
├── blab.json
├── components/                             # レジストリ。ノードではない（§5）
└── mnist-cnn/                              # Experiment（kind: experiment）
    ├── meta.json
    ├── 20260814-063012_a1b2_baseline/      # Run（kind: run）
    │   ├── meta.json
    │   ├── params.json
    │   ├── metrics.jsonl
    │   ├── summary.json
    │   ├── components.json                # 何で組まれていたか（§4）
    │   ├── code/                          # 実際に動いたコード（§6）
    │   ├── artifacts/
    │   └── logs/
    └── 20260814-070000_c3d4_cv5/           # Group（kind: group）
        ├── meta.json
        ├── summary.json                    # group 自身の値 + 配下 run の集計
        ├── artifacts/                      # CV 全体で 1 つの成果物
        └── 20260814-070001_e5f6_fold0/     # Run
```

### 2.1 ディレクトリ名

`{YYYYMMDD-HHMMSS}_{短ID}_{slug}`（名前がなければ末尾を省く）。

- 日時は **UTC 固定**。TZ の異なるマシン間で `rsync` しても名前の時系列順が保たれる。
  ローカル時刻での表示は UI が `created_at` から行う。
- 短 ID は ULID の**末尾 4 文字（小文字）**。識別子は実質 ULID ひとつで、ディレクトリ名は
  その省略表記。
- 作成は `os.mkdir`（exist_ok なし）。既存で失敗したら ULID ごと採番し直してリトライする。
  これで同一秒・同名の連投でも衝突しない。
- Experiment ディレクトリ名は slug 化した experiment 名そのもの。

## 3. ファイル

### `meta.json`（run）

```jsonc
{
  "schema_version": 1,
  "kind": "run",
  "id": "01J8XK7Q2N4M0...",          // ULID（26 文字）
  "name": "baseline-lr3e4",
  "status": "running",               // running | finished | failed | killed
  "created_at": "2026-08-14T15:30:12+09:00",
  "finished_at": null,
  "duration_sec": null,
  "heartbeat_at": "2026-08-14T15:41:03+09:00",
  "tags": ["cnn"],
  "notes": "",
  "git": {"commit": "a1b2c3d", "branch": "main", "dirty": false},
  "env": {
    "python": "3.12.3", "hostname": "gpu01", "platform": "Linux-7.0", "pid": 1234,
    "packages": {"torch": "2.11.0", "numpy": "2.4.3"}   // 実体を残さないものの名前と version
  },
  "cmd": "python train.py --lr 3e-4",
  "policy_violations": []            // blab.json のポリシーに反した点（無い場合はキー自体が無い）
}
```

`env.packages` は **import されたモジュールに対応するインストール済み配布物**だけを
挙げる（環境全体ではない）。実体を snapshot しないものについて「何を使ったか」は
残す、という原則の適用である（§6）。

`policy_violations` は `blab.json` の `require_components` / `require_tags` に反した run
にだけ現れる。**違反があっても値は全部書き切り、`status` は `finished` のまま**にする。
ポリシー違反は記録の規律の問題であって、実験の失敗ではない。`on_missing: "error"` の
場合は、すべて書いたあとにプロセスへ例外を投げる。

`heartbeat_at` は記録層のデーモンスレッドが **15 秒毎**に更新する（`meta.json` 全体を
原子的に置換）。`status == "running"` のまま `heartbeat_at` が **60 秒**以上古い run は、
閲覧層が `stale` として表示する（プロセス強制終了の検出）。

### `meta.json`（group）

run と同じ `kind` / `id` / `name` / `status` / `created_at` / `finished_at` /
`duration_sec` / `tags` / `notes` に加えて、`group_kind`（`"cv"` など、UI の表示ヒント）。
group には `heartbeat_at` はない。

### `params.json`

ハイパラなどの実行時情報。任意のネスト JSON。UI は `optim.lr` のようにドット区切りで
フラット化して列にするため、**キーにドット（`.`）は使えない**（記録層は既定で警告して
`_` に置換、`BLAB_STRICT=1` ならエラー）。リストなど非スカラの葉は UI では JSON 文字列と
して 1 列に表示する。

### `metrics.jsonl`

各 step / epoch の時系列。1 行 1 レコードの JSON、**追記のみ**。

```jsonl
{"_step": 0, "_time": 1786702284.5, "_epoch": 0, "train/loss": 2.31}
{"_step": 100, "_time": 1786702301.2, "_epoch": 0, "train/loss": 0.84, "val/acc": 0.72}
```

- `_` 始まりは予約キー（`_step` / `_time` / `_epoch`）。`_time` は **unix 秒（float）**。
- 疎で良い。全行が同じキーを持つ必要はない。
- `step` 省略時は run 内部のカウンタで自動採番する（0 始まりで +1。明示指定があれば
  カウンタはその値に追従する）。

### `summary.json`（run）

1 run につき 1 つのスカラ値。`log_summary` を複数回呼ぶと shallow merge（同キーは後勝ち）。

```json
{"test/acc": 0.9312, "test/loss": 0.221}
```

### `summary.json`（group）

**2 セクションからなる。**

- `values` — **その group 自身に記録された値**（`Group.log_summary`）。CV 全体で 1 つしか
  定義できない指標（out-of-fold スコア、fold 対応差の検定など）の置き場所。fold 平均では
  作れないので配下 run には置けない。**正のデータであってキャッシュではない。**
- `metrics` / `n_runs` / `n_excluded` — 配下 run の `summary.json` の自動集計。**葉の run のみ**を
  再帰的に集め、内側の group の `summary.json` は見ない（二重集計しない）。したがって
  **`values` は親の集計に混ざらない**。集計対象は `status == "finished"` な run で、それ以外は
  `n_excluded` に数える。`std` は標本標準偏差（n-1）。

```json
{
  "schema_version": 1,
  "values": {"oof/macro_f1": 0.681, "paired_diff_mean": 0.043, "wilcoxon_p": 0.020},
  "n_runs": 5,
  "n_excluded": 0,
  "metrics": {
    "test/acc": {"mean": 0.9284, "std": 0.0061, "min": 0.9192, "max": 0.9350, "count": 5}
  },
  "generated_at": "2026-08-14T16:20:00+09:00"
}
```

集計セクションはキャッシュでいつでも再生成できる（`blab reindex` / `blab.aggregate_group`）。
**再生成は `values` を読み直して書き戻す。集計が人の記録を消してはいけない。**
（ファイルごと消せば `values` も失われる点だけは run の `summary.json` と同じ扱い。）

### `artifacts/` と `logs/`

自由なファイル置き場。サブディレクトリ可。UI は拡張子から画像 / テキスト / CSV / その他を
判別して表示する。symlink は当面作らない（`/files` のルート外脱出禁止と矛盾するため）。

## 4. `components.json`（run）

「この run が何で組まれていたのか」の記録。**宣言ではなく観測**である。`blab.load()` が
実際に呼ばれた事実だけが載る。

```jsonc
{
  "schema_version": 1,
  "bindings": [
    {"index": 0, "id": "mlp_classifier", "version": "v2", "role": "student",
     "hash": "sha256:8b2d40…", "loaded_by": null, "args": {"k": 100},
     "entrypoint": 0},
    {"index": 1, "id": "mlp_backbone", "version": "v2", "role": null,
     "hash": "sha256:77b3de…", "loaded_by": 0, "args": {"hidden": 64},
     "entrypoint": 0},
    {"index": 2, "id": "feature_stats", "version": "v1", "role": "stats",
     "hash": "sha256:dd368c…", "loaded_by": null, "args": {"data": {"$ref": 0}},
     "entrypoint": 0}
  ],
  "entrypoints": [
    {"name": "train.py", "path": "code/train.py", "hash": "sha256:3c91ff…",
     "recorded_at": "2026-09-10T15:30:12+09:00",
     "argv": "train.py --lr 3e-4",
     "project_root": "/home/me/proj",
     "first_party": [{"module": "mylib.losses", "path": "code/_first_party/mylib/losses.py",
                      "hash": "sha256:…", "origin": "/home/me/proj/mylib/losses.py"}],
     "unresolved_imports": [],
     "skipped": []}
  ]
}
```

### 4.1 binding のフィールド

| フィールド | 意味 |
| --- | --- |
| `index` | この配列内の添字。`$ref` と `loaded_by` の参照先 |
| `id` / `version` | 解決された component。dirty 実行では `version` が `null` |
| `role` | 呼び出し側が付けた役名（`"student"` など）。任意 |
| `hash` | **実際に import されたファイル**の内容ハッシュ |
| `loaded_by` | この load が起きたときに実体化中だった binding の `index`（**呼び出しの包含**） |
| `args` | entry に渡された引数（**データフロー**） |
| `entrypoint` | どの entrypoint の実行中に load されたか（`entrypoints` の添字） |
| `dirty` | `true` なら未登録の `current/` を実行した。このキーは真のときだけ現れる |
| `snapshot` | dirty のときのソースの置き場（`code/_components/<id>.py`） |
| `failed` | `true` なら entry が例外を投げた。このキーは真のときだけ現れる |

**`index` は load 開始順**に振る。この採番により `loaded_by` は必ず自分より小さい `index`
を指すので、閲覧層はトポロジカルソートなしに構成の木を描ける。

`loaded_by` と `args` の `$ref` は**別物**である。前者は「どの component の実体化中に
起きた load か」、後者は「どの binding から引数として渡されたか」。component が
`__init__` の中で `blab.load()` した依存は前者にしか現れない（引数ではないため）。

`(id, version, role, loaded_by, args)` が完全一致する load は 1 件に畳む。args が違えば
別 binding として残る（CV の fold ごとの dataset など）。

### 4.2 引数の記録

| 引数の種類 | 記録 |
| --- | --- |
| JSON 化できる値 | そのまま（`{"k": 100}`） |
| `blab.load` が返したオブジェクト | 同一性から検出して `{"$ref": <index>}` |
| それ以外の live object | `{"$unrecorded": "Tensor"}`。**推測しない** |

## 5. レジストリ（`components/`）

```
<root>/components/
└── mlp_classifier/                    # ディレクトリ名 == component id
    ├── component.json                 # メタ情報と version 一覧（この配列の順序が登録順）
    ├── README.ja.md                   # 人が書く説明（component 単位・安定）
    ├── current/                       # ← 編集する場所（可変）
    │   └── mlp_classifier.py
    └── versions/                      # ← 登録済み。不変
        ├── v1/mlp_classifier.py
        └── v2/mlp_classifier.py
```

```jsonc
// component.json
{
  "schema_version": 1,
  "id": "mlp_classifier",
  "entry": "MlpClassifier",            // @blab.entry で見つかった名前（登録時に記録）
  "tags": ["model"],
  "versions": [
    {"id": "v1", "hash": "sha256:3f9a1c…", "note": "初版",
     "registered_at": "2026-09-01T10:00:00+09:00"},
    {"id": "v2", "hash": "sha256:8b2d40…", "note": "dropout を追加",
     "registered_at": "2026-09-08T14:20:00+09:00"}
  ]
}
```

- **`versions` 配列の順序が登録順**であり、それが唯一の順序の定義である（version id は
  自由文字列なので大小比較ができない）。`latest` は配列の末尾を意味する。
- component id は Python の識別子に限る（合成モジュール名 `blab._components.<id>__<version>`
  の一部になるため）。version id は英数字で始まり `. _ -` を含められる自由文字列で、
  `latest` は予約語。
- `versions/<version>/` にはソースファイルだけを置く。メタ情報を二重に持たない。
- `components/` は**ノードではない**（`meta.json` を持たない）。閲覧層はここを
  run/group の走査対象から外す。

## 6. `code/`（run に残る実際のコード）

```
code/
├── train.py                       # entrypoint の snapshot
├── eval_probe.py                  # 後から blab.open() で書き足したスクリプト
├── _first_party/                  # entrypoint が import した自作モジュール
│   └── mylib/losses.py            #   置き場は import パスを写した階層
└── _components/                   # dirty 実行した component のソース
    └── mlp_classifier.py
```

entrypoint は `__main__.__file__` から特定する。component と違って自己完結を強制できない
ので、**辿れる範囲を snapshot し、辿れなかったものを記録する。**

- **first-party** = インストールされていないモジュール（site-packages / 標準ライブラリ /
  blab 自身 / blab ルート配下を除外）のうち、**project root 配下**にあるもの。
  project root は ① `blab.json` の `project_root` ② entrypoint から辿った git root
  ③ entrypoint のあるディレクトリ の順に決める。**`BLAB_DIR` の親は基準にしない**
  （データディレクトリはコードと無関係な場所に置けるので、対応が取れない）。
- editable install したパッケージは、実体が project root の外なら third-party 扱いで
  snapshot しない。名前と version は `meta.json` の `env.packages` に残る。
- 辿れなかった import は `unresolved_imports` に、コピーを諦めたファイル（サイズ上限・
  ファイル数上限）は `skipped` に残り、閲覧層は run に「未記録の参照あり」と表示する。
  **所在が説明できる名前は出さない** — この実行で import された名前、標準ライブラリ、
  インストール済み配布物の名前は「隠している」ことにならないため。残るのは動的 import と、
  どれにも該当しない名前だけ。
- 同じ名前で内容が違う entrypoint が来た場合は `<stem>.<hash8>.py` として別に置く。
  同じ内容・同じ argv の再実行は 1 件に畳み、`n_recorded` と `last_recorded_at` を持つ。

## 7. 書き込みの約束

- `meta.json` / `params.json` / `summary.json` / `components.json` は同一ディレクトリの一時ファイル →
  `os.replace` で原子的に差し替える。読み手が半端な JSON を見ることはない。
- `metrics.jsonl` は 1 行を単一 `write` で追記して `flush`。読み手は**最終行が不完全なら
  捨てる**（次回にその行の先頭から読み直す）。
- **ロックは持たない。1 run ディレクトリに書くのは 1 プロセスだけ**という前提。分散学習では
  rank 0 のみが書く。記録層は fork した子プロセスからは `meta.json` を書かない。

## 8. 閲覧層が保証すること

- ディレクトリを**読むだけ**で、一切書き込まない。
- `meta` / `params` / `summary` は mtime が変わったものだけ読み直す。
- `metrics.jsonl` は読み終えたバイトオフセットを保持し、追記分だけ読む。
- `components.json` も mtime 差分で読み直す。component の逆引き（この component を使った
  run 一覧）は、読み込み済みの `components.json` から逆写像を張って作る。
- 消えたディレクトリは索引から除去する。
- `/files/{path}` はルート外へのパス脱出（`..`、絶対パス、symlink 経由）を拒否する。

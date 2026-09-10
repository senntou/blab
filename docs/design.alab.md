# ALab 設計方針

`what.md` の要件をもとに、実装方針を確定させたもの。

## 0. 決定事項サマリ

| 論点 | 決定 |
| --- | --- |
| UI の動かし方 | ローカルサーバ（Python）+ 静的フロント（HTML/CSS/JS） |
| 保存パッケージ | Python のみ |
| ライブ表示 | 必要（学習中の run も UI に出る） |
| クエリ | UI 上のフィルタ / ソート / 比較。SQL は当面提供しない |
| 保存形式 | JSON / JSONL を正とする。parquet は `alab export` で後から生成する任意成果物 |
| Group 集計 | 配下 run の summary をキーごとに mean/std/min/max で自動集計 |
| Group 自身の値 | `group/summary.json` の `values` セクションに記録する（集計とは別枠。集計で消えない） |
| 記録済み run/group への追記 | `alab.open(パス/ID/名前)` で開き直す。`resume=True` でのみ status を running に戻す |
| run ディレクトリ名 | `<日時(UTC)>_<短ID>_<名前>`（例 `20260814-063012_a1b2_baseline-lr3e4`） |
| ID | run/group の `id` は ULID（自前実装）。短 ID は ULID の末尾 4 文字 |
| ライブ判定 | デーモンスレッドが 15 秒毎に `heartbeat_at` を更新。UI は 60 秒で `stale` 表示 |
| symlink アーティファクト | 当面提供しない（copy / move のみ） |

## 1. 全体像

ALab は 2 つの独立した層からなる。

```
┌──────────────────────────────┐
│ 記録層 (alab パッケージ)        │  実験コードから呼ぶ。ディレクトリに書くだけ。
└──────────────┬───────────────┘
               │  ファイルシステム（＝唯一のインターフェース）
┌──────────────┴───────────────┐
│ 閲覧層 (alab ui)              │  ディレクトリを読むだけ。書き込まない。
└──────────────────────────────┘
```

**この 2 層は互いを知らない。** 記録層はレイアウト規約に従ってファイルを書くのみで、UI の存在を前提としない。閲覧層はディレクトリを読み取り専用で走査する。したがって、

- alab を使わずに手で規約どおりのディレクトリを作っても UI で閲覧できる
- 記録層だけ使って UI を使わなくてもよい
- 保存済みディレクトリは `rsync` / `scp` でそのまま持ち運べる（DB を持たない）

規約は「レイアウト仕様」として文書化し、記録層はその参照実装という位置づけにする。

## 2. ディレクトリレイアウト

### 2.1 階層と種別

Experiment / Group / Run はすべて**ディレクトリ**で表現し、種別は各ディレクトリ直下の `meta.json` の `kind` フィールドで判別する。これにより階層構造とディレクトリ構造が 1:1 で一致し、Group のネストも自然に扱える。

```
alab/                                       # ルート（既定 ./alab、ALAB_DIR で変更可）
├── alab.json                               # {"schema_version": 1}  ルートの目印
└── mnist-cnn/                              # Experiment
    ├── meta.json                           # kind: "experiment"
    ├── 20260814-063012_a1b2_baseline/      # Run（日時は UTC。created_at の 15:30+09:00 に対応）
    │   ├── meta.json                       # kind: "run"
    │   ├── params.json
    │   ├── metrics.jsonl
    │   ├── summary.json
    │   ├── artifacts/
    │   │   ├── confusion_matrix.png
    │   │   └── preds.csv
    │   └── logs/
    │       └── stdout.log                  # 任意
    └── 20260814-070000_c3d4_cv5/           # Group
        ├── meta.json                       # kind: "group"
        ├── summary.json                    # 集計結果（キャッシュ、再生成可能）
        ├── 20260814-070001_e5f6_fold0/     # Run
        ├── 20260814-070142_a7b8_fold1/
        └── ...
```

命名規則: `{YYYYMMDD-HHMMSS}_{短ID 4桁}_{slug 化した名前}`。名前未指定なら末尾を省く。

- 日時は **UTC 固定**。TZ の異なるマシン間で `rsync` しても名前の時系列順が保たれる。ローカル時刻での表示は UI が `created_at` から行う。
- 短 ID は run の ULID の**末尾 4 文字（小文字化）**。識別子は実質 ULID の 1 つで、ディレクトリ名はその省略表記。
- 作成は `os.mkdir`（exist_ok なし）で行い、既存で失敗したら ULID ごと採番し直してリトライする。これで同一秒・同名の連投でも衝突しない。

### 2.2 ファイル仕様

**`meta.json`（run）** — 1 run に 1 つ。実行そのものの情報。

```jsonc
{
  "schema_version": 1,
  "kind": "run",
  "id": "01J8XK7Q2N4M0",          // 全体で一意（ULID）
  "name": "baseline-lr3e4",
  "status": "running",            // running | finished | failed | killed
  "created_at": "2026-08-14T15:30:12+09:00",
  "finished_at": null,
  "duration_sec": null,
  "heartbeat_at": "2026-08-14T15:41:03+09:00",  // デーモンスレッドが 15 秒毎に更新
  "tags": ["cnn", "aug"],
  "notes": "",
  "git": {"commit": "a1b2c3d", "branch": "main", "dirty": false},
  "env": {"python": "3.12.3", "hostname": "gpu01", "platform": "Linux-7.0"},
  "cmd": "python train.py --lr 3e-4"
}
```

`heartbeat_at` は `init` 時に起動するデーモンスレッドが **15 秒毎**に更新する（meta.json 全体を原子的に書き換え）。`status == "running"` のまま `heartbeat_at` が **60 秒**以上古い run は UI 側で `stale` として表示する（プロセス強制終了の検出）。スレッド内の例外は握って警告に落とし、学習本体には影響させない。

**`params.json`** — ハイパラなどの実行時情報。任意のネスト JSON を許可し、UI では `optim.lr` のようにドット区切りでフラット化して列にする。フラット化と衝突するため**キーにドット（`.`）は禁止**（記録層でバリデーション。`ALAB_STRICT=1` ならエラー、既定では警告して `_` に置換）。リスト等の非スカラの葉は UI では JSON 文字列として列表示する。

**`metrics.jsonl`** — 各 step / epoch の時系列。1 行 1 レコード、追記のみ。

```jsonl
{"_step": 0, "_time": 1786...,  "_epoch": 0, "train/loss": 2.31}
{"_step": 100, "_time": 1786..., "_epoch": 0, "train/loss": 0.84, "val/acc": 0.72}
```

- `_` 始まりは予約キー（`_step`, `_time`, `_epoch`）。それ以外はユーザ定義。`_time` は **unix 秒（float）**。
- `step` 省略時は run 内部のカウンタで**自動採番**する（0 始まりで +1。`step` を明示指定した呼び出しがあればカウンタはその値に追従する）。
- 疎で良い。全行が同じキーを持つ必要はない。
- 追記オンリーなので学習中でも読める（＝ライブ表示の土台）。

**`summary.json`** — 1 run につき 1 つのスカラ値。テスト評価値、最良値、総パラメータ数など。

```json
{"test/acc": 0.9312, "test/loss": 0.221, "best/val_acc": 0.9280}
```

`log_summary` を複数回呼んだ場合は **shallow merge（同キーは後勝ち）**。

**`artifacts/`** — 自由なファイル置き場。UI は拡張子から画像 / テキスト / CSV / その他を判別して表示する。サブディレクトリ可。

**`meta.json`（group）** — `kind: "group"`, `id`, `name`, `status`, `created_at`, `finished_at`, `tags`, `notes`, および `group_kind`（`"cv"` など、UI の表示ヒント）。`status` は run と同じ `running | finished | failed | killed`。group コンテキストマネージャが例外脱出した場合は `failed` を記録するが、summary の集計自体は finished な配下 run だけで行って書き出す。

**`summary.json`（group）** — `values`（group 自身に記録された値）と自動集計。§4 参照。

### 2.3 書き込みの原子性

- `meta.json` / `params.json` / `summary.json`: 同一ディレクトリに一時ファイルを書いて `os.replace` で差し替える。読み手が半端な JSON を見ることがない。
- `metrics.jsonl`: 1 行を単一 `write` で追記し `flush`。行途中で読まれた場合、読み手は最終行が不完全なら捨てる。
- ロックは持たない。1 run ディレクトリに書くのは 1 プロセスのみ、という前提を仕様に明記する。

## 3. 記録層（Python パッケージ `alab`）

### 3.1 API

```python
import alab

# 単独 run
run = alab.init(experiment="mnist-cnn", name="baseline", params={"lr": 3e-4, "bs": 128})
for step, batch in enumerate(loader):
    run.log({"train/loss": loss}, step=step)
run.log_summary({"test/acc": acc})
run.log_artifact("outputs/confusion_matrix.png")
run.finish()

# コンテキストマネージャ（推奨・例外時に status="failed" を記録）
with alab.init(experiment="mnist-cnn", params=cfg) as run:
    ...

# Group（CV）
with alab.group(experiment="mnist-cnn", name="cv5", group_kind="cv") as g:
    for fold in range(5):
        with g.run(name=f"fold{fold}", params={**cfg, "fold": fold}) as run:
            ...
    # fold 平均では作れない値（out-of-fold 指標、fold 対応差の検定）は group 自身に持たせる
    g.log_summary({"oof/acc": 0.931, "wilcoxon_p": 0.02})
    g.log_artifact("output/per_fold.csv")
    # g.finish() 時に配下 run の summary を自動集計して group/summary.json を書く

# 記録済みの run / group を開き直す（評価が学習の後に来る場合）
alab.open("mnist-cnn/20260814-063012_a1b2_baseline").log_summary({"probe/acc": 0.71})
alab.open("a1b2").log_artifact("output/probe/confusion.png")   # 短 ID / 名前でも引ける
with alab.open("a1b2", resume=True) as run:                     # 中断した学習の再開
    run.log({"train/loss": 0.2})
```

補助:

- `run.log_artifact(path, name=None)` — コピー（既定）。大きいファイル向けに `mode="move"` を用意。symlink は当面提供しない（`/files` の「ルート外脱出禁止」と矛盾するため。§9 参照）。
- `run.log_image(key, array_or_path)` — 画像を `artifacts/` に保存する薄いヘルパ。
- `run.dir` — run ディレクトリの `Path`。ユーザが直接書きたい場合の逃げ道。
- `alab.open(target, dir=None, resume=False)` — **記録済みの run / group を開き直す。**
  `target` はルートからの相対パス / 実パス / ディレクトリ名 / ULID / 短 ID / `name`。
  走査で複数一致したら**曖昧としてエラー**にする（実験の取り違えは黙って起きてはいけない）。
  既定（`resume=False`）は status も実行時間も書き換えず、記録の追加だけを行う。
  `resume=True` は run 限定で、status を `running` に戻し heartbeat を再開する。
  `metrics.jsonl` は追記で、step は最終行の続きから採番する。
- `group.log_summary(dict)` / `group.log_artifact(path)` — group 自身の値・成果物。§4 参照。
- `alab.init(dir=...)` / 環境変数 `ALAB_DIR` でルート指定。既定は cwd から上に `alab.json` を探し、無ければ `./alab` を作る。探索でヒットした場合は解決したルートのパスを init 時に 1 行ログ出力する（別プロジェクト配下での誤ヒット事故の検知用）。

### 3.2 設計上の約束

- **依存は最小限**。記録層は標準ライブラリのみで動く（parquet export と UI は extra 依存）。学習環境に重い依存を持ち込まない。
- **記録は決して学習を落とさない**。ログ書き込み中の例外は握って警告に落とす（`ALAB_STRICT=1` で厳格モードに切替）。
- **`log()` は安い**。既定はバッファリングせず即 flush（ライブ表示のため）。高頻度呼び出し向けに `flush_interval` を用意。
- ネストした `alab.init` は許可しない（明示的なエラー）。

### 3.3 モジュール構成

```
alab/
├── __init__.py        # init / group の公開 API
├── layout.py          # パス規約・命名・ディレクトリ探索（仕様の中心）
├── schema.py          # meta/params/summary のスキーマとバリデーション
├── run.py             # Run オブジェクト
├── group.py           # Group オブジェクト・集計
├── io.py              # 原子的書き込み、JSONL writer/reader
├── index.py           # ディレクトリ走査とインメモリ索引（UI が使う）
├── aggregate.py       # Group 集計ロジック
├── export.py          # parquet 書き出し（任意 extra）
├── cli.py             # alab ui / ls / export / rm
├── server/
│   └── app.py         # FastAPI アプリ
└── static/            # index.html / app.js / style.css
```

## 4. Group 集計と group 自身の値

`group/summary.json` は 2 セクションからなる。

### 4.1 `values` — group 自身の値

`Group.log_summary` で書く、**その group にしか置き場所がない値**。10-fold CV なら
out-of-fold 指標や fold 対応差の検定 p 値がこれにあたる。fold 平均では作れないので
配下 run には置けず、かといって「CV 1 本 = 1 実験」の記録に含めないと後から引けない。

- run の `summary.json` と同じく shallow merge（後勝ち）。
- **集計とは別セクションなので、`alab reindex` しても消えない。** 集計が人の記録を
  上書きする構造にはしない。
- **親 group の集計には混ざらない**（集計は葉の run だけを見るため、§4.2）。
- 表示上は、同じキーが集計側にもある場合 **group 自身の値を優先**する。明示的に書いた
  値を平均で上書きしてはいけない。UI は集計由来のセルにだけ `± std (n=)` を添える。

### 4.2 配下 run の集計

`metrics` / `n_runs` / `n_excluded` は配下 run の `summary.json` から自動生成する。

- 配下（再帰的に）の `status == "finished"` な run を集める。**集めるのは葉の run のみ**で、内側にネストした group 自身の `summary.json` は見ない（二重集計しない）。
- キーの和集合をとり、各キーについて数値の `mean` / `std` / `min` / `max` / `count` を出す。
- 一部の run に欠けているキーは、存在する run のみで集計し `count` に反映する。
- 集計対象外になった run の数を `n_excluded`（failed / killed / running / stale の合計）として記録する。「fold が 1 つ落ちて 4 fold の平均になっている」ことに UI 上で気づけるようにするため。

```json
{
  "schema_version": 1,
  "values": {"oof/acc": 0.9301, "wilcoxon_p": 0.02},
  "n_runs": 5,
  "n_excluded": 0,
  "metrics": {
    "test/acc": {"mean": 0.9284, "std": 0.0061, "min": 0.9192, "max": 0.9350, "count": 5}
  },
  "generated_at": "2026-08-14T16:20:00+09:00"
}
```

集計セクションはあくまでキャッシュで、UI は必要なら再計算できる（`alab.aggregate` が同じロジックを提供）。`alab reindex` で全 group を再生成する。再生成は `values` を読み直して書き戻す。

## 5. 閲覧層

### 5.1 サーバ

`alab ui [--dir ./alab] [--port 8420] [--open]` で FastAPI + uvicorn を起動し、`alab/static/` を配信する。読み取り専用（当面、UI からの notes 編集なども入れない）。

索引の作り方:

- 起動時にルート配下を走査し、`meta.json` を持つディレクトリを収集してツリーを作る。
- run ごとに `meta/params/summary` の mtime を覚え、変わったものだけ読み直す。
- 再走査時に消えたディレクトリ（`alab rm` や手動削除）を検出し、索引から除去する。
- `metrics.jsonl` は読み終えたバイトオフセットを保持し、次回は追記分だけ読む（ライブ更新が O(差分)）。
- ポーリングは UI 側から 2〜5 秒間隔。WebSocket は当面使わない（実装コストに見合わない）。
- `max_points` の間引きは等間隔ストライドで始める（見た目に不満が出たら LTTB 等を検討）。

API（いずれも JSON）:

| エンドポイント | 内容 |
| --- | --- |
| `GET /api/tree` | Experiment / Group / Run の階層ツリー（軽量、名前と status のみ） |
| `GET /api/nodes?experiment=&kind=` | run/group の一覧。meta + params(フラット) + summary を結合した「行」 |
| `GET /api/runs/{path}` | 単一 run の全メタ情報 |
| `GET /api/runs/{path}/metrics?keys=&max_points=` | 時系列。`max_points` 超なら間引いて返す |
| `GET /api/groups/{path}/aggregate` | Group 集計 |
| `GET /api/runs/{path}/artifacts` | アーティファクト一覧（名前・サイズ・種別） |
| `GET /files/{path}` | アーティファクト実体。ルート外へのパス脱出を禁止 |

`{path}` はルートからの相対パス（`mnist-cnn/20260814-063012_a1b2_baseline`）。`..` を含むパスは拒否する。既定で `127.0.0.1` のみ listen する。

### 5.2 フロントエンド

素の HTML / CSS / JS（ES Modules、ビルドステップなし）。フレームワークもバンドラも入れない。グラフは依存なしの自前 SVG 折れ線から始め、性能が足りなければ軽量ライブラリを vendoring する。

画面:

1. **Experiment 一覧** — 名前、run 数、最終更新、実行中の数。
2. **Run テーブル**（中心画面）— 1 行 1 run。列は `name / status / duration` + `params.*` + `summary.*` を動的に構成。
   - 列の表示 / 非表示切替、ドラッグ順序変更
   - 列ごとのフィルタ（数値は範囲、文字列は部分一致、status は選択）
   - 任意列でのソート
   - Group は折りたたみ行として表示し、展開で配下 run。group 行には自身の値と集計値を出す。
   - チェックボックスで複数 run / group を選択 → 比較ビューへ
3. **比較ビュー** — 選択した run / group に対して（**両者を同じ表に混ぜられる**。単発 run の
   ベースラインと 10-fold CV の group を並べて比べるため。group のセルは、自身の値が
   あればそれ、無ければ配下 run の平均で、後者には `± std (n=)` を添えて区別する）
   - **params diff**: 全 run で同一のキーは畳んで隠し、差異のあるキーのみ強調表示
   - **metrics 重ね描き**: キーごとに 1 チャート、run ごとに 1 系列。x 軸は step / epoch / 経過時間を切替
   - **summary 表**: 行 = run、列 = summary キー。最良値をハイライト
   - **アーティファクト並置**: 同名アーティファクトを run 横断でグリッド表示（画像の並列比較）
4. **Run 詳細** — meta / params / metrics チャート / summary / アーティファクトビューア / ログ。
5. **Group 詳細** — 集計表（mean ± std）、fold 一覧、fold 重ね描きチャート。

ライブ表示: `running` な run があるときのみポーリングし、テーブルの該当行とチャートを差分更新する。実行中バッジと `stale` バッジを出す。

## 6. CLI

| コマンド | 内容 |
| --- | --- |
| `alab ui` | ローカル UI 起動 |
| `alab ls [experiment]` | run / group を端末に一覧表示 |
| `alab show <path>` | 単一 run の meta/params/summary を表示 |
| `alab reindex` | group の集計を再生成 |
| `alab export [--out runs.parquet]` | run 一覧（meta+params+summary）と metrics を parquet 化 |
| `alab rm <path>` | run / group を削除（確認あり） |

## 7. parquet エクスポート（任意機能）

正のデータはあくまで JSON/JSONL。`alab export` は後から派生物を作るだけで、削除しても情報は失われない。

- `runs.parquet` — 1 行 1 run。meta + フラット化した params + summary。
- `metrics.parquet` — long 形式（`run_id, step, epoch, time, key, value`）。

DuckDB からこれらを読めば SQL 比較もできる、という位置づけにする（ALab 本体は SQL を提供しない）。将来 SQL が欲しくなったら `alab query` をこの上に足す。

## 8. 実装フェーズ

| フェーズ | 内容 | 完了条件 |
| --- | --- | --- |
| M1 | レイアウト仕様 + 記録層（`init` / `log` / `log_summary` / `log_artifact` / `finish`）+ `alab ls` | 学習スクリプトから run を保存でき、ディレクトリを目で確認できる |
| M2 | 索引 + サーバ + Run テーブル UI（フィルタ / ソート / 列選択） | ブラウザで run 一覧を見られる |
| M3 | Run 詳細 + metrics チャート + ライブ更新 | 学習中の loss 曲線がブラウザで伸びる |
| M4 | Group / CV（記録 API・自動集計・UI 表示） | CV の mean ± std が UI に出る |
| M5 | 比較ビュー（params diff / 重ね描き / summary 表） | 複数 run をブラウザで比較できる |
| M6 | アーティファクト一覧・ビューア・並置比較 | 画像を run 横断で並べて見られる |
| M7 | `alab export`（parquet） | DuckDB から SQL で読める |

M1〜M3 が最小構成。M4 以降は独立に足せる。

## 9. 非目標（当面やらないこと）

- リモートサーバ / マルチユーザ / 認証。ローカル単一ユーザ前提。
- DB（SQLite 等）の導入。ファイルシステムを唯一の真実とする。
- 実験の起動・スケジューリング。ALab は記録と閲覧のみ。
- ハイパラ探索。
- UI からの書き込み（notes 編集、run 削除など）。読み取り専用に留める。
- 分散学習での複数プロセス同時書き込み。rank 0 のみが書く前提。
- `log_artifact(mode="symlink")`。ルート外を指す symlink は `/files` の「ルート外脱出禁止」と矛盾し、`..` 拒否だけでは防げない。巨大チェックポイントの需要が実際に出たら、realpath 検証（外部を指すものは配信拒否・一覧では「外部リンク」表示）とセットで再検討する。

## 10. 未決事項

- 巨大な `metrics.jsonl`（>10^6 行）の扱い。間引きをサーバ側で行うが、初回読み込みコストの許容値を実測して決める。
- 新規 run 検出はポーリング毎のフルウォークになる。run 数が数千を超えたときの走査コストも同様に実測して決める。

（解決済み: ULID は `time` + `secrets` で自前実装する。step 省略時は自動採番（§2.2）。アーティファクト既定モードはコピー、symlink は非目標（§9）。）

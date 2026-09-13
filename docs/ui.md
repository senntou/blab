# UI

## 起動

`blab[ui]` の extras が必要。プロジェクトのディレクトリ（またはその下）で実行する。

```bash
blab ui                            # http://127.0.0.1:8420
blab ui --port 9000 --open         # ポートを変え、ブラウザを開く
blab ui --runs-dir /data/experiments/my-research
```

既定では 127.0.0.1 でのみ待ち受ける。`--host` で変えられるが、UI に認証は無い。
リモートのマシンの結果を見るときは SSH のポートフォワードを使う。

```bash
ssh -L 8420:127.0.0.1:8420 gpu-server
```

## 画面

### 名前の絞り込み

summary・metrics・列など、数が多くなるものには絞り込み欄がある。

- 語の文字がその順に出てくる名前が残る。連続していなくてよい（`traiacc` で `train/acc`）
- 空白で区切った語は、すべてに一致する名前だけが残る（`test auc`）
- 先頭に `-` を付けた語を**連続して**含む名前は除かれる（`test auc -classwise`）
- 語を入れている間は、一致度の高い順に並ぶ（ひと続きの一致や、`/` `_` の直後での一致が上）。
  run 一覧の行だけは、選んだ列のソート順のまま
- 大文字小文字は区別しない。入力はブラウザに保存され、画面を開き直しても残る

### Experiments

experiment ごとの run 数、実行中の run 数、最終更新日時の表。列でソートでき、名前で絞り込める。

### run 一覧

experiment 内の run と group の表。

- 列は `resolved.yaml` の構成（component の版と引数）と `summary.json` の値から作られる。
  列の表示・非表示を選べる（列の一覧も名前で絞り込める）
- 列でソートでき、名前や値で絞り込める
- group は折りたたみ行になり、配下の run の summary の mean ± std が表示される
- チェックボックスで run を選び、次の操作ができる
  - **比較**（2 件以上。run と group を混ぜてもよい）
  - **移動** — 所属 group を変える。`blab mv` と同じ操作
  - **削除** — run を `_trash` group に移す（下記）

### run 詳細

| タブ | 内容 |
| --- | --- |
| YAML | `resolved.yaml` |
| 構成 | 構成ツリー。各 component の版、引数とその由来（YAML / `--set` / 実行時 / デフォルト値）、README。同じ component が複数回 build された場合は build ごとに表示される。実行中は仮の記録（まだ呼ばれていない build は出ない） |
| summary | `summary.json` の値の表 |
| metrics | `metrics.jsonl` のグラフ |
| アーティファクト | `artifacts/` のファイル。画像・テキスト・CSV はその場で表示される |
| ソース | run にコピーされた component のソース |
| 外部ファイル | `data.json` の内容。run にコピーされた小さいファイルは中身も表示される |
| ログ | `logs/stdout.log` / `logs/stderr.log` |
| 環境 | `env.json` |

### group 詳細

| タブ | 内容 |
| --- | --- |
| run | 配下の run の表と集計 |
| 重ね描き | 配下の run の metrics を重ねたグラフ |
| 所属の変更 | run の所属 group の変更 |

### 比較

run 一覧で選んだ run / group を並べる。

| タブ | 内容 |
| --- | --- |
| 構成の diff | 構成ツリーの差分（component の版と引数） |
| コードの diff | 版が違う component のソースの差分 |
| metrics | metrics を重ねたグラフ |
| summary | summary の表 |

### Components / component 詳細

一覧には component ごとの id、タグ、ラベル、使った run の数が表示される。
詳細には次のタブがある。

| タブ | 内容 |
| --- | --- |
| README | `README.ja.md` / `README.md` |
| ソース | 版を選んでソースを表示 |
| diff | 任意の 2 つの版の差分 |
| 使った run | この component を使った run を版ごとに表示 |

## 削除とゴミ箱

UI の「削除」はファイルを消さず、run を experiment 内の `_trash` group に移す。

- `_trash` の run は、run 一覧と experiment の集計には表示されない
- run 一覧の「ゴミ箱」から `_trash` group を開ける。そこから「移動」で元に戻せる
- ディレクトリを完全に消すときは CLI の `blab rm <run のパス>` を使う
- `blab ls` では `_trash` も通常の group として表示される

## 表示の更新

表示は自動では更新されない。最新の状態を見るには、トップバーの「更新」を押す（選択や絞り込みの入力など、
ブラウザに保存していない操作途中の状態はリセットされる）。`running` のまま 60 秒以上
heartbeat が更新されていない run は `stale` と表示される（プロセスが強制終了された場合など）。

## UI が書き込むもの

UI が保存先に対して行う書き込みは、run の所属 group の変更（削除を含む）だけで、`blab mv` と同じ処理を呼ぶ。
表の列の表示設定や開いていたタブなどは、ブラウザの localStorage に保存される。

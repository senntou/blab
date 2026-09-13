# CLI

各コマンドのオプションは `blab <command> --help` でも確認できる。

プロジェクトが必要なコマンドは、カレントディレクトリから上に向かって `blab.json` を探す。
探し始める場所は `blab --dir <path> <command>` で変えられる。

## プロジェクト

| コマンド | 内容 |
| --- | --- |
| `blab init [path] [--name NAME] [--runs-dir DIR]` | `blab.json`・`components/`・`experiments/` を作り、`.gitignore` に追記し、グローバル索引に登録する。既存のプロジェクトでは索引への再登録だけを行う |
| `blab link <path>` | 別のプロジェクトをグローバル索引に登録する。別プロジェクトの component を参照するときに使う |
| `blab projects [--json]` | グローバル索引に登録されたプロジェクトの一覧 |

## component

| コマンド | 内容 |
| --- | --- |
| `blab new <id>` | 雛形（`main.py` と `README.ja.md`）を作る |
| `blab components [--tags TAG ...] [--json]` | 一覧。作業コピーのハッシュ、ラベル、タグ |
| `blab show <id>` | README、ラベル履歴、この component を使った run |
| `blab tag <id> <label> [--note TEXT] [--from-run RUN]` | 版にラベルを付け、`components/.frozen/` にコピーする |
| `blab diff <ref> <ref>` | 2 つの版のソースの差分。`<ref>` は `id`（作業コピー）、`id@label`、`id@sha256:...` |

## 実行

| コマンド | 内容 |
| --- | --- |
| `blab check <yaml> [--set PATH=VALUE ...] [--name NAME] [--group GROUP] [--json]` | 事前検証だけを行う。問題があれば終了コード 1 |
| `blab run <yaml> [--set PATH=VALUE ...] [--name NAME] [--group GROUP] [--runs-dir DIR] [--no-capture]` | 事前検証、凍結、実行。run が正常終了しなければ終了コードは 0 以外 |
| `blab run <run のパス> [--runs-dir DIR] [--no-capture]` | 過去の run を再実行する |

| オプション | 内容 |
| --- | --- |
| `--set PATH=VALUE` | YAML の値を 1 か所上書きする（例: `--set run.lr=1e-4`）。複数回指定できる |
| `--name` | run 名。YAML の `name` より優先 |
| `--group` | 所属 group。YAML の `group` より優先 |
| `--runs-dir` | 保存先 |
| `--no-capture` | stdout / stderr を捕捉しない。デバッガや進捗バーを使うとき |

## run の管理

| コマンド | 内容 |
| --- | --- |
| `blab ls [experiment] [--runs-dir DIR]` | run と group の一覧。group には summary の mean ± std を表示する |
| `blab show <run / group のパス>` | meta、summary、`resolved.yaml`（group は集計） |
| `blab mv <run のパス> [--group NAME] [--runs-dir DIR]` | 所属 group を変える。`--group` を省略すると experiment の直下に戻す |
| `blab rm <run / group のパス> [-y]` | ディレクトリを削除する。確認があり、元に戻せない |
| `blab ui [--port 8420] [--host 127.0.0.1] [--open] [--runs-dir DIR]` | UI を起動する（[UI](ui.md)） |

## 環境変数

| 変数 | 内容 |
| --- | --- |
| `BLAB_RUNS` | 保存先。`--runs-dir` や設定ファイルより優先される |
| `BLAB_STRICT` | `1` にすると、記録系のエラーを警告ではなく例外にする |
| `BLAB_HOME` | グローバル索引（`projects.json`）の置き場所。既定は `~/.blab` |

保存先は次の順で決まる。相対パスはプロジェクトルートからの相対。

1. 環境変数 `BLAB_RUNS`
2. `--runs-dir`
3. `blab.local.json` の `runs_dir`
4. `blab.json` の `runs_dir`
5. `<project>/runs/`

# blab

ファイルシステムを唯一のインターフェースとする、ローカル実験管理ツール。

既存の実験管理ツールは**値**（params / metrics）を記録するが、**その実験が何で組まれて
いたのか**は git commit という不透明な塊に丸投げしている。blab は run から
**ワンクリックで構成要素のコードと日本語の説明に辿れる**ことを中心に据える。
そして逆に、**その component を使った全 run** も引ける。

- **記録層**（`import blab`）— 標準ライブラリのみ。規約どおりのディレクトリに書くだけ。
- **閲覧層**（`blab ui`）— そのディレクトリを読み取り専用で走査してブラウザに出す。

2 層は互いを知らない。blab を使わずに手で規約どおりのディレクトリを作っても UI で
閲覧できるし、記録層だけ使って UI を使わなくてもよい。DB を持たないので、保存済み
ディレクトリは `rsync` / `scp` でそのまま持ち運べる。

**「沈黙による嘘をしない」** — これが blab の全機構を貫く原則。捕まえられない情報が
あること自体は許容するが、捕まえられなかったことを黙って隠さない。dirty 実行、
未記録の import、記録できなかった引数は、すべて UI に出る。

設計の詳細は [docs/design.md](docs/design.md)、レイアウト仕様は
[docs/layout.md](docs/layout.md) を参照。

## インストール

```bash
uv pip install -e '.[ui]'   # UI 込み。記録層だけなら extras 不要
```

## component — 実験の構成要素

model / dataset / metric / augmentation といった**実験ごとに differ る部品**を、
レジストリに住む**自己完結した 1 ファイル**として登録する。

```bash
blab new mlp_classifier                    # .blab/components/mlp_classifier/ に雛形
$EDITOR .blab/components/mlp_classifier/current/mlp_classifier.py
$EDITOR .blab/components/mlp_classifier/README.ja.md
blab register mlp_classifier v1 --note "初版" --tags model
```

```python
# .blab/components/mlp_classifier/current/mlp_classifier.py
import torch.nn as nn
import blab


@blab.entry                                # 入口はちょうど 1 つ
class MlpClassifier(nn.Module):
    def __init__(self, k: int, hidden: int = 64):
        super().__init__()
        self.backbone = blab.load("mlp_backbone@v2")   # 依存は binding として記録される
        self.head = nn.Linear(self.backbone.out_features, k)

    def forward(self, x):
        return self.head(self.backbone(x))
```

規則は 3 つだけ。

| | |
| --- | --- |
| 1 ファイルで自己完結 | import 先はサードパーティのパッケージか、`blab.load()` で引く別の component だけ |
| 入口は `@blab.entry` で 1 つ | `blab register` が登録時に検査する |
| 他の component を継承しない | **継承は依存を記録から隠し、合成は依存を記録に出す** |

共有基底クラスや共通ユーティリティはレジストリに入れず、自分の pip パッケージ
（`pip install -e`）へ。安定して育つフレームワーク的なコードは blab に入れるものではない。

**version は人間が付ける自由文字列**（`v1` / `k-variable` / `2026-baseline`）。
登録済み version は不変で、編集は `current/` に対して行う。`current/` が登録済み版と
違うまま version 未指定で `load` すると**エラーで止まる**（編集したつもりで古いコードを
黙って実行する事故を防ぐ）。開発中は `BLAB_DEV=1`（または `BLAB_DEV=<id>,<id>`）で
`current/` をそのまま実行でき、その run には dirty バッジが付き、ソースが run に残る。

## 実験を回す

```python
import blab

with blab.init(experiment="cifar100", name="distill", params={"lr": 3e-4, "T": 4.0}) as run:
    # 既存 component は id 参照だけで使える（コピー不要）
    student = blab.load("mlp_classifier@v3", role="student", k=100)
    teacher = blab.load("resnet50_classifier@v1", role="teacher", k=100)
    train   = blab.load("cifar100_train@v2", role="train", aug="randaug")
    acc     = blab.load("top1_accuracy@v1")

    # ここから先は完全に普通の Python。blab は何も強制しない
    for epoch in range(100):
        run.log({"train/loss": loss}, epoch=epoch)
    run.log_summary({"test/acc": acc(preds, labels)})
    run.log_artifact("outputs/confusion_matrix.png")
```

`blab.load()` は **entry を args で呼んで戻り値を返す**（クラスならインスタンス）。
何を何の引数で使ったかが `components.json` に記録され、run テーブルの列にもなる。
記録されるのは **run の中で** 呼ばれた load だけ（ノートブックでの探索は記録しない）。

```python
# Group（CV）— 抜けるときに配下 run の summary を自動集計する
with blab.group(experiment="cifar100", name="cv5", group_kind="cv") as g:
    for fold in range(5):
        with g.run(name=f"fold{fold}", params={"fold": fold}) as run:
            ds = blab.load("cifar100_fold@v1", role="train", fold=fold)
            ...
    # fold 平均では作れない値は group 自身に持たせる（集計とは別枠。reindex しても消えない）
    g.log_summary({"oof/acc": 0.931, "wilcoxon_p": 0.02})

# 記録済みの run を開き直す（評価が学習の後になるとき）
with blab.open("a1b2") as run:            # パス / ULID / 短 ID / 名前で引ける
    probe = blab.load("linear_probe@v1")  # 後から使った component も積まれる
    run.log_summary({"probe/acc": 0.71})
```

`blab.open()` の既定では status も実行時間も書き換えない（後付けの記録が、終わった実験の
記録を塗り替えないため）。走査で複数の run に一致する指定は曖昧としてエラーにする。

実行したスクリプト自身は `run/code/` に自動 snapshot される。`import` した自作モジュール
（project root 配下でインストールされていないもの）も `code/_first_party/` に入り、
**辿れなかった import は `unresolved_imports` に残って UI に「未記録の参照あり」と出る。**

保存先は `BLAB_DIR` → cwd から上位に `blab.json` を探索 → `./.blab` の順に決まる。
`blab.init(dir=...)` で明示指定もできる。プロジェクトをまたいで 1 つのルートを
共有したい場合は `BLAB_DIR` を設定する。

### ポリシー（任意）

`blab.json` に書くと、記録の規律を緩く検査できる。

```jsonc
{
  "schema_version": 1,
  "require_components": true,      // binding ゼロの run を許さない
  "require_tags": ["model"],       // これらのタグを持つ component が 1 つ以上必要
  "on_missing": "warn"             // error | warn | ignore（既定 warn）
}
```

`error` でも**値は全部書き切ってから**例外を投げ、status は `finished` のまま
`meta.json` の `policy_violations` に違反を残す。ポリシー違反は記録の規律の問題であって、
実験の失敗ではない。

## CLI

| コマンド | 内容 |
| --- | --- |
| `blab ui [--port 8420] [--open]` | ローカル UI を起動（既定で 127.0.0.1 のみ listen） |
| `blab new <id>` | component の雛形を作る |
| `blab register <id> <version> [--note] [--tags]` | `current/` を凍結して登録 |
| `blab components [--tags model]` | component 一覧 |
| `blab show <id>[@<version>]` | component の説明・ソース・version 履歴 |
| `blab diff <id>@v1 <id>@v3` | version 間の diff |
| `blab ls [experiment]` | run / group を端末に一覧表示 |
| `blab show <path>` | 単一 run の meta/params/summary を表示 |
| `blab reindex` | group の集計を再生成 |
| `blab rm <path>` | run / group を削除（確認あり） |

## UI

`blab ui` で以下が見られる。すべて読み取り専用。

1. **Experiment 一覧** — run 数 / 実行中の数 / 最終更新
2. **Run テーブル** — 列は **構成（binding）** / `params.*` / `summary.*` から動的構成。
   「どの version を使った run か」でソート・フィルタできる。group は折りたたみ行
3. **Run 詳細** — meta / **構成パネル** / metrics チャート / アーティファクト / ログ
   - 構成パネルが blab の要。binding を `role / id@version / args` で並べ、クリックで
     component 詳細へ。内部で load された依存は木として、引数として渡された参照は
     `→ #n` として示す
   - この run に残っているコード（entrypoint / first-party / dirty component）をその場で表示
4. **Component 一覧** — id / タグ / 最新 version / 使用 run 数 / 最終使用日
5. **Component 詳細** — blab の主画面。README、ソース（version 選択可）、登録履歴と
   任意 2 版の diff、そして **逆引き: この component を使った run 一覧**（version 別に
   分け、metric の分布付き）
6. **Group 詳細** — group 自身の値、mean ± std の集計表、fold 重ね描き
7. **比較ビュー** — **構成の diff + version が違う component のソース diff** /
   params diff / metrics 重ね描き / summary 表 / アーティファクト並置。
   **run と group を混ぜて比較できる**（単発 run のベースライン vs 10-fold CV の group）

比較が「params の数値 diff」ではなく「**構成の diff + 変わった部分のコード diff**」になる。
研究者の頭の中は常に「baseline と同じ、ただし loss だけ新しいやつ」という形をしているので、
UI がその形で答える。

学習中の run はライブ更新される（`running` があるときのみ 3 秒間隔でポーリング）。
heartbeat が 60 秒以上途絶えた `running` は `stale` と表示される。

## 環境変数

| 変数 | 内容 |
| --- | --- |
| `BLAB_DIR` | ルートディレクトリ |
| `BLAB_DEV` | `1` で全 component、`id1,id2` で名指しした component を `current/` から実行 |
| `BLAB_STRICT` | `1` で記録の失敗を握らずに送出する（既定は警告に落とす） |

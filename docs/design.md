# blab 設計方針

実験管理ツール **blab** の設計。ALab（metric とハイパラを記録するだけの実験管理ツール）を
土台に、**実験の構成要素そのものを一級市民にする**方向へ作り直したもの。

ALab 時代の設計は [design.alab.md](design.alab.md)、そのレビューは [review.alab.md](review.alab.md) に残してある。

## 0. なぜ作り直すか

既存の実験管理ツール（MLflow, W&B, ALab）は**値**（params / metrics）を記録するが、
**その実験が何で組まれていたのか**は git commit という不透明な塊に丸投げしている。
理論上は再現できるが、半年後に `a1b2c3d` を checkout して `models/` を読む人はいない。
実際に必要なのは「この run の model は何だったのか」への即答であって、世界全体の復元ではない。

blab は run から**ワンクリックで構成要素のコードと日本語の説明に辿れる**ことを中心に据える。
そして逆に、**その component を使った全 run** も引ける。

### 約束すること / しないこと

| | |
| --- | --- |
| 約束する | 表示している情報が、**実際に動いたもの**であること |
| 約束する | 記録できなかったことを、**記録できていないと表示**すること |
| 約束しない | 実験の完全な再現性（環境・データ実体・乱数まで含めた復元） |

**「沈黙による嘘をしない」** — これが blab の全機構を貫く唯一の原則である。捕まえられない
情報があること自体は許容するが、捕まえられなかったことを黙って隠すのは許容しない。

## 1. 決定事項サマリ

| 論点 | 決定 |
| --- | --- |
| 世界の構成要素 | **component** と **entrypoint** の 2 つだけ。`trainer` という概念は持たない |
| component の実体 | レジストリに住む**自己完結した 1 ファイル**。id == ファイル名 |
| component の import 先 | サードパーティのパッケージ、または `blab.load()` で引く別の component のみ |
| component 間の継承 | **不可**（合成のみ）。依存を記録に出すため |
| entrypoint | あなたが実行したファイル。run ディレクトリに**自動 snapshot** |
| カテゴリ / kind | **持たない**。全 component を公平に扱う。タグは任意 |
| バージョン | **人間が付ける自由文字列**（`v1` / `k-variable` など）。登録順が唯一の順序 |
| バージョン整合性 | `current/` が登録済み版と異なればエラー。人間が番号を上げる |
| 開発中の逃げ道 | `BLAB_DEV=1` で dirty 実行可。ソースを run に snapshot し dirty と表示 |
| `blab.load()` | **実体化してインスタンスを返す**。args を記録する |
| component 間の参照 | 宣言させず、`load` の戻り値の同一性から**観測**する |
| 階層構造 | ALab を踏襲。Experiment / Group / Run のディレクトリ 1:1。CV も残す |
| 保存形式 | JSON / JSONL。DB を持たない。ファイルシステムが唯一の真実 |
| 記録層と閲覧層 | 互いを知らない。閲覧層は読み取り専用 |

## 2. 全体像

```
┌───────────────────────────────┐
│ レジストリ（blab/components/）   │  再利用される部品。1 回だけ登録し、以後 id 参照。
└───────────────┬───────────────┘
                │  blab.load("id@v3")
┌───────────────┴───────────────┐
│ entrypoint（あなたのスクリプト）  │  配線と学習ループ。実験ごとに書く。run に snapshot。
└───────────────┬───────────────┘
                │  ファイルシステム（＝唯一のインターフェース）
┌───────────────┴───────────────┐
│ 閲覧層（blab ui）               │  ディレクトリを読むだけ。書き込まない。
└───────────────────────────────┘
```

component は `blab.load()` で呼ばれる側であり、誰かが最外側で呼ばないと始まらない。
その最外側の 1 ファイルが entrypoint で、entrypoint は component になれない（自分を
`load` できないため）。**したがって「trainer は特別」という規則は要らない。trainer とは
たまたま entrypoint に書かれた学習ループのことである。**

記録層と閲覧層が互いを知らないという ALab の性質はそのまま維持する。手で規約どおりの
ディレクトリを作っても UI で閲覧でき、保存済みディレクトリは `rsync` でそのまま持ち運べる。

## 3. コンポーネントシステム

### 3.1 3 つの概念

| 概念 | 何か | どこに住むか |
| --- | --- | --- |
| **Component（定義）** | id + version + ソース + 説明 + タグ。実験をまたいで共有される静的な存在 | `blab/components/<id>/` |
| **Binding（使用）** | 「この run で `resnet18_linear_classifier@v3` を `k=100`、役 `student` で使った」 | `run/components.json` |
| **Entrypoint** | 実行されたスクリプトそのもの | `run/code/<name>.py` |

component は「登録されたもの」、binding は「使われたこと」。1 run は binding を複数持つ。
これにより **model が複数ある実験**（蒸留の student / teacher など）が自然に表現できる。

### 3.2 レジストリのレイアウト

```
.blab/                                             # ルート（既定 ./.blab、BLAB_DIR で変更可）
├── blab.json                                      # ルートの目印 + ポリシー
└── components/
    └── resnet18_linear_classifier/                # ディレクトリ名 == component id
        ├── component.json                         # メタ情報と version 一覧（登録順）
        ├── README.ja.md                           # 人が書く説明（component 単位・安定）
        ├── current/                               # ← あなたが編集する場所（可変）
        │   └── resnet18_linear_classifier.py
        └── versions/                              # ← 登録済み。不変
            ├── v1/resnet18_linear_classifier.py
            └── v3/resnet18_linear_classifier.py
```

**`component.json`** — このファイルが権威。version の**配列の順序が登録順**であり、それが
唯一の順序の定義である（version id は自由文字列なので大小比較ができない）。`latest` は
配列の末尾を意味する。

```jsonc
{
  "schema_version": 1,
  "id": "resnet18_linear_classifier",
  "entry": "Resnet18LinearClassifier",       // @blab.entry で見つかった名前（登録時に記録）
  "tags": ["model", "classifier"],
  "versions": [
    {"id": "v1", "hash": "sha256:3f9a1c…", "note": "初版", "registered_at": "2026-09-01T10:00:00+09:00"},
    {"id": "v3", "hash": "sha256:8b2d40…", "note": "k を可変に、bias を除去", "registered_at": "2026-09-08T14:20:00+09:00"}
  ]
}
```

`versions/<version>/` の中にはソースファイルだけを置く。メタ情報を二重に持たない。

**`README.ja.md`** — component 単位の説明。version をまたいで安定して育つ「これは何か」を書く。
version ごとの差分は `component.json` の `note` に 1 行で書く。UI は README の最終更新時刻と
最新 version の登録時刻を比べ、README が古い場合は**陳腐化マーカー**を出す。

### 3.3 component ファイルの規則

**規則 1: 自己完結した 1 ファイルであること。**
component はレジストリから import されるため、相対 import（`from ..utils import foo`）は
解決できない。したがって import 先は次の 2 つだけに限る。

- サードパーティのパッケージ（`torch`, `numpy`, `timm`, および**あなた自身の pip パッケージ**）
- `blab.load()` で引く別の component

**規則 2: 入口を `@blab.entry` で 1 つだけ明示すること。**

```python
# blab/components/resnet18_linear_classifier/current/resnet18_linear_classifier.py
import torch.nn as nn
import blab

@blab.entry
class Resnet18LinearClassifier(nn.Module):
    def __init__(self, k: int):
        super().__init__()
        self.backbone = blab.load("resnet18_backbone@v2")   # 依存は binding として記録される
        self.head = nn.Linear(512, k)

    def forward(self, x):
        return self.head(self.backbone(x))
```

ファイル名から推測する命名規約ではなく明示マーカーを採る。曖昧さがなく、補助クラスを
同じファイルに置けて、かつ `blab register` が**登録時に**「import して entry が
ちょうど 1 つあるか」を検査できる。登録が単なるコピーではなく検証を伴う。

**規則 3: 他の component のクラスを継承しない。**

| | 可否 |
| --- | --- |
| サードパーティのクラスを継承（`nn.Module`, `Dataset` …） | **可** |
| 同一ファイル内のクラスを継承 | **可** |
| 他の component のクラスを継承 | **不可** |

理由は blab の存在意義に直結する。**継承は依存を記録から隠し、合成は依存を記録に出す。**
`class MyModel(SomeBase)` と書いた瞬間、`MyModel` の挙動の半分は別ファイルにあり、
「ワンクリックで見えるコード」が単体では理解できないものになる。合成なら依存が
`components.json` に観測された事実として現れ、UI からも辿れる。

この規則の代償として、共有基底クラスや共通ユーティリティをレジストリ内に置けない。
そこで次の線を引く。

> **レジストリ = 実験ごとに differ る部品。共有基底クラス・共通ユーティリティ = 自分の
> pip パッケージ（`pip install -e`）。**

安定して育つフレームワーク的なコードは blab に入れるものではない。そちらは普通の
パッケージなので継承も相対 import も自由である。

### 3.4 バージョニング

**version id は人間が付ける自由文字列**（`"v1"`, `"v2"`, `"k-variable"`, `"2026-baseline"`）。
内容ハッシュを識別子にはしない。番号に人間の意図が乗り、`component.json` の `note` と
対応して「v3 にしたとき BN を外した」という記憶と接続するからである。UI でワンクリックで
説明が見られるので、番号の意味を忘れることがない。

**登録済み version は不変。** 編集は `current/` に対して行い、`blab register` で凍結する。

**整合性ゲート** — `blab.load()` は次を検査する。

1. id を解決。レジストリに無ければ**エラー**（＝「未登録のものは使えない」の実現）
2. version 未指定なら `component.json` の `versions` 末尾（latest）に解決
3. **version を明示していない load に限り**、`current/<id>.py` のハッシュを解決した
   version（＝latest）のハッシュと比較
   - **一致** → `versions/<v>/` から import し、binding を記録する
   - **不一致** → エラー：`current が v3 と異なります。blab register <id> <新version> してください`
   - `current/` が存在しない（clone / rsync 直後など）→ 検証をスキップして version をそのまま使う
   - **`id@v1` のように version を明示した load は検査しない。** そのゲートが守るのは
     「編集したつもりで古いコードを黙って実行する」事故だが、version を明示するのは
     「その版を使う」という表明なので、そこに事故はない

import 元は必ず**凍結された `versions/<v>/`** であって `current/` ではない。したがって
「記録された version と実際に動いたバイト列が違う」は原理的に起こらない。

このゲートが本当に守っているのは記録の正しさではなく、**「編集したつもりで古いコードを
黙って実行してしまう」事故**である。記録の方は import 元が凍結版であることで既に保証されている。

**開発中の逃げ道** — 新しいモデルを書いている最中に 1 日 40 回登録するのは不可能なので、
`BLAB_DEV=1`（または `blab.load(..., dev=True)`）を用意する。

指定の効き方は 2 種類に分ける。

| 指定 | 範囲 | version を明示した load |
| --- | --- | --- |
| `blab.load(..., dev=True)` | その load だけ。**明示的** | 矛盾なのでエラー |
| `BLAB_DEV=1` / `BLAB_DEV=<id>,<id>` | プロセス全体 / 名指しした id。**包括的** | **version が勝つ**（dev にしない） |

包括的な指定は「pin していないものは編集中のものを使う」という意味に留める。
そうしないと、1 つの component を編集している間だけ `BLAB_DEV=1` を付けたときに、
スクリプト中で version を pin した無関係な load まで巻き込んで落ちてしまう。

- `current/` から import して実行する
- binding は `{"version": null, "dirty": true, "hash": "…"}` として記録する
- **そのソースを run に snapshot する**（`code/_components/<id>.py`）。version 参照が使えない
  ぶん、内容そのものを残して嘘をつかない
- run に **dirty バッジ**を表示する

**複数バージョンの同時ロード** — `sys.modules` のキー衝突を避けるため、合成モジュール名
`blab._components.<id>__<version>` で載せる（`importlib.util.spec_from_file_location`）。
同一 (id, version) の再ロードは `sys.modules` のキャッシュを再利用する。ただしこの名前は
**レジストリをまたぐと一意でない**ので（別プロジェクトの同名 version）、キャッシュにある
モジュールのファイルが違う場合はハッシュを足した名前で載せ直す。

### 3.5 タグ（カテゴリは持たない）

kind を必須の enum にすると必ず判断に困る（tokenizer は model か dataset か、
augmentation はどちらか）。困る分類は運用されない。また**合成の情報は entrypoint の
コードが持っている**ので、kind は構造上不要である。

タグは UI のフィルタと一覧のグルーピングのためだけにある任意の文字列とする。

代償として、「実験時は必ず model を指定」のような**必須ロールの検査が弱くなる**。
`blab.json` のポリシーでタグベースの緩い検査だけ提供する。

```jsonc
{
  "schema_version": 1,
  "require_components": true,          // binding ゼロの run を許さない
  "require_tags": ["model"],           // これらのタグを持つ component が 1 つ以上必要
  "on_missing": "warn"                 // error | warn | ignore（既定 warn）
}
```

検査は `init()` ではなく **`finish()` 時**に行う。init のあとにモデルを組む書き方は普通にあるため。

**`on_missing: "error"` のときに何が起きるか。** これは §5 の「記録は決して学習を落とさない」と
衝突しうるので、順序を仕様として固定する。

1. summary / metrics / meta を**全部書き切る**
2. `status` を **`finished` のまま**確定する（学習は成功しており、値も正しい。
   ポリシー違反は記録の規律の問題であって、実験の失敗ではない）
3. 違反の内容を `meta.json` の `policy_violations` に記録する
   （例: `["require_tags: 'model' を持つ component が無い"]`）。UI はこれをバッジで出す
4. **そのうえで**例外を投げる

つまり `error` の実効的な効果は「プロセスの終了コード」と「UI のバッジ」だけであり、
データを失わせない。§3.4 の整合性ゲートが**事故を防ぐため**に実行前に止めるのに対し、
こちらは**規律のため**に事後に知らせるものなので、この非対称は意図的である。
既定は `warn` のままとする。

なお `error` を選んだ場合、8 時間の学習を終えてから知らされるのでは遅い。初回 `log()` の
時点で binding がゼロなら**警告だけ**出すという早期検知を併せて実装する（load が log より
後に来る書き方もありうるので、ここで止めはしない）。

### 3.6 `blab.load()` のセマンティクス

```python
blab.load(spec, *, role=None, dev=None, args=None, **kwargs)
```

- `spec` は `"id"` または `"id@version"`
- **entry を args で呼び、その戻り値を返す。** クラスならインスタンス、ファクトリ関数なら
  返された関数。規則を 1 本に統一する
- `kwargs` は `args` にマージされる。`role` / `dev` / `args` は blab の予約語なので、
  component の引数名がこれらと衝突する場合は `args={...}` の形で渡す
- run の外（ノートブックでの探索など）でも呼べる。その場合は単に記録しないだけ

**なぜ実体化して返すのか。** args を記録できることが決定的である。`k=100` が記録に
載らなければ「実験の詳細を持ちたい」という目的を果たせず、run テーブルの列にもならない。
クラスやファクトリを返す方式にすると args を別途「宣言」させることになり、
**宣言が実際と食い違いうる**という、この設計が排除したはずの問題が復活する。
返り値を継承できないという制約は、§3.3 規則 3 と整合するので代償ではない。

**引数の記録** — 3 通りに分けて扱う。

| 引数の種類 | 記録 |
| --- | --- |
| JSON 化できる値 | そのまま（`{"k": 100}`） |
| `blab.load` が返したオブジェクト | **同一性から検出**して binding 参照（`{"$ref": 1}`）。DAG を人間が宣言しなくてよい |
| それ以外の live object | `{"$unrecorded": "Tensor"}`。推測しない |

同じ component を複数回 `load` した場合、`(id, version, role, args)` が完全一致するものは
1 件に畳み、args が違えば別 binding として残す（CV の fold ごとの dataset など）。

**内部依存の記録（`loaded_by`）** — 上の `{"$ref": n}` は「**引数として渡された**」という
データフローの記録である。したがって、component が自分の `__init__` の中で `blab.load()` を
呼んだ場合（§3.3 の `self.backbone = blab.load("resnet18_backbone@v2")`）は**ここに乗らない**。
backbone は親の引数ではないからである。構成の親子関係は args とは別のフィールドで記録する。

| フィールド | 意味 | 取り方 |
| --- | --- | --- |
| `loaded_by: <index>` | この load が、どの binding の実体化中に起きたか（**呼び出しの包含**） | load スタックの先頭 |
| `args: {"$ref": n}` | このオブジェクトが、どの binding から**引数として渡された**か（**データフロー**） | 引数オブジェクトの同一性 |

両者は別物なので、**両方記録する。** `loaded_by` は entrypoint 直下の load では `null`。
実装は contextvar の load スタックで、entry を呼ぶ前に push し、返ったら pop するだけ。
§6.2 の構成パネルの DAG は `loaded_by` を骨格として木に描き、`$ref` を横の辺として重ねる。

**規約: component は依存を `__init__` の中で load すること。** `forward()` の初回呼び出しなど
実体化の外で遅延 load した場合、その時点の load スタックは空なので `loaded_by` は `null` に
なる。これは**実際に起きたことをそのまま記録する**ということであり、推測して親を捏造しない
（§0 の原則）。遅延 load が実運用で問題になるなら、呼び出し元フレームの `__file__` が
どの `versions/<v>/` 配下かを見て `loaded_from` として補う余地を残す。

entry が例外を投げた場合も binding は残し、`"failed": true` を付ける。

### 3.7 登録フロー

```bash
blab new <id>                                    # current/<id>.py と README.ja.md の雛形を作る
blab register <id> <version> [--note "…"] [--tags model,classifier]
```

`blab register` は次を行う。

1. `current/<id>.py` を読む
2. 別プロセスで import し、`@blab.entry` がちょうど 1 つあることを検査
3. ハッシュを取る。既存 version と同一内容なら**拒否**（登録すべき変更がない）
4. version id が既存と重複していれば**拒否**
5. `versions/<version>/` へコピーし、`component.json` の `versions` 末尾に追記

日常の運用は次のようになる。`current/` を編集 → 実験を回す → エラー「current は v3 と
異なります」 → `blab register resnet18_linear_classifier v4 --note "k を可変に"` → 実行。

## 4. ディレクトリレイアウト（Experiment / Group / Run）

ALab を踏襲する。Experiment / Group / Run はすべてディレクトリで、種別は直下の
`meta.json` の `kind` で判別する。階層構造とディレクトリ構造が 1:1 で対応し、Group は
ネストできる。

```
.blab/
├── blab.json
├── components/                                 # §3.2
└── cifar100/                                   # Experiment（kind: experiment）
    ├── meta.json
    ├── 20260910-063012_a1b2_distill/           # Run（kind: run）
    │   ├── meta.json
    │   ├── components.json                     # ★ blab で追加
    │   ├── params.json
    │   ├── metrics.jsonl
    │   ├── summary.json
    │   ├── code/                               # ★ blab で追加
    │   │   ├── train_distill.py                #   entrypoint の snapshot
    │   │   ├── _first_party/                   #   entrypoint が import した自作モジュール
    │   │   └── _components/                    #   dirty 実行時のみ
    │   ├── artifacts/
    │   └── logs/
    └── 20260910-070000_c3d4_cv5/               # Group（kind: group）
        ├── meta.json
        ├── summary.json
        ├── artifacts/
        ├── 20260910-070001_e5f6_fold0/
        └── …
```

命名規則 `{YYYYMMDD-HHMMSS}_{短ID 4桁}_{slug}`、日時は UTC 固定、ID は ULID、
`heartbeat_at` によるライブ判定、原子的書き込み（一時ファイル + `os.replace`）、
`metrics.jsonl` の追記オンリー — これらは ALab の仕様をそのまま引き継ぐ。
詳細は [layout.md](layout.md) を参照。

### 4.1 `components.json`

```jsonc
{
  "schema_version": 1,
  "bindings": [
    {"index": 0, "id": "resnet18_linear_classifier", "version": "v3", "role": "student",
     "hash": "sha256:8b2d40…", "loaded_by": null, "args": {"k": 100}},
    {"index": 1, "id": "resnet18_backbone", "version": "v2", "role": null,
     "hash": "sha256:77b3de…", "loaded_by": 0, "args": {}},
    {"index": 2, "id": "resnet50_classifier", "version": "v1", "role": "teacher",
     "hash": "sha256:1c77ab…", "loaded_by": null, "args": {"k": 100}},
    {"index": 3, "id": "cifar100_train", "version": "v2", "role": "train",
     "hash": "sha256:4e0912…", "loaded_by": null, "args": {"aug": "randaug"}},
    {"index": 4, "id": "top1_accuracy", "version": "v1", "role": null,
     "hash": "sha256:aa31f0…", "loaded_by": null, "args": {}}
  ],
  "entrypoints": [
    {"name": "train_distill.py", "path": "code/train_distill.py",
     "hash": "sha256:3c91ff…", "recorded_at": "2026-09-10T15:30:12+09:00",
     "argv": "python train_distill.py --lr 3e-4", "unresolved_imports": []}
  ]
}
```

- `index` は `{"$ref": n}` と `loaded_by` の参照先。同一 component を異なる args で
  複数回 load しても一意に指せる
- **`index` は load の開始順に振る**（完了順ではない）。上の例では `index 1` の backbone は
  `index 0` の `__init__` の中で load されるので、student のほうが先に採番される。
  この採番により **`loaded_by` は必ず自分より小さい index を指す**という不変条件が成り立ち、
  UI はトポロジカルソートなしに構成の木を描ける
- component の内部で load されたものも同じ配列にフラットに並ぶ。親子関係を持つのは
  `args` ではなく `loaded_by`（§3.6）
- `entrypoints` は配列。同じ run に後から `blab.open()` で書き足した場合、そのスクリプトも
  積まれる

### 4.2 entrypoint の snapshot

blab は `__main__.__file__` から自分を呼んだファイルを知っているので、ユーザは何も
宣言しなくてよい。`init()` の時点で `code/` にコピーし、ハッシュと `argv` を記録する。

これは学習スクリプトに限らない。後から評価を回す `eval_probe.py` が
`blab.open("a1b2").log_summary({...})` で既存 run に値を書き足すなら、そのスクリプトも
同じ run の `code/` に積まれる。

```
code/
├── train_distill.py     # 2026-09-10 学習
└── eval_probe.py        # 2026-09-14 後から足した probe/acc を出したコード
```

「この後付けの metric は何のコードが出したのか」に答えられる。

entrypoint は component と違って自己完結を**強制できない**（普通の Python スクリプトなので）。
そこで原則を適用する。**first-party import を辿れる範囲で snapshot し、辿れなかったものは
`unresolved_imports` に記録して run ページに「未記録の参照あり」と表示する。**
動的 import や文字列ベースのファクトリは原理的に漏れるので、漏れたことを表示する側で担保する。

**置き場所とパス構造** — snapshot したモジュールは `code/_first_party/` に、
**import パスを写した階層**で置く（ファイルシステム上の相対パスではない）。そのまま読めて、
必要なら import もできる形にするため。

```
code/
├── train_distill.py                 # entrypoint
├── _first_party/                    # entrypoint が import した自作モジュール
│   ├── utils.py                     #   utils
│   └── mylib/
│       ├── __init__.py
│       └── losses.py                #   mylib.losses
└── _components/                     # dirty 実行時のみ（§3.4）
    └── resnet18_linear_classifier.py
```

**「first-party」の定義** — first-party を「**インストールされていないモジュール**」と定義する。
`sys.modules` を走査し、`inspect.getsourcefile` で実体を得て、`sysconfig` の `purelib` /
`platlib` / 標準ライブラリ配下、および blab ルート配下を除外する。そのうえで
**project root 配下にあるものだけ** snapshot する。

`project root` は次の順で決める。

1. `blab.json` の `project_root`（明示指定。相対パスは `blab.json` からの相対）
2. entrypoint のファイルから上に辿って見つかった **git root**
3. entrypoint のあるディレクトリ

**`BLAB_DIR` の親を基準にはしない。** blab のデータディレクトリは
プロジェクトルートである必要がなく（`/data/experiments/blab` のようにコードと無関係な場所に
置ける）、その親を基準にするとコードとの対応が取れないからである。データディレクトリの
可搬性は `code/` に snapshot が入ることで担保されるので、基準を data 側に置く必要はない。

- editable install（`pip install -e`）したパッケージは、実体が project root の外にあれば
  **third-party として扱い snapshot しない**。§3.3 で「共有ヘルパは自分の pip パッケージへ」
  と決めたので、これを snapshot すると数千ファイルを run ごとに複製することになる
- snapshot しなかった third-party は、名前とバージョンを `meta.json` の `env.packages` に
  記録する（実体は残さないが、何を使ったかは残す）

### 4.3 binding と run テーブルの列

binding の args は、run テーブルの列としてもフラット化して出す。

| 列 | 値 | 由来 |
| --- | --- | --- |
| `student` | `resnet18_linear_classifier@v3` | binding（role、無ければ id） |
| `student.k` | `100` | binding の args |
| `train` | `cifar100_train@v2` | binding |
| `lr` | `3e-4` | `params.json` |

component 自体を列にすることで、**「どの version を使った run か」でソート・フィルタできる。**
`params.json` は component の args ではないハイパラ（lr, epochs など）を引き続き担う。

## 5. 記録層 API

```python
import blab

with blab.init(experiment="cifar100", name="distill", params={"lr": 3e-4, "T": 4.0}) as run:
    # 既存 component は id 参照だけで使える（コピー不要）
    student = blab.load("resnet18_linear_classifier@v3", role="student", k=100)
    teacher = blab.load("resnet50_classifier@v1",        role="teacher", k=100)
    train   = blab.load("cifar100_train@v2", role="train", aug="randaug")
    acc     = blab.load("top1_accuracy@v1")

    # ここから先は完全に普通の Python。blab は何も強制しない
    opt = torch.optim.Adam(student.parameters(), lr=3e-4)
    for epoch in range(100):
        for x, y in DataLoader(train, batch_size=128):
            ...
            run.log({"train/loss": loss})
    run.log_summary({"test/acc": acc(preds, labels)})
    run.log_artifact("outputs/confusion_matrix.png")
```

Group（CV）と `open()` は ALab を踏襲する。

```python
# Group（CV）— 抜けるときに配下 run の summary を自動集計
with blab.group(experiment="cifar100", name="cv5", group_kind="cv") as g:
    for fold in range(5):
        with g.run(name=f"fold{fold}", params={"fold": fold}) as run:
            ds = blab.load("cifar100_fold@v1", role="train", fold=fold)
            ...
    # fold 平均では作れない値は group 自身に持たせる
    g.log_summary({"oof/acc": 0.931, "wilcoxon_p": 0.02})

# 記録済みの run を開き直す（評価が学習の後になるとき）
blab.open("a1b2").log_summary({"probe/acc": 0.71})
```

### 設計上の約束

- **記録は決して学習を落とさない。** ログ書き込み中の例外は握って警告に落とす
  （`BLAB_STRICT=1` で厳格モード）。この原則の意図は「**データを失わせない**」ことであり、
  次の 2 つは例外として意図的に例外を投げる。
  - **§3.4 の整合性ゲート**は学習の**前**に止める。古いコードを黙って実行するのは、
    記録の失敗より重い事故だからである
  - **§3.5 のポリシー検査（`error` 時）**は全部書き切った**後**に投げる。データは既に
    永続化されているので、原則の意図には反しない
- **`blab.load()` は学習コードを縛らない。** 実体化して返すだけで、実体化の順序も
  配線も学習の流れも entrypoint の自由。blab は DI コンテナにならない
- **記録層の依存は最小限。** 標準ライブラリのみで動く（UI は extra 依存）
- ネストした `blab.init` は許可しない

### モジュール構成

```
blab/
├── __init__.py        # init / group / open / load / entry の公開 API
├── layout.py          # パス規約・命名・ディレクトリ探索
├── schema.py          # meta/params/summary/components のスキーマとバリデーション
├── registry.py        # ★ component の解決・整合性ゲート・import
├── entry.py           # ★ @blab.entry マーカー
├── snapshot.py        # ★ entrypoint の snapshot と first-party import 追跡
├── run.py             # Run オブジェクト
├── group.py           # Group オブジェクト
├── io.py              # 原子的書き込み、JSONL writer/reader
├── index.py           # ディレクトリ走査とインメモリ索引（component 逆引きを含む）
├── aggregate.py       # Group 集計
├── cli.py             # blab ui / new / register / components / show / diff / ls / rm
├── server/app.py      # FastAPI アプリ
└── static/            # 素の HTML / CSS / JS
```

## 6. 閲覧層

### 6.1 API

ALab の API を引き継ぎ、component 関連を追加する（★）。

| エンドポイント | 内容 |
| --- | --- |
| `GET /api/tree` | Experiment / Group / Run の階層ツリー |
| `GET /api/nodes?experiment=&kind=` | run/group の一覧（meta + params + summary + **binding 列**） |
| `GET /api/runs/{path}` | 単一 run の全メタ情報（components.json を含む） |
| `GET /api/runs/{path}/metrics?keys=&max_points=` | 時系列（`max_points` 超は間引き） |
| `GET /api/runs/{path}/code/{name}` | ★ run に snapshot されたソース（entrypoint / first-party / dirty component） |
| `GET /api/groups/{path}/aggregate` | Group 集計 |
| `GET /api/runs/{path}/artifacts` / `GET /files/{path}` | アーティファクト |
| `GET /api/components` | ★ component 一覧（id / tags / latest / 使用 run 数 / 最終使用） |
| `GET /api/components/{id}` | ★ component.json + README + version 一覧 |
| `GET /api/components/{id}/source?version=` | ★ ソース |
| `GET /api/components/{id}/diff?a=v1&b=v3` | ★ version 間の diff（`difflib`） |
| `GET /api/components/{id}/runs` | ★ **逆引き**。この component を使った run 一覧（version 別） |

索引は起動時にルート配下を走査して作り、mtime で差分更新する。component の逆引きは
各 run の `components.json` を走査して逆写像を張る。`metrics.jsonl` は読み終えた
バイトオフセットを保持して追記分だけ読む。ポーリングは UI 側から 2〜5 秒間隔。

### 6.2 画面

1. **Experiment 一覧** — run 数 / 実行中の数 / 最終更新
2. **Run テーブル** — 列は `params.*` / `summary.*` / **binding**（§4.3）から動的構成。
   フィルタ・ソート・列選択、Group は折りたたみ行
3. **Run 詳細** — meta / **構成パネル** / metrics チャート / artifacts / ログ
   - **構成パネル**が blab の要。binding を `role / id@version / args` で一覧し、
     クリックで component 詳細へ。**木の骨格は `loaded_by`** で描き、`{"$ref": n}` は
     横向きの辺として重ねる（§3.6 の 2 つのフィールドをそのまま 2 つの見せ方にする）
   - entrypoint のソースをその場で表示
   - **dirty バッジ**、**「未記録の参照あり」警告**を出す
4. **Component 一覧** — ★ id / タグ / 最新 version / 使用 run 数 / 最終使用日
5. **Component 詳細** — ★ **blab の主画面**
   - `README.ja.md`（陳腐化マーカー付き）
   - ソース（version 選択可）
   - version 一覧（登録順・note 付き）と、**任意 2 版の diff**
   - **逆引き: この component を使った run 一覧。** version 別に分け、summary 列を付けて
     ソート可能にする。metric の分布も出す（「この augmentation は結局効いたのか」への即答）
6. **Group 詳細** — group 自身の値、mean ± std の集計表、fold 重ね描き
7. **比較ビュー** — run / group を混ぜて比較できる
   - **component 集合の diff** ← blab で追加。同 id で version が違えば**ソース diff を展開**
   - **entrypoint の diff**
   - params diff（全 run で同一のキーは畳む）/ metrics 重ね描き / summary 表 / artifacts 並置

比較が「params の数値 diff」ではなく「**構成の diff + 変わった部分のコード diff**」になる。
研究者の頭の中は常に「baseline と同じ、ただし loss だけ新しいやつ」という形をしているので、
UI がその形で答える。

## 7. CLI

| コマンド | 内容 |
| --- | --- |
| `blab ui [--port 8420] [--open]` | ローカル UI 起動（既定で `127.0.0.1` のみ listen） |
| `blab new <id>` | component の雛形を作る |
| `blab register <id> <version> [--note] [--tags]` | `current/` を凍結して登録 |
| `blab components [--tag model]` | component 一覧 |
| `blab show <id>[@<version>]` | component の説明・ソース・使用 run 数 |
| `blab diff <id>@v1 <id>@v3` | version 間の diff |
| `blab ls [experiment]` | run / group を一覧 |
| `blab show <path>` | 単一 run の meta/params/summary/components |
| `blab reindex` | group の集計を再生成 |
| `blab rm <path>` | run / group を削除（確認あり） |

## 8. 実装フェーズ

ALab の記録層・閲覧層のコードを土台に使えるが、レイアウトと命名が変わるので機械的な
置換では済まない。component システムを先に立てて、そこに ALab 相当を載せ直す順で進める。

**2026-09-11 時点で M1〜M8 が実装済み**（M5 / M6 / M8 は ALab から引き継いだ部分が大きい）。
`demo/`（git 管理外）に two moons + MLP の実データがあり、UI で全画面を確認できる。

| フェーズ | 内容 | 完了条件 |
| --- | --- | --- |
| M1 | レジストリ + `@blab.entry` + `register` / `load` + 整合性ゲート | CLI から component を登録し、スクリプトから `load` して使える |
| M2 | 記録層（`init` / `log` / `log_summary` / `log_artifact` / `finish`）+ `components.json` + entrypoint snapshot + `blab ls` | 実験を回すと構成込みで run が保存される |
| M3 | 索引（component 逆引きを含む）+ サーバ + Run テーブル | ブラウザで run 一覧が見られる |
| M4 | **Run 詳細の構成パネル + Component 詳細ページ + 逆引き** | **run から component へワンクリックで辿れる ← blab の核心** |
| M5 | metrics チャート + ライブ更新 | 学習中の loss 曲線がブラウザで伸びる |
| M6 | Group / CV（記録 API・自動集計・UI） | CV の mean ± std が UI に出る |
| M7 | 比較ビュー（component 集合 diff / ソース diff / params diff / 重ね描き） | 2 つの run の「何が違うか」がコードレベルで見られる |
| M8 | アーティファクト一覧・ビューア・並置比較 | 画像を run 横断で並べて見られる |

M1〜M4 が最小構成。ここまでで「実験の詳細を持ちたい」という当初の目的は達成される。

## 9. 非目標（当面やらないこと）

- **完全な再現性の保証。** §0 で明言したとおり、blab が約束するのは想起可能性である
- **`blab run` / replay**（レジストリから実験を起動する、過去 run を再実行する）。
  実現するなら blab が実体化の順序を持つ DI コンテナになる必要があり、
  「trainer の流れを固定しない」という要件と衝突する。
  ただし**レジストリに完結したコードが積まれる構造なので、後から足す余地は残る**
- **component 間の継承**（§3.3 規則 3）
- **共有ヘルパの component 化。** 自分の pip パッケージへ
- **component が複数ファイルに渡ること。** 1 component = 1 ファイル
- リモートサーバ / マルチユーザ / 認証。ローカル単一ユーザ前提
- DB の導入。ファイルシステムを唯一の真実とする
- 実験の起動・スケジューリング・ハイパラ探索
- UI からの書き込み（読み取り専用に留める）
- 分散学習での複数プロセス同時書き込み（rank 0 のみが書く前提）

## 10. 未決事項

- **エディタ支援の劣化。** `Model = blab.load("id@v3")` は動的解決なので返り値が `Any` になり、
  entrypoint 内では定義ジャンプ・補完・型チェックが効かない。component を書いている最中
  （`current/` 内）は普通のファイルなので影響はない。日々どれくらい痛いかは実運用してから
  判断する。耐えられない場合は「レジストリを import 可能なパッケージとして `sys.path` に
  載せる」設計を再検討する
- **共有ヘルパを持てないことの窮屈さ。** §3.3 の代償が実運用でどれだけ効くか。
  自分の pip パッケージへの切り出しで足りるかどうかを実測する
- **entrypoint の first-party import 追跡の精度。** 動的 import は原理的に漏れる。
  漏れたことを表示する方針で足りるかどうか
- **引数名の衝突。** `role` / `dev` / `args` が component の引数名とぶつかった場合の
  `args={...}` フォームで足りるか
- **レジストリが育ったときの整理。** component の削除・アーカイブ・リネームをどう扱うか。
  過去 run が参照している component を消せてはいけない
- **巨大な `metrics.jsonl`（>10^6 行）の初回読み込みコスト。** 実測して決める（ALab から継続）
- **合成モジュールの pickle 化。** component のインスタンスは
  `blab._components.<id>__<version>` というプロセス内合成モジュールのクラスなので、
  `spawn` で起動した子プロセス（`DataLoader(num_workers>0, start_method="spawn")` など）が
  unpickle するとモジュール名を解決できない。Linux 既定の `fork` では `sys.modules` を
  継承するので問題は出ない。必要になったら、この名前を解決する meta path finder を
  `sys.meta_path` に入れる形で塞げる

## 11. 実装で決めたこと

設計に書ききれていなかった細部のうち、実装時に決めて動いているもの。

| 論点 | 決定 |
| --- | --- |
| component id の文字種 | **Python の識別子に限る**（合成モジュール名の一部になるため）。`-` は不可 |
| version id の文字種 | 英数字始まり、`. _ -` を許す自由文字列。`latest` は予約語（配列の末尾の別名） |
| ハッシュ | 生バイトの SHA-256（`sha256:…`）。**正規化しない**。空白の違いも別内容として扱う |
| `blab.load()` のルート解決 | 実行中の run があればその run のルート。無ければ `BLAB_DIR` → 上位探索。`blab.init(dir=...)` で別ルートを指定した実行が、cwd 側の無関係なレジストリを引かないため |
| 合成モジュール名の衝突 | キャッシュにあるモジュールのファイルが違えば、ハッシュを足した名前で載せ直す（§3.4） |
| 既定のルート名 | `./.blab`（プロジェクト直下を汚さない）。共有ルートは `BLAB_DIR` |
| `blab.json` の既定値 | `ensure_root` が既定値を**明示して書く**。挙動を隠さないため |
| binding の畳み込みキー | `(id, version, role, loaded_by, args)`。`loaded_by` を含めるので、別の親の下で起きた同一 load は別 binding になる |
| `$ref` の同一性検出 | weakref を張れるオブジェクトは弱参照で覚える（`id()` の再利用による誤参照を避ける）。張れないものだけ強参照で抱える |
| binding の `entrypoint` | どの entrypoint の実行中に load されたかを添字で持つ。後から `blab.open()` した run で、どのスクリプトが何を使ったかを区別するため |
| 同一 entrypoint の再実行 | `(name, hash, argv)` が同じなら 1 件に畳み、`n_recorded` と `last_recorded_at` を持つ。配列が無限に伸びない |
| README の陳腐化判定 | 最新 version の登録より **1 時間以上**古いときだけマーカーを出す。README を書いた直後に register するのが普通の順序なので、厳密比較だと常時点灯して意味を失う |
| group の binding 列 | group 自身は component を持たないので、配下 run の**共通部分だけ**を出す（fold ごとに違う args は落ちる） |
| run テーブルの列名衝突 | 同じ role / id が複数あるときは `#<index>` を付けて区別する。畳んで消すと別物が同じ列に見える |
| args 列の既定表示 | component 列は既定で表示、args 列は既定で非表示（列ピッカーから出せる）。列数が多すぎて表が読めなくなるため |
| ポリシー検査の失敗 | 検査自体が失敗した場合は警告に落とす（記録の検査で学習を落とさない） |
| first-party の上限 | 1 run あたり 200 ファイル / 1 ファイル 1MB まで。超えた分は `skipped` に理由を残す |

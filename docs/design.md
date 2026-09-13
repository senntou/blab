# blab 設計方針（v2）

実験管理ツール **blab** の設計。

系譜は 3 世代ある。metric とハイパラを記録するだけの **ALab**（[design.alab.md](design.alab.md) /
[review.alab.md](review.alab.md)）、実験の構成要素を一級市民にした **blab v1**
（[design.v1.md](design.v1.md)）、そして本書の **v2** である。

## 0. v1 から何を変えるのか

v1 は **観測**の道具だった。ユーザは普通の Python を書き、blab は横から「実際に何が
起きたか」を見て記録する。`blab.load()` の呼び出しを観測し、entrypoint を snapshot し、
辿れなかったものは「辿れなかった」と表示する。

v2 はこれを**宣言**に反転させる。ユーザは YAML で「何をどう組むか」を宣言し、**blab が
それを読んで組み立て、実行する**。v1 が非目標として明示的に退けていた `blab run`
（design.v1.md §9）を、設計の中心に据える。

### なぜ反転させるのか

v1 の再現性の穴は、model でも dataset でもなく **entrypoint そのもの**だった。
`snapshot.py` は「頑張ってコピーし、辿れなかった import は `unresolved_imports` に残す」
という誠実な実装だが、裏を返せば**学習ループのコードだけは凍結されていない**。動的
import は原理的に漏れる。実験の中で一番よく書き換わる部分が、一番保証の薄い場所にあった。

trainer をコンポーネントにすれば、そこがハッシュで凍結された不変のコードになる。
そのためには「誰が trainer を起動するのか」を決めねばならず、それは blab しかない。
反転はここから必然的に出てくる。

副産物として、**実験そのものが機械の読める 1 個のデータになる**。実験同士の差分が
「コードの diff」ではなく「構成の diff」で取れ、過去の run をそのまま再実行でき、
`params.json`（数値）と `components.json`（部品）という v1 の 2 本立てが 1 本の木に統合される。

### 約束すること / しないこと

| | |
| --- | --- |
| 約束する | **コードの再現** — run から、実際に動いたコードを完全に復元できること |
| 約束する | 表示している情報が、**実際に動いたもの**であること |
| 約束する | 記録できなかったことを、**記録できていないと表示**すること |
| 約束しない | 実行環境の再現（パッケージ版を**記録はする**が、揃えるのはユーザの責任） |
| 約束しない | 数値レベルの一致（CUDA の非決定性、乱数の扱いはプログラム側の問題） |

**「沈黙による嘘をしない」** — v1 から引き継ぐ唯一の原則。捕まえられない情報があること
自体は許容するが、捕まえられなかったことを黙って隠すのは許容しない。

## 1. 決定事項サマリ

| 論点 | 決定 | v1 から |
| --- | --- | --- |
| 実行モデル | **YAML で構成を宣言し、`blab run` が組み立てて実行する** | 反転 |
| 観測モード（普通の Python から `blab.init()`） | **当面廃止**。入口は `blab run` だけ | 廃止 |
| 世界の構成要素 | **コンポーネントだけ**。trainer もコンポーネント | 統合 |
| コンポーネントの実体 | **1 ディレクトリ**。中は自由（相対 import 可） | 1 ファイル → ディレクトリ |
| コンポーネントの置き場所 | プロジェクトの **git 管理下**（`components/`） | 変更 |
| 主キー | **プロジェクト ID + コンポーネント id**（id はディレクトリ名）。**プロジェクトの**ディレクトリ名には依存しない | 新規 |
| 凍結 | **自動**。内容ハッシュで常に凍結する。`blab register` は無い | 自動化 |
| version 名 | 後から貼る**ラベル**（`blab tag`）。ラベル付きだけが git に載る | 降格 |
| 開発中の逃げ道（`BLAB_DEV`） | **廃止**。自動凍結により不要になった | 廃止 |
| 子コンポーネントの渡し方 | **Builder（工場）**として渡し、`.build()` で実体化 | 新規 |
| 引数の記録 | YAML 由来と実行時由来を合体した**最終形を観測して**記録 | 継続 |
| 実験ログの置き場所 | **コードと別パス**。git に載せない | 分離 |
| run の自己完結性 | 使ったコンポーネントのソースを **run に焼き込む** | 強化 |
| 事前検証 | 学習を始める**前に**構成を全部検査する。ここは止める | 新規 |
| ポリシー検査 | 事前検証に吸収。実行中の検査は持たない | 統合 |
| 階層構造 | Experiment / Group / Run。**group は YAML で名乗るだけ**。集計は読むときに導出 | 簡素化 |
| 保存形式 | JSON / JSONL / YAML。DB を持たない | 継続 |
| 記録層と閲覧層 | 閲覧層は読み取り専用 | 継続 |

> 本書のうち §5.1（YAML の記法）、§5.2（別プロジェクト参照）、§7.1（メソッド名 `execute`）
> の 3 点は合意を取らずに決めた。いずれも局所的なので、変えたければ言ってほしい。該当箇所に
> **【要確認】** を付けてある。

## 2. 全体像

```
  experiments/distill.yaml          ← 人が書く。構成の宣言
          │
          │  blab run
          ▼
  ┌───────────────────┐
  │ 解決・凍結 (§4.3)   │  参照を解決し、各コンポーネントを内容ハッシュで凍結
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 事前検証 (§5.3)     │  凍結版から import し、entry と引数名を照合。ここで全部落とす
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 組み立て・実行      │  root を実体化し execute() を呼ぶ。子は Builder として渡る
  └─────────┬─────────┘
            ▼
  runs/cifar100/2026…/             ← 自己完結した記録
    resolved.yaml    全部ハッシュに解決済みの構成
    components/      実際に使ったコードの実体
    env.json / metrics.jsonl / summary.json / artifacts/ / logs/
          │
          │  blab ui（読み取り専用）
          ▼
      ブラウザ
```

## 3. プロジェクトとレイアウト

### 3.1 ディレクトリ

**1 git リポジトリ = 1 プロジェクト**とする。

```
cifar-research/                       # git リポジトリ = プロジェクト
│
├── blab.json                     ★  プロジェクト ID・名前・既定設定
├── components/                   ★  レジストリ（編集する作業コピー）
│   ├── resnet18/                 ★    コンポーネント本体。丸ごとハッシュ・丸ごと凍結
│   │   ├── README.ja.md          ★      人が書く説明
│   │   ├── main.py               ★      @blab.entry はここ（§4.2）
│   │   └── blocks.py             ★      ディレクトリ内は相対 import 自由
│   ├── standard_trainer/         ★
│   ├── .meta/                    ★  ラベル・タグ（§4.4）。ハッシュ対象外
│   │   └── resnet18.json         ★
│   └── .frozen/                  ★  ラベルの付いた版だけ（§4.4）
│       └── resnet18/v3/
├── experiments/                  ★  実験の YAML
│   ├── baseline.yaml             ★
│   └── distill.yaml              ★
│
├── blab.local.json               ✗  このマシンだけの設定
├── .blab/                        ✗  キャッシュ。いつ消しても安全
│   └── frozen/                   ✗    ラベルの無い自動凍結版は全部ここ
│
└── runs/                         ✗  実験ログ（既定の場所。移動可）
    └── cifar100/
        ├── 20260913-063012_a1b2_distill/
        └── 20260913-070000_c3d4_cv5/
```

★ = git 管理下 / ✗ = `.gitignore`（`blab init` が自動で書く）

設計上の要点は 3 つある。

**`components/` と `experiments/` は見える場所に置く。** これらはツールの内部状態ではなく、
**毎日編集するソースコード**である。隠しディレクトリは、ツールが管理する状態のためのもの。

**凍結版は作業コピーの外に出す。** コンポーネントは 1 ディレクトリ単位で、ディレクトリ全体を
ハッシュする（§4.3）。凍結版を `components/resnet18/versions/` のように内側に置くと、凍結版
自身がハッシュ対象に入り、凍結するたびにハッシュが変わる循環が起きる。外に出せば済む。

**`.blab/` には真実を 1 つも置かない。** 索引・解決結果・ラベルの無い凍結版だけを置き、
消しても再構築できる状態を保つ。こうしておけば、キャッシュの不整合が実験の再現性を
脅かすことは原理的に起きない。掃除は `rm -rf .blab/` で終わる。

### 3.2 プロジェクト ID

**ディレクトリ名を同一性の根拠にしない。** `git clone <repo> another-name` でも、CI が
`/work/src` にチェックアウトしても、`mv` しても変わってしまう。絶対パスも `git clone` で
別の場所に移ると壊れる。

代わりに `blab.json` に明示的に持つ。このファイルは git で追跡されるのでクローンしても
付いてくるし、ディレクトリを移動・改名しても壊れない。

```jsonc
// blab.json — git 管理下
{
  "schema_version": 2,
  "project": "cifar-research",        // 人間が読む名前。表示と YAML からの参照に使う
  "project_uid": "01JQ8R7X4K9M2N",    // blab init が生成。これが本当の主キー
  "components_dir": "components",     // 既定値を明示して書く
  "experiments_dir": "experiments"
}
```

コンポーネントの主キーは **`(project_uid, id)`** である。`project` の改名は表示名を変える
だけで、過去 run との対応は切れない。

blab はグローバルに索引を持つ。

```jsonc
// ~/.blab/projects.json — ただのキャッシュ。壊れても再スキャンで直る
{
  "01JQ8R7X4K9M2N": {"name": "cifar-research", "path": "/home/you/work/cifar"},
  "01JQ9T2P8V5W1Q": {"name": "seg-baseline",   "path": "/home/you/work/seg"}
}
```

この索引があるので、カレントディレクトリがどこであろうと id でコンポーネントを解決できる。
「blab が管理する」とは、**パスではなく id で解決すること**であって、実体を隠すことではない。

### 3.3 実験ログの置き場所

コードはホーム、ログは大きい別ディスク、という配置が普通である。しかも**その対応は
マシンごとに違う**ので、git に載る `blab.json` に書くわけにはいかない。

| 優先 | 指定 | 用途 |
| --- | --- | --- |
| 1 | 環境変数 `BLAB_RUNS` | クラスタのジョブ内で scratch に吐く |
| 2 | `blab run --runs-dir <path>` | smoke test を一時ディレクトリに逃がす |
| 3 | `blab.local.json` の `runs_dir` | **このマシンでの置き場所**（gitignore 済み） |
| 4 | `blab.json` の `runs_dir` | チーム共通の既定値（相対パスはプロジェクトからの相対） |
| 5 | 既定 | `<project>/runs/` |

```jsonc
// blab.local.json — git に載らない。マシンごとに書く
{"runs_dir": "/data/experiments/cifar-research"}
```

`runs_dir` は**常にそのプロジェクト専用**とする。大きいディスクを複数プロジェクトで共有する
場合は `/data/experiments/<プロジェクト名>` のように人間が書き分ける。blab が勝手に
サブディレクトリを掘るより、明示的に書いてもらうほうが事故がない。

### 3.4 git 管理の境界

| | git | 消していいか |
| --- | --- | --- |
| `components/`（作業コピー） | ★ | ✗ |
| `components/.frozen/`（ラベル付きの版だけ） | ★ | ✗ |
| `experiments/*.yaml` | ★ | ✗ |
| `blab.json` | ★ | ✗ |
| `blab.local.json` | ✗ | マシン設定なので任意 |
| `.blab/`（キャッシュ + ラベル無しの凍結版） | ✗ | **いつでも可** |
| `runs/` | ✗ | 人間の判断 |

境界の基準は「参照されているか」ではなく **「人間が意味を認めたか」** である。自動凍結は
1 日に何十回も起きるし、smoke test も run を作るので、参照の有無を基準にするとゴミが
参照によって守られてしまう。`blab tag` を叩いたという人間の行為だけを昇格の条件にすれば、
**リポジトリの中身が、人間が意味を認めたものと一致する。**

`experiments/*.yaml` が git に載ることには、地味だが良い副作用がある。**「どういう実験を
やったか」の履歴が git log に残る。** これまでコミットメッセージに埋もれていた情報が、
構造化された差分として読める。

## 4. コンポーネント

### 4.1 実体と規則

コンポーネントは**1 ディレクトリ**である。model / dataset / metric / augmentation だけでなく、
**trainer もコンポーネント**である。blab は種類を区別しない。

```
components/
├── standard_trainer/     # ← これがコンポーネント本体。丸ごとハッシュされ、丸ごと凍結される
│   ├── README.ja.md      #     人が書く説明
│   ├── main.py           #     @blab.entry
│   ├── loops.py          #     相対 import で使える
│   └── schedulers.py
├── .meta/                # ← blab が管理するメタ情報。ハッシュ対象外
│   └── standard_trainer.json
└── .frozen/              # ← ラベルの付いた版（§4.4）
```

**コンポーネント本体のディレクトリには、コードと README しか置かない。** ラベルやタグは
`components/.meta/<id>.json` に出す。中に置くと、ラベルを貼る行為がそのコンポーネントの
ハッシュを変えてしまう（コードを 1 行も変えていないのに別の版になる）。§3.1 で `.frozen/` を
外に出したのと同じ理由である。

**コンポーネント id はディレクトリ名である。** 別途 `id` フィールドを真実とはしない
（二重の真実を作らない）。ディレクトリの改名はコンポーネントの改名であり、過去 run は
自分でコードを抱えているので影響を受けない（§7.3）。

規則は 2 つだけ。

| | |
| --- | --- |
| 1 | **ディレクトリ内で自己完結すること。** 外への import はサードパーティのパッケージ（自分の pip パッケージを含む）のみ |
| 2 | **入口は `main.py` の `@blab.entry` でちょうど 1 つ** |

v1 の「1 ファイル」制約は、trainer をコンポーネントにする以上もたない。trainer は一番
コードが長くなり、一番ヘルパを欲しがる。ディレクトリ単位にすれば、v1 が未決事項として
挙げていた「共有ヘルパを持てないことの窮屈さ」が、**コンポーネント内部については**解消する。

コンポーネント**間**で共有したいコードは、引き続き自分の pip パッケージ（`pip install -e`）へ
出す。安定して育つフレームワーク的なコードは blab に入れるものではない。

### 4.2 entry

```python
# components/resnet18/main.py
import torch.nn as nn
import blab

from .blocks import BasicBlock        # ディレクトリ内なので相対 import が使える


@blab.entry
class Resnet18(nn.Module):
    def __init__(self, k: int, pretrained: bool = False):
        super().__init__()
        self.layers = nn.Sequential(*[BasicBlock(64) for _ in range(8)])
        self.head = nn.Linear(512, k)

    def forward(self, x):
        return self.head(self.layers(x))
```

- **`main.py` がコンポーネントの入口モジュール。** `__init__.py` は要らない（blab が
  パッケージとして合成するので、相対 import は `__init__.py` 無しで解決する）
- `@blab.entry` は `main.py` の名前空間から届く範囲にちょうど 1 つ。補助クラスを
  同じファイルに置いてよい
- entry はクラスでもファクトリ関数でもよい。blab は**引数で呼んで戻り値を使う**

### 4.3 ハッシュと自動凍結

**コンポーネントのハッシュ**は、ディレクトリ全体から決まる。

```
sha256( ソートした (相対パス, ファイルの生バイト) の列 )
除外: ドットで始まるエントリ、__pycache__、*.pyc
```

正規化はしない（空白の違いも別内容）。ドット始まりを除外するのは `.ipynb_checkpoints` の
たぐいを巻き込まないためで、`.meta/` と `.frozen/` はそもそもコンポーネント本体の外にある
（§4.1）ので対象にならない。

**ハッシュ対象に入るのは、そのディレクトリの中身の全部である。** README も入る。したがって
README の誤字を直すと新しいハッシュの版が 1 つ増える。これは意図的な割り切りで、理由は
「凍結されたディレクトリの中身とハッシュが 1:1 である」ことを崩さないためである
（除外したファイルがあると、同じハッシュで中身が違う凍結版が作れてしまう）。増えた版は
ラベルが無いので `.blab/` に留まり、git は汚れない。UI 側で「差分は README だけ」と
表示すれば実務上は困らない。

**凍結は自動で起きる。** `blab run` が YAML の参照を解決するとき、各コンポーネントの
作業コピーをハッシュし、そのハッシュがまだ凍結されていなければ `.blab/frozen/<id>/<hash>/`
へコピーする。人間は何もしない。

```bash
$EDITOR components/resnet18/main.py     # 普通に編集する
blab run experiments/baseline.yaml      # 普通に実行する
#   → resnet18 のハッシュが 3f9a1c… と判明
#   → 未凍結なので .blab/frozen/resnet18/3f9a1c…/ へ凍結
#   → run には resnet18@sha256:3f9a1c… と記録される
```

これによって、

- **全ての run が例外なく再現可能になる。** 「登録し忘れたから追えない」run が存在しなくなる
- **v1 の `BLAB_DEV` が丸ごと不要になる。** 逃げ道が要らないのは、道が塞がっていないから
- **「編集したつもりで古いコードが動く」事故が消える。** 常に今の中身が動き、常にその
  中身が記録される。v1 の整合性ゲート（`current/` と登録版のハッシュ比較）も不要になる

import 元は必ず凍結済みディレクトリであって作業コピーではない。したがって「記録された
ハッシュと実際に動いたバイト列が違う」は原理的に起こらない。

### 4.4 ラベル（tag）

ハッシュは正しいが、人間の記憶とは接続しない。「v3 で BN を外した」という記憶に繋ぐため、
**後からラベルを貼る。**

```bash
blab tag resnet18 v3 --note "BN を外した"
#   → 今の作業コピーのハッシュに "v3" というラベルを貼る
#   → その版が .blab/frozen/ から components/.frozen/resnet18/v3/ へ昇格し、git に載る
```

過去のハッシュに後から貼ることもできる。キャッシュを消していても、その版を使った run が
残っていれば **run の中に実体が焼き込まれている**（§7.3）ので復元できる。

```bash
blab tag resnet18 v3 --from-run runs/cifar100/20260913-063012_a1b2_distill/
```

実際にはこれが一番よくある使い方になるはずである。「あの実験のときのモデル、良かったから
名前を付けておこう」が後からできる。

**ラベルは不変。** 一度貼ったラベルを別のハッシュに付け替えることはできない（過去の
YAML が指す先が変わってしまう）。間違えたら別の名前を使う。

```jsonc
// components/.meta/resnet18.json — git 管理下。コンポーネント本体の外にある（§4.1）
{
  "schema_version": 2,
  "id": "resnet18",                         // ディレクトリ名との一致を検査するためだけに持つ
  "tags": ["model", "classifier"],
  "labels": [
    {"name": "v1", "hash": "sha256:3f9a1c…", "note": "初版",        "tagged_at": "2026-09-01T10:00:00+09:00"},
    {"name": "v3", "hash": "sha256:8b2d40…", "note": "BN を外した", "tagged_at": "2026-09-08T14:20:00+09:00"}
  ]
}
```

配列の順序が登録順であり、それが唯一の順序の定義である（ラベルは自由文字列なので
大小比較ができない）。`latest` は配列の末尾を意味する予約語。

**タグ（`tags`）は分類ではなくフィルタ用の自由文字列。** v1 と同じく kind は持たない。
`tokenizer` は model か dataset か、といった困る分類は運用されないためである。

`.meta/<id>.json` が無くてもよい。**無ければ「ラベルもタグも無い」とみなす。** 手で作った
コンポーネントのディレクトリがそのまま動くほうが、ファイルシステムを真実とする方針に合う。
`blab tag` が初めて必要になった時点で blab が作る。

entry の名前（`@blab.entry` が付いていたクラス名）はここに書かない。**コードから導出できる
ので、キャッシュである。** `.blab/index/` に置き、消えても import し直せば分かる。

### 4.5 import の仕組み

凍結ディレクトリを、合成したパッケージ名で `sys.modules` に載せる。

```
blab._c.<id>__<hash の先頭 8 桁>
```

`importlib.util.spec_from_file_location` に `submodule_search_locations` を与えてパッケージ
として登録するので、`__init__.py` が無くても `from .blocks import BasicBlock` が解決する。

同一ハッシュの再ロードは `sys.modules` のキャッシュを使う。**ハッシュが名前に入っているので、
同じ id の別バージョンを 1 プロセス内で同時にロードしても衝突しない**（v1 では
レジストリをまたぐと一意でない問題があったが、内容ハッシュにしたことで解消している）。

**未決:** 合成モジュール名は子プロセスから解決できないので、`spawn` で起動した
`DataLoader(num_workers>0)` が unpickle するときに問題が出る。Linux 既定の `fork` では
`sys.modules` を継承するので出ない。必要になったら `sys.meta_path` にこの名前を解決する
finder を入れて塞ぐ。

### 4.6 継承について

v1 は「他のコンポーネントのクラスを継承しない」という規則を明文で持っていた。**v2 では
規則が要らない。構造上できないからである。**

YAML で参照した子コンポーネントは Builder として渡り（§6）、`.build()` で得られるのは
**インスタンスだけ**である。クラスオブジェクトを手に入れる経路がないので、継承しようがない。

規則が消えても目的は達成されている。**継承は依存を記録から隠し、合成は依存を記録に出す。**
v2 では合成しか書けないので、依存は必ず YAML と `resolved.yaml` に現れる。

## 5. 実験 YAML

### 5.1 書き方 【要確認】

```yaml
# experiments/distill.yaml
experiment: cifar100
name: distill

run:
  use: distill_trainer          # ← "use" があればコンポーネント参照
  epochs: 100                   # ← 残りは全部その引数
  lr: 0.0003
  seed: 0

  dataset:                      # ← 引数の位置に、そのまま子を書く
    use: cifar100
    augment: randaug

  student:
    use: resnet18
    pretrained: false

  teacher:
    use: resnet50
    pretrained: true

  metric: {use: top1_accuracy}  # ← 引数が無くても {use: ...} で書く

  transforms:                   # ← リストも書ける
    - {use: random_flip, p: 0.5}
    - {use: randaug, n: 2}
```

規則は 1 つ。**`use` キーを持つマッピングはコンポーネント参照、それ以外は普通の値。**
引数と子を `args:` / `children:` に分けるより、この形のほうが浅く読める。

**文字列単体の省略形（`metric: top1_accuracy`）は認めない。** 認めると
`optimizer: adam` のような普通の文字列引数と区別が付かず、「その id のコンポーネントが
存在すれば参照」という規則になる。するとコンポーネントを新規作成した瞬間に既存 YAML の
意味が黙って変わる。§0 の原則に真っ向から反するので、冗長でも常に `{use: ...}` と書く。

**参照は引数の値の任意の深さに書ける。** リスト（augmentation のパイプライン）でも、
マッピングの値（複数の metric）でもよい。`resolved.yaml` での位置は
`transforms[0]` / `metrics.acc` のようなパスで表す。

**予約キーは `use` と `data`（§8）の 2 つだけ。** これらのキーを持つ普通のマッピングを
引数として渡したい場合は `{$raw: {...}}` で包む。blab は `$` で始まるキーを予約する。
なお、`$raw` で包まれていないマッピングが `use` / `data` を含む場合は、参照として解釈した
うえで**警告を出す**（黙って解釈しない）。

**YAML に制御構文は入れない。** 参照（`${...}`）、四則演算、条件分岐、ループは持たない。
この種の設定ファイルは例外なく機能が増えて第二のプログラミング言語になるので、最初に
線を引く。YAML が表現してよいのは**コンポーネントの木**だけである。分岐やループが
欲しくなったら、それはコンポーネントの Python に書く。

「実行時にしか決まらない引数」（dataset のクラス数を model に渡す、など）を YAML に
書かなくて済むのが §6 の Builder である。参照構文を持たずに済んでいるのはそのおかげ。

### 5.2 参照の 3 形式 【要確認】

```yaml
use: resnet18                       # 今の作業コピー（実行時に自動凍結される）
use: resnet18@v3                    # ラベルで固定
use: resnet18@sha256:3f9a1c         # ハッシュで完全固定
use: seg-baseline/unet@v2           # 別プロジェクトのコンポーネント
```

別プロジェクトは `<プロジェクト名>/<id>` で書く。プロジェクト名は `blab.json` の `project`
で、グローバル索引（§3.2）から実パスに解決する。索引に無ければ事前検証でエラーにし、
「`blab link <path>` で登録してください」と出す。

`resolved.yaml`（§5.4）にはプロジェクト名ではなく `project_uid` とハッシュが書かれるので、
プロジェクトを改名しても過去の run は壊れない。

### 5.3 事前検証（preflight）

**`blab run` は、学習を 1 秒でも始める前に、YAML を上から下まで歩いて検査する。**
ここで見つかったものは全部エラーで止める。

検査には import が要り、import 元は凍結版でなければならない（§4.3）ので、**凍結が検査に
先立つ**。実際の順序は次のとおり。

```
YAML 読み込み → 参照解決 → ハッシュ → 凍結 → 凍結版から import → 検査 → 実行
```

凍結が検査より前に来ることで、構文エラーを含むコンポーネントも凍結される。これは無害で
ある。凍結は内容アドレスなので中身と名前が食い違うことはなく、`.blab/` に置かれるだけで
git には載らない（ラベルを貼らない限り）。

1. 参照されたコンポーネントが全部存在するか（別プロジェクト参照を含む）
2. 各コンポーネントを凍結版から import できるか（**構文エラー・import エラーがここで出る**）
3. `main.py` に `@blab.entry` がちょうど 1 つあるか
4. root のコンポーネントが `execute` を持つか
5. YAML に書かれた引数名が、entry の実際の引数と合っているか（タイポ検出）
6. YAML にもデフォルト値にも無い必須引数はどれか → 「実行時に渡されるはず」として記録し、
   渡されなければ `.build()` の瞬間にエラー
7. 参照された外部ファイル（§8）の論理名が解決でき、実パスが存在するか
8. `blab.json` の `require_tags` を満たすか（木の中に、要求されたタグを持つコンポーネントが
   1 つ以上あるか）

**v1 で解けなかった問題が、ここで解ける。** v1 §3.5 は「ポリシー検査で例外を投げない」と
決めていたが、その理由は「止められる時点が原理的に存在しない」ことだった。`blab.load()` の
たびに binding が増えるので、どの瞬間にも「model が無い」と「model はまだ load されて
いないだけ」の区別が付かない。

v2 では**構成が実行前に全部分かっている**ので、この曖昧さが消える。したがって事前検証は
遠慮なく止めてよい。8 時間学習してから知らされる、という事態は構造的に起きなくなる。

### 5.4 resolved.yaml

**あなたが書く YAML と、blab が記録する YAML を分ける。**

| | |
| --- | --- |
| `experiments/distill.yaml` | `use: resnet18` のようにゆるく書ける。書きやすさ優先。git 管理下 |
| `<run>/resolved.yaml` | 全部ハッシュに解決済み。実行時に渡された引数も埋まっている |

パッケージ管理の `package.json` と `package-lock.json` の関係と同じである。

```yaml
# runs/cifar100/20260913-063012_a1b2_distill/resolved.yaml
schema_version: 2
experiment: cifar100
name: distill
project_uid: 01JQ8R7X4K9M2N

run:
  use: distill_trainer
  hash: sha256:c41e9a…
  args: {epochs: 100, lr: 0.0003, seed: 0}
  children:
    dataset:
      use: cifar100
      hash: sha256:77b2f0…
      builds:                                   # 実際に build された回数ぶん並ぶ
        - args: {augment: randaug, split: train}
          args_from: {augment: yaml, split: runtime}
        - args: {augment: randaug, split: val}
          args_from: {augment: yaml, split: runtime}
    student:
      use: resnet18
      hash: sha256:3f9a1c…
      builds:
        - args: {pretrained: false, k: 100}
          args_from: {pretrained: yaml, k: runtime}
```

`resolved.yaml` は**記録であると同時に、実行可能な入力**である（§9）。

## 6. Builder — 実行時にしか決まらない引数

### 6.1 問題

model を作るには `k`（クラス数）が要るが、その値は dataset を実際に作るまで分からない。
backbone の出力次元 → head の入力次元、tokenizer の語彙数 → embedding のサイズ、など
同じ形の依存は ML のあちこちに出る。

素朴な解は 2 つあり、どちらも採らない。

**YAML に手で書く**（`k: 100`）。dataset を差し替えたとき直し忘れる。しかも運が悪いと
落ちずに間違ったまま学習が走る（クラス数が多いぶんには行列の形が合ってしまう）。
**「YAML にそう書いてある」と「実際にそうである」が黙ってズレる** — blab が一番嫌う事故。

**YAML に参照構文を入れる**（`k: ${dataset.n_classes}`）。評価順序の概念が生まれ、YAML から
Python オブジェクトを覗く構文が必要になり、やがて四則演算と条件分岐が欲しくなる。
§5.1 で引いた線を自分で越えることになる。そのうえ「1 つの宣言から 5 fold ぶんの dataset を
作る」のような、宣言と実体が 1:1 でないケースは解けない。

### 6.2 仕組み

**blab は子コンポーネントを実体化しない。作り方（Builder）だけを渡す。**

```python
# components/standard_trainer/main.py
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
```

`.build(**kwargs)` を呼んだ瞬間に、blab が **YAML に書かれた引数と、コードから渡された
引数を合体させて**実体化する。衝突した場合はコード側が勝つ（YAML は既定値の位置づけ）。

メソッド名を `__call__` ではなく `.build()` にしたのは、PyTorch で `model(x)` が順伝播を
意味するためである。`self.model(k=10)` は順伝播に見える。

**ルールは 1 本に統一する。YAML の子は常に Builder。** 実行時引数が要らない場合も
`self.metric.build()` と書く。「Builder のときと実体のときがある」という例外を作るほうが
遥かに悪い。

### 6.3 何が記録されるか

blab が記録するのは、**`.build()` が実際に呼ばれたときの合体後の引数**である。

```jsonc
{"args": {"pretrained": false, "k": 100},
 "args_from": {"pretrained": "yaml", "k": "runtime"}}
```

`k: 100` は誰も宣言していない。**実際にそう渡されたから、そう記録されている。**

これは v1 の「宣言させず、実際に起きたことから観測する」という原則が、宣言的な設計の
内側でそのまま生き残るということである。役割分担はこうなる。

> **YAML は「構成の形」を宣言する。「最終的な値」は観測する。**

引数の記録は v1 §3.6 を引き継ぎ、3 通りに分ける。

| 引数の種類 | 記録 |
| --- | --- |
| JSON 化できる値 | そのまま（`{"k": 100}`） |
| `.build()` が返したオブジェクト | **同一性から検出**して参照（`{"$ref": "dataset#0"}`） |
| それ以外の live object | `{"$unrecorded": "Tensor"}`。推測しない |

同一性の検出は weakref で行う（`id()` の再利用による誤参照を避ける）。weakref を張れない
オブジェクトだけ強参照で抱える。

v1 にあった `loaded_by`（どの binding の実体化中に起きた load か）は**不要になった。**
親子関係は YAML に明示されているので、観測して推定する必要がない。

同一 Builder から同じ引数で複数回 build した場合は 1 件に畳む。引数が違えば別の
`builds` エントリとして残す（CV の fold ごとの dataset など）。

## 7. 実行と記録

### 7.1 execute 【要確認】

YAML の root（`run:` の直下）に置かれたコンポーネントだけが特別扱いされる。blab はそれを
実体化し、**`execute(run)` を呼ぶ。** 子コンポーネントは実体化されるだけで、`execute` は
呼ばれない。

メソッド名を `run` にしないのは、blab が「Run」を「1 回の実験の記録」という名詞として
既に使っているためである（ディレクトリ構造、UI、CLI の全体）。衝突すると説明が常に混乱する。

`execute` を持たないコンポーネントを root に置いた場合は、事前検証でエラーにする。

### 7.2 記録 API

`execute` が受け取る `run` が記録の入口。v1 の `Run` をほぼそのまま引き継ぐ。

```python
run.log({"train/loss": 0.31}, epoch=3)       # 時系列 → metrics.jsonl
run.log_summary({"test/acc": 0.87})          # 1 run に 1 つの値 → summary.json
run.log_artifact("outputs/cm.png")           # ファイル → artifacts/
run.path                                     # run ディレクトリ
```

**子 run を作る API は持たない。** v1 の `Group.run()` に相当するものは v2 には無く、
group は §7.4 のとおり YAML で名乗るだけである。1 プロセスが複数 run を所有する経路を
作らないことで、「1 run ディレクトリに書くのは 1 プロセスだけ」という前提が保たれる。

**記録は決して学習を落とさない。** 記録系の失敗は警告に落とす（`BLAB_STRICT=1` で送出）。
唯一の例外は事前検証で、そちらは実行前に止まるので学習時間を失わない。

`params.json` は**廃止する。** ハイパラはコンポーネントの引数であり、`resolved.yaml` に
全部入っている。UI の run テーブルの列はそこから構成する。v1 で `params` と `components` が
二重に持っていた情報が 1 本になる。

### 7.3 run ディレクトリ

**run はリポジトリの外に出るので、run 単体で完結していなければならない。** リポジトリが
手元に無い run、リポジトリが変わったあとの run、共同研究者から `rsync` でもらった run —
どれも、run 単体でコードが読めなければ意味がない。

```
runs/cifar100/20260913-063012_a1b2_distill/
├── meta.json            # kind / status / 実行時刻 / 実行時間 / project_uid / heartbeat
├── resolved.yaml        # §5.4。全部ハッシュに解決済みの構成
├── env.json             # §7.5
├── components/          # ★ この run で実際に使ったコンポーネントの実体
│   ├── distill_trainer/
│   ├── resnet18/
│   └── cifar100/
├── metrics.jsonl
├── summary.json
├── artifacts/
└── logs/
    ├── stdout.log       # blab が実行を所有しているので自動で取れる
    └── stderr.log
```

`components/` に入るのは実際に使った数個のディレクトリだけで、1 つ数 KB〜数十 KB。
コストは無視できる。得られるのは「**この run のディレクトリさえあれば、コードは完全に
復元できる**」という性質である。レジストリが吹き飛んでも、プロジェクトを消しても、
過去の実験は読めるし再実行できる。

**stdout / stderr の自動保存は、実行を所有したことで得られた新しい能力である。**
v1 の観測モードでは、blab はプロセスの外側を知らなかった。同様に、正確な終了ステータス、
正確な実行時間、シグナルによる中断の検出も v2 で初めて正しく取れる。

ディレクトリ名は ALab から引き継ぐ：`YYYYMMDD-HHMMSS_<短 ID>_<name>`。

### 7.4 Group と CV

Experiment / Group / Run はすべてディレクトリで、種別は `meta.json` の `kind` で判別する。
階層構造とディレクトリ構造が 1:1 で対応する。ここは ALab / v1 から変えない。

**グループは実行が作るのではなく、宣言が作る。** run が自分の所属先を名乗るだけでよい。

```yaml
experiment: cifar100
group: cv5-lr3e4          # ← この名前を名乗った run が同じ group に集まる
name: fold0
```

`blab run` は、同じ experiment に同名の group ディレクトリが無ければ作り、あればその下に
run を置く。**group を所有するプロセスは存在しない。**

CV は**ただのループ**になる。専用のコンポーネントは要らない。

```bash
for f in 0 1 2 3 4; do
  blab run experiments/cv.yaml --group cv5-lr3e4 --set run.fold=$f --name fold$f
done
```

この形の利点は、**fold が独立プロセスになる**ことである。

- 複数 GPU / 複数ノードに素直に撒ける（`for` を `sbatch` に変えるだけ）
- 3 fold 目だけ落ちたら、それだけ回し直せる
- trainer コンポーネントが CV を一切知らなくてよい

**集計は所有者を持たず、読むときに導出する。** group の集計値を誰かが書くのではなく、
配下 run の `summary.json` から mean ± std をその場で計算する（UI と `blab ls` の両方で）。

これによって「group がいつ完成するのか」という問いが消える。fold を 3 つ回した時点では
3 つぶんの集計が見え、5 つ揃えば 5 つぶんになる。あとから 1 fold 足しても整合するし、
1 つ消しても整合する。v1 が `blab reindex` で解いていた問題が、導出にしたことで発生しなくなる。

**`--set` は CLI からの上書きである。** fold 番号のように 1 か所だけ変えて回すために要る。
`run.fold=2` のようにドット区切りで YAML 内の位置を指す。上書き後の値は `resolved.yaml` に
残るので、記録の正しさは損なわれない。YAML は唯一の真実ではなくなるが、
**`resolved.yaml` は唯一の真実であり続ける。**

**group を間違えたら、あとから移せる。**

```bash
blab mv runs/cifar100/20260913-063012_a1b2_fold0/ --group cv5-lr3e4
```

宣言方式は「`--group` を付け忘れた」「名前を打ち間違えた」という事故が起きやすいので、
救済手段を最初から用意する。UI からも同じ操作ができる（§11）。移動は run の `meta.json` に
`moved_from` として残す。**黙って履歴を書き換えないため**である。

fold 平均では作れない値（OOF accuracy、検定の p 値など）を group 自身に持たせる方法は
未決とする（§14）。

### 7.5 環境情報

**記録するだけ。検証も強制もしない。** 揃えられるかはユーザの責任、というのが §0 の約束。

```jsonc
// env.json
{
  "python": "3.12.4",
  "platform": "Linux-7.0.0-30-generic-x86_64",
  "hostname": "gpu03",
  "packages_hash": "sha256:…",         // uv.lock があればその中身のハッシュ
  "packages": {"torch": "2.4.0", "numpy": "2.1.0", …},   // uv.lock / pip freeze 相当
  "git": {"commit": "2227cb0…", "dirty": true},
  "cuda": {"torch_cuda": "12.4", "cudnn": "9.1.0", "gpu": "NVIDIA A100-SXM4-80GB"},
  "argv": ["blab", "run", "experiments/distill.yaml"]
}
```

取得に失敗した項目は `null` にせず、**理由を残す**（`{"cuda": {"$unavailable": "torch が import できない"}}`）。
§0 の原則の適用である。

乱数シードは blab が管理しない。`seed` は trainer コンポーネントの引数であり、それを
どう使うか（`torch.manual_seed` だけか、`cudnn.deterministic` まで倒すか）はプログラムの
責任である。blab は `resolved.yaml` に値が残ることだけを保証する。

### 7.6 状態

| status | 意味 |
| --- | --- |
| `running` | 実行中。heartbeat を打つ |
| `finished` | 正常終了 |
| `failed` | 例外で終了。トレースバックを `meta.json` に残す |
| `killed` | シグナルで中断（`SIGTERM` / `SIGINT`） |

heartbeat が 60 秒以上途絶えた `running` は、UI で `stale` と表示する。

## 8. 外部ファイル（データ）

学習データ本体、正解ラベル、CV の fold split を書いた CSV — これらも再現性の一部である。

**新しい概念は足さない。データの所在を宣言するのも、コンポーネントである。**

```yaml
dataset:
  use: imagenet_dir
  path: {data: IMAGENET_ROOT}          # ← 論理名。実パスはマシンごとに解決
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

`{data: NAME}` は blab が解決し、コンポーネントには実パス（`pathlib.Path`）が渡る。
YAML 自体にはマシン依存の情報が入らないので、そのまま持ち運べる。

**記録するのは同一性であって実体ではない。**

| | |
| --- | --- |
| 小さいファイル（既定 1 MB 未満） | **run にコピーする。** fold split や label CSV は一番失われやすく、一番復元したい。安い |
| 大きいファイル / ディレクトリ | パス・サイズ・更新時刻・ハッシュだけを記録 |
| 巨大なディレクトリ | 毎回全バイトを読まない。ファイル一覧（相対パス・サイズ・更新時刻）からハッシュを作り、`.blab/` にキャッシュする。厳密な全バイトハッシュは明示的に要求されたときだけ |

事前検証（§5.3）で、参照された論理名が `blab.local.json` に無い場合と、実パスが存在しない
場合はエラーにする。**学習を始める前に「そのデータ、このマシンには無い」と分かる。**

`{data: ...}` で宣言せずに、コンポーネントの中でパスを直書きすることは当然できてしまう。
それは記録に残らないので、blab は何も言えない。ここは規律の問題として扱う（`blab.json` の
ポリシーで「`{data:}` 以外のファイルアクセスを警告する」ような検査は、当面持たない）。

## 9. 再実行

```bash
blab run runs/cifar100/20260913-063012_a1b2_distill/
```

run ディレクトリを渡すと、その中の `resolved.yaml` と `components/` **だけ**を見て実行する。
レジストリもプロジェクトも参照しない。したがって、

- コンポーネントを編集したあとでも、過去 run はその当時のコードで再実行される
- プロジェクトを消したあとでも、run のディレクトリさえあれば再実行できる
- 共同研究者から run をもらえば、そのまま再実行できる

これが §0 で約束した「コードの再現」の達成形である。環境の再現は別問題で、`env.json` を
見て自分で揃えるか、`uv` を使っているなら `packages_hash` の一致を確認する。

**実行時引数は再現されない。** `resolved.yaml` には `.build()` に渡された値が記録されているが、
再実行時はコードが再びそれを計算する。記録と食い違ったら警告を出す（データが変わった、
ライブラリの挙動が変わった、などの検出になる）。**記録を再生するのではなく、コードを
動かして一致を確かめる**という立場を取る。

## 10. CLI

| コマンド | 内容 |
| --- | --- |
| `blab init` | `blab.json` を作り、`.gitignore` を書き、グローバル索引に登録する |
| `blab link <path>` | 別のプロジェクトをグローバル索引に登録する |
| `blab run <yaml>` | 事前検証 → 凍結 → 実行 |
| `blab mv <run のパス> --group <name>` | run の所属 group を変える（§7.4） |
| `blab run <run のパス>` | 過去 run の再実行（§9） |
| `blab check <yaml>` | 事前検証だけして実行しない |
| `blab new <id>` | コンポーネントの雛形を作る（`main.py` / `README.ja.md`） |
| `blab tag <id> <label> [--note] [--from-run]` | ラベルを貼り、git 管理下へ昇格させる |
| `blab components [--tags model]` | コンポーネント一覧 |
| `blab show <id>[@<label>]` | 説明・ソース・ラベル履歴 |
| `blab diff <id>@v1 <id>@v3` | 版間の diff |
| `blab ls [experiment]` | run / group の一覧 |
| `blab show <run のパス>` | 単一 run の meta / 構成 / summary |
| `blab rm <run のパス>` | run / group を削除（確認あり） |
| `blab ui [--port 8420] [--open]` | ローカル UI |

`blab run` の主なオプション：`--name`（run 名）、`--group`（所属 group、§7.4）、
`--set <path>=<value>`（YAML の 1 か所を上書き。`--set run.lr=1e-4`）、
`--runs-dir`（ログの置き場所、§3.3）、`--dry-run`（`blab check` と同義）。

**`blab register` は無い**（自動凍結に置き換わった）。**`blab reindex` も無い**（`.blab/` が
ただのキャッシュなので、消せば再構築される）。

## 11. 閲覧層（UI）

原則として読み取り専用。記録層と閲覧層が互いを知らないという性質は引き継ぐ。ディレクトリを
走査するだけなので、手で規約どおりのディレクトリを作っても閲覧できる。

**唯一の例外が run の group 付け替え**（§7.4）である。group を宣言で決める以上、付け忘れと
打ち間違いは必ず起きるので、救済手段を UI に置く。ただし UI がディレクトリを直接触るのでは
なく、`blab mv` と同じ操作を呼ぶ形にして、実装を 1 か所に保つ。移動は `moved_from` として
記録に残す。

1. **Experiment 一覧** — run 数 / 実行中の数 / 最終更新
2. **Run テーブル** — 列は `resolved.yaml` の**構成**と `summary.*` から動的構成。
   「どのハッシュの resnet18 を使った run か」でソート・フィルタできる。group は折りたたみ行
3. **Run 詳細** — meta / **構成ツリー** / metrics チャート / アーティファクト / ログ / 環境
   - 構成ツリーが blab の要。`resolved.yaml` をそのまま木として描き、各ノードをクリックすると
     コンポーネントの詳細へ飛ぶ。引数は YAML 由来と実行時由来を色で区別する
   - run に焼き込まれたソース（`components/`）をその場で表示できる
4. **Component 一覧** — id / タグ / ラベル / 使用 run 数 / 最終使用日
5. **Component 詳細** — README、ソース（ラベル・ハッシュ選択可）、ラベル履歴と任意 2 版の
   diff、そして **逆引き: このコンポーネントを使った全 run**（版別に分け、metric の分布付き）
6. **Group 詳細** — mean ± std の集計表（読むときに導出）、fold 重ね描き、
   **run の所属 group の付け替え**（`blab mv` と同じ操作。UI が行う唯一の書き込み）
7. **比較ビュー** — **構成ツリーの diff + 版が違うコンポーネントのソース diff** /
   metrics 重ね描き / summary 表 / アーティファクト並置。run と group を混ぜて比較できる

比較が「数値の diff」ではなく「**構成の diff + 変わった部分のコード diff**」になる。
研究者の頭の中は常に「baseline と同じ、ただし loss だけ新しいやつ」という形をしているので、
UI がその形で答える。v2 では `resolved.yaml` という構造化された木があるので、この diff は
v1 より正確かつ簡単に作れる。

学習中の run はライブ更新する（`running` があるときのみ 3 秒間隔でポーリング）。

## 12. 実装フェーズ

v1 のコードは相当量が再利用できる。`io.py` / `layout.py` / `aggregate.py` / `errors.py` /
`prefs.py` / `index.py` / `server/` / `static/` はほぼそのまま、`registry.py` と `run.py` は
考え方を引き継いで書き直し、`snapshot.py` は役割が変わる（entrypoint 追跡 → コンポーネントの
焼き込み）、`entry.py` は残る。

| Phase | 内容 |
| --- | --- |
| 1 | プロジェクト（`blab.json` / `blab.local.json` / グローバル索引）と `blab init` / `blab link` |
| 2 | コンポーネント: ディレクトリ単位のハッシュ・自動凍結・合成パッケージとしての import |
| 3 | YAML パーサと事前検証（`blab check`）。ここまでで「実行せずに構成を検査する」が動く |
| 4 | Builder と `.build()` の引数合体・観測・`resolved.yaml` の生成 |
| 5 | `blab run`: 実行・記録 API・run ディレクトリ・コンポーネントの焼き込み・stdout 捕捉 |
| 6 | `env.json` と外部ファイル（`{data: ...}`）の解決・記録 |
| 7 | Group / CV（`group:` の宣言・group ディレクトリの競合処理・読むときの集計導出） |
| 8 | `blab tag` とラベルの昇格。`blab show` / `diff` / `ls` |
| 9 | UI を `resolved.yaml` ベースに移植。構成ツリーと比較ビュー |
| 10 | 再実行（`blab run <run>`）と、実行時引数の食い違い検出 |

Phase 3 が終わった時点で、**実行なしで構成を検査できる**という v2 最大の実務的価値が
先に手に入る。ここを早く置くのが良い。

## 13. 非目標（当面やらないこと）

- **実行環境の再現。** 記録はするが、揃えるのはユーザの責任（§0）
- **数値レベルの再現保証。** CUDA の非決定性・乱数の扱いはプログラム側の問題
- **観測モード（普通の Python から `blab.init()`）。** v2 では入口は `blab run` だけ。
  段階的導入のしやすさは失うが、2 つの実行モデルを同時に正しく保つコストのほうが高い。
  必要になれば後から足せる（記録層は共通なので）
- **ハイパラ探索 / スイープ。** YAML に制御構文を入れないと決めた以上、複数 YAML を
  外で生成する形になる。`resolved.yaml` があるので後から足しやすい
- **YAML の合成・継承**（Hydra の `defaults:` のようなもの）。第二のプログラミング言語に
  なる道なので、当面持たない
- **コンポーネント間の共有ヘルパ。** 自分の pip パッケージへ
- リモートサーバ / マルチユーザ / 認証。ローカル単一ユーザ前提
- DB の導入。ファイルシステムを唯一の真実とする
- UI からの書き込み（§11 の group 付け替えを唯一の例外として、読み取り専用に留める）
- 分散学習での複数プロセス同時書き込み（rank 0 のみが書く前提）

## 14. 未決事項

- **エディタ支援。** trainer が受け取る `dataset` は `blab.Builder` であって dataset ではない
  ので、`self.dataset.build(...)` の戻り値に補完が効かない。コンポーネント**内部**は普通の
  Python なので影響はない。`Builder[Cifar100]` のようなジェネリクスで改善できるが、
  YAML から来る型を静的に知る手段が無いので限界がある。日々どれくらい痛いかは実運用で判断する
- **Builder であることの分かりにくさ。** `__init__` の引数が実体ではなく工場だという点は
  慣れが要る。命名規約（`self.dataset` ではなく `self.dataset_builder`）を推奨すべきか、
  型注釈で足りるか
- **`use` キーの衝突。** `use` という名前の引数を持つコンポーネントの明示形（§5.1）で足りるか
- **`--set` で `use` の差し替えを許すか。** v2 初版では**値の上書きだけを許し、
  `--set run.student.use=resnet50` のようなコンポーネントの差し替えは禁止する**
  （§3.4 の「実験の定義が git に残る」を守るため）。制限は後から緩められるが、逆はできない
  ので、きつい側から始める。実運用で窮屈なら緩める
- **group 自身の値。** fold 平均では作れない OOF accuracy や検定の p 値を、誰がどこに
  書くか（§7.4）。group を所有するプロセスが無いので、書き手がいない。候補は
  「group を読んで書く専用の run を後から回す」「`blab group set` のような CLI を持つ」
- **`.blab/frozen/` の肥大化。** ラベルの無い版が溜まり続ける。1 つ数 KB なので容量は
  問題にならないはずだが、実測する。`rm -rf .blab/` が常に安全であることは保つ
- **子プロセスからの unpickle**（§4.5）。`spawn` で起動した DataLoader
- **巨大な `metrics.jsonl`（>10^6 行）の初回読み込みコスト。** 実測して決める（ALab から継続）
- **コンポーネントの削除・リネーム。** 過去 run が参照しているコンポーネントを消せては
  いけない。ただし v2 では run が実体を抱えているので、v1 より安全ではある
- **外部ファイルの規律。** `{data: ...}` を経由しないファイルアクセスを検出できない（§8）

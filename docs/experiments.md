# 実験 YAML と実行

## 書式

```yaml
# experiments/distill.yaml
experiment: cifar100        # 必須。保存先の下のディレクトリ名になる
group: cv5                  # 任意。同じ group 名の run が 1 つのディレクトリにまとまる
name: distill               # 任意。run ディレクトリ名の末尾になる

run:                        # 必須。root component
  use: distill_trainer      # use を持つマッピングが component 参照
  epochs: 100               # 残りのキーはその component の引数
  lr: 0.0003

  dataset:                  # 引数の位置に子の component を書く
    use: cifar100
    augment: randaug

  student: {use: resnet18, pretrained: false}
  teacher: {use: resnet50@v3}
  metric: {use: top1_accuracy}

  transforms:               # リストの中にも書ける
    - {use: random_flip, p: 0.5}
    - {use: randaug, n: 2}
```

- `use` キーを持つマッピングは component 参照、それ以外のキーはその component の引数になる
- 参照は引数の値のどこにでも書ける（リストの要素、マッピングの値など）
- 引数の無い component も `{use: top1_accuracy}` と書く。`metric: top1_accuracy` は文字列の引数として扱われる
- `use` や `data` というキーを持つ普通のマッピングを引数として渡したい場合は、`{$raw: {...}}` で包む。
  `$` で始まるキーは予約されている
- 変数参照・計算・条件分岐・ループの構文は無い。必要な処理は component の Python で書く

## component の参照

| 書き方 | 意味 |
| --- | --- |
| `resnet18` | 作業コピー。実行時のハッシュが記録される |
| `resnet18@v3` | ラベルで指定 |
| `resnet18@latest` | 最後に付けたラベル |
| `resnet18@sha256:3f9a1c` | ハッシュで指定（先頭の一部でもよい） |
| `seg_baseline/unet@v2` | 別プロジェクトの component |

別プロジェクトは `<project>/<id>` で書く。`<project>` は相手のプロジェクトの `blab.json` の
`project` で、そのプロジェクトがグローバル索引に登録されている必要がある（`blab init` 済み、
または `blab link <path>` で登録）。

## 事前検証

`blab run` と `blab check` は実行前に次を確認し、1 つでも問題があれば実行しない。

1. 参照された component がすべて存在するか
2. 各 component を import できるか（構文エラー・import エラー）
3. `main.py` に `@blab.entry` がちょうど 1 つあるか
4. root の component が `execute` を持つか
5. YAML の引数名が entry の引数と合っているか（近い名前があれば候補を表示する）
6. `{data: NAME}` の論理名が `blab.local.json` にあり、実パスが存在するか
7. `blab.json` の `require_tags` を満たすか

YAML にもデフォルト値にも無い必須引数は、エラーではなく「実行時に `.build()` で渡される想定」として扱われる。

```bash
blab check experiments/distill.yaml          # 構成の木、既定値、実行時に渡される想定の引数を表示
blab check experiments/distill.yaml --json
```

## 実行

```bash
blab run experiments/distill.yaml
```

| オプション | 内容 |
| --- | --- |
| `--name NAME` | run 名。YAML の `name` より優先 |
| `--group GROUP` | 所属 group。YAML の `group` より優先 |
| `--set PATH=VALUE` | YAML の値を 1 か所上書きする。複数回指定できる |
| `--runs-dir DIR` | 保存先 |
| `--no-capture` | stdout / stderr を `logs/` に捕捉しない。デバッガや進捗バーを使うとき |

実行の流れは次のとおり。

1. 事前検証
2. 参照された component のハッシュを計算し、初めての版なら `.blab/frozen/` にコピーする
3. run ディレクトリを作り、root component を実体化して `execute(run)` を呼ぶ
4. 終了後、状態（`finished` / `failed` / `killed`）を記録する。`finished` 以外なら終了コードは 0 以外

### `--set`

```bash
blab run experiments/distill.yaml --set run.lr=1e-4 --set run.dataset.augment=none
```

- パスは `run.` から始まるドット区切り。リストの要素は `run.transforms[0].p` のように指定する
- 値は YAML として解釈される（`true` は真偽値、`1e-4` は数値）
- 変えられるのは値だけ。`--set run.student.use=resnet50` のような component の差し替えはエラーになる
- 上書きした箇所は `resolved.yaml` の `overrides` に記録され、引数の由来は `override` になる

## 保存先

| 優先 | 指定 |
| --- | --- |
| 1 | 環境変数 `BLAB_RUNS` |
| 2 | `--runs-dir` |
| 3 | `blab.local.json` の `runs_dir` |
| 4 | `blab.json` の `runs_dir` |
| 5 | `<project>/runs/` |

run は `<保存先>/<experiment>/[<group>/]<YYYYMMDD-HHMMSS>_<短い ID>_<name>/` に作られる
（日時は UTC）。中身は [保存形式](layout.md#5-run-のファイル) を参照。

## group と CV

YAML の `group` または `--group` で同じ名前を指定した run は、同じ group ディレクトリに入る。
group を作る操作は無く、最初の run が作る。

```bash
for f in 0 1 2 3 4; do
  blab run experiments/cv.yaml --group cv5 --set run.dataset.fold=$f --name fold$f
done
```

- 各 fold は独立したプロセス・独立した run になる。並列に実行してよい
- group の集計（mean ± std）はファイルに保存されず、UI と `blab ls` が表示のたびに、
  配下の `finished` の run の `summary.json` から計算する
- fold が失敗した場合は、その fold だけを回し直せばよい

group を間違えた run は移せる。移動した事実は run の `meta.json` の `moved_from` に残る。

```bash
blab mv runs/cifar100/20260913-063012_a1b2_fold0/ --group cv5
blab mv runs/cifar100/cv5/20260913-063012_a1b2_fold0/      # --group を省略すると experiment 直下に戻す
```

## 外部ファイル

データセットや fold の分割ファイルは、YAML に論理名で書き、実パスは `blab.local.json` に書く。

```yaml
dataset:
  use: imagenet_dir
  root: {data: IMAGENET_ROOT}
  folds: {data: FOLD_SPLIT_CSV}
```

```jsonc
// blab.local.json（git 管理外）
{
  "data": {
    "IMAGENET_ROOT": "/mnt/nvme/datasets/imagenet",
    "FOLD_SPLIT_CSV": "/home/you/work/cifar/splits/fold5.csv"
  }
}
```

component には実パスが `pathlib.Path` で渡される。`resolved.yaml` には論理名（`{"$data": "IMAGENET_ROOT"}`）が、
`data.json` には実パスと同一性の情報が記録される。

| 対象 | 記録 |
| --- | --- |
| 1 MB 未満のファイル | 全バイトのハッシュ。ファイルを run の `data/` にコピーする |
| 64 MB 未満のファイル | 全バイトのハッシュ |
| 64 MB 以上のファイル | 先頭 1 MB とサイズから作ったハッシュ（`partial`） |
| ディレクトリ | 配下のファイルの相対パス・サイズ・更新時刻から作ったハッシュ（`manifest`） |

`partial` と `manifest` は全バイトを比較していないので、中身が変わってもハッシュが同じになる場合がある。

論理名が `blab.local.json` に無い場合や、実パスが存在しない場合は事前検証でエラーになる。
`{data: ...}` を使わずにコードの中で開いたファイルは記録されない。

## 再実行

```bash
blab run runs/cifar100/20260913-063012_a1b2_distill/
```

run ディレクトリの `resolved.yaml` と、run にコピーされた `components/` を使って実行する。

- component はプロジェクトの `components/` ではなく run の中のコピーから読み込まれるので、
  その後に component を編集・削除していても当時のコードで動く
- 新しい run が作られ、`meta.json` の `replay_of` に元の run が記録される。元の run は変更されない
- コマンドはいずれかの blab プロジェクトの中で実行する。新しい run の保存先はそのプロジェクトの
  規則（または `--runs-dir`）で決まる
- `.build()` に渡される実行時の値はコードが再び計算する。記録と違う値になった場合は警告が出る
- `--set` は使えない
- パッケージの版などの実行環境は再現しない。`env.json` を見て揃える

# mlp_classifier

隠れ層 1 枚の MLP。`linear_classifier` と同じ `predict` / `update` を持つので、
YAML の `model:` を差し替えるだけで入れ替わる。

`layers.py` を相対 import している。**component は 1 ディレクトリなので、中で
ヘルパを分けてよい**（v1 の「1 ファイル」制約は無くなった）。

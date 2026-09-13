"""blab — 実験を、再現できるコードの木として記録する。

実験は YAML で宣言し、blab が組み立てて実行する。

    blab run experiments/distill.yaml

component の書き方は `@blab.entry` を 1 つ付けるだけ。

    import blab

    @blab.entry
    class StandardTrainer:
        def __init__(self, dataset, model, epochs):
            # dataset / model は blab.Builder。まだ実体化されていない
            ...

        def execute(self, run):
            train = self.dataset.build(split="train")
            model = self.model.build(k=train.n_classes)
            run.log({"train/loss": 0.31}, epoch=0)

使い方は README.md と docs/、保存形式は docs/layout.md。

実行層は標準ライブラリと PyYAML だけで動く。UI（`blab ui`）は extra 依存。
"""

from __future__ import annotations

from .builder import Builder
from .entry import entry
from .errors import BlabError, BlabUsageError
from .run import Run

__version__ = "0.2.0"

__all__ = [
    "entry",
    "Builder",
    "Run",
    "BlabError",
    "BlabUsageError",
    "__version__",
]

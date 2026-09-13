"""ふつうの学習ループ。**root component** なので `execute(run)` を持つ。"""

import blab

from .loops import evaluate, train_one_epoch


@blab.entry
class StandardTrainer:
    def __init__(self, dataset, model, metric, epochs=20, seed=0, log_every=1):
        # dataset / model / metric は blab.Builder。**まだ実体化されていない**。
        self.dataset, self.model, self.metric = dataset, model, metric
        self.epochs, self.seed, self.log_every = epochs, seed, log_every

    def execute(self, run):
        train = self.dataset.build(split="train")   # ここで初めて実体化する
        val = self.dataset.build(split="val")       # 同じ Builder から 2 個作れる
        model = self.model.build(k=train.n_classes) # ← 実行時にしか分からない値を渡す
        metric = self.metric.build()

        for epoch in range(self.epochs):
            loss = train_one_epoch(model, train)
            if epoch % self.log_every == 0 or epoch == self.epochs - 1:
                run.log(
                    {"train/loss": loss, "val/acc": evaluate(model, val, metric)},
                    epoch=epoch,
                )

        run.log_summary({"val/acc": evaluate(model, val, metric)})

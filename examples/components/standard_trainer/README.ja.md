# standard_trainer

1 エポックずつ回して `val/acc` を記録するだけの学習ループ。**root component** なので
`execute(run)` を持つ。blab はこれを実体化して `execute` を呼ぶ。

| 引数 | 意味 |
| --- | --- |
| `dataset` / `model` / `metric` | **Builder**。`.build()` を呼ぶまで実体化されない |
| `epochs` | エポック数 |
| `seed` | 乱数の種（使い方はこの component の責任。blab は値を記録するだけ） |
| `log_every` | 何エポックごとに記録するか |

## Builder であること

`self.dataset` は dataset **ではなく**、dataset の作り方である。

```python
train = self.dataset.build(split="train")    # YAML の引数 + ここの引数を合体して実体化
model = self.model.build(k=train.n_classes)  # 実行時にしか分からない値を渡せる
```

`k` は YAML のどこにも書かれていない。**実際にそう渡されたから、そう記録される**
（design.md §6.3）。

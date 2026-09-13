"""ロジスティック回帰。`k` は実行時に dataset から決まる。"""

import math

import blab


@blab.entry
class LinearClassifier:
    def __init__(self, k, lr=0.1):
        self.k, self.lr = k, lr
        self.w = [[0.0, 0.0] for _ in range(k)]
        self.b = [0.0] * k

    def logits(self, x):
        return [sum(wi * xi for wi, xi in zip(w, x)) + b for w, b in zip(self.w, self.b)]

    def predict(self, x):
        scores = self.logits(x)
        return max(range(self.k), key=scores.__getitem__)

    def update(self, x, y):
        scores = self.logits(x)
        top = max(scores)
        exp = [math.exp(s - top) for s in scores]
        total = sum(exp)
        loss = -math.log(exp[y] / total)
        for c in range(self.k):
            g = exp[c] / total - (1.0 if c == y else 0.0)
            for j in range(len(x)):
                self.w[c][j] -= self.lr * g * x[j]
            self.b[c] -= self.lr * g
        return loss

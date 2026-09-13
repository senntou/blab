"""隠れ層 1 枚の MLP。`linear_classifier` と差し替えて使う。"""

import math
import random

import blab

from .layers import Dense


@blab.entry
class MlpClassifier:
    def __init__(self, k, hidden=16, lr=0.1, seed=0):
        rng = random.Random(seed)
        self.k, self.lr = k, lr
        self.h = Dense(2, hidden, rng)
        self.out = Dense(hidden, k, rng)

    def _forward(self, x):
        hidden = [math.tanh(v) for v in self.h.forward(x)]
        return hidden, self.out.forward(hidden)

    def predict(self, x):
        _, scores = self._forward(x)
        return max(range(self.k), key=scores.__getitem__)

    def update(self, x, y):
        hidden, scores = self._forward(x)
        top = max(scores)
        exp = [math.exp(s - top) for s in scores]
        total = sum(exp)
        loss = -math.log(exp[y] / total)
        grad = [exp[c] / total - (1.0 if c == y else 0.0) for c in range(self.k)]
        back = self.out.backward(hidden, grad, self.lr)
        self.h.backward(x, [(1 - h * h) * g for h, g in zip(hidden, back)], self.lr)
        return loss

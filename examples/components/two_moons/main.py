"""two moons の 2 値分類データセット。標準ライブラリだけで作る。"""

import math
import random

import blab


@blab.entry
class TwoMoons:
    """`split` は実行時に trainer から渡される（YAML には書かない）。"""

    def __init__(self, split, n=600, noise=0.2, seed=0):
        self.split = split
        self.n_classes = 2
        # `hash()` は文字列に対してプロセスごとに変わる（PYTHONHASHSEED）ので使わない。
        # split ごとに違い、かつ再実行で一致する種をここで作る。
        rng = random.Random(seed * 1000 + sum(ord(c) for c in split))
        self.samples = [self._point(rng, i % 2, noise) for i in range(n)]

    @staticmethod
    def _point(rng, label, noise):
        t = rng.uniform(0, math.pi)
        if label == 0:
            x, y = math.cos(t), math.sin(t)
        else:
            x, y = 1 - math.cos(t), 0.5 - math.sin(t)
        return [x + rng.gauss(0, noise), y + rng.gauss(0, noise)], label

    def __len__(self):
        return len(self.samples)

    def __iter__(self):
        return iter(self.samples)

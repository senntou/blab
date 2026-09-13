"""group の集計（layout.md §6.2）。

**group の集計値（mean ± std）はファイルに書かない。読むときに導出する。**

配下の**葉の run のみ**を再帰的に集め（内側の group の集計は見ない = 二重集計しない）、
`status == "finished"` な run の `summary.json` からその場で計算する。

これによって「group がいつ完成するのか」という問いが消える。3 fold 回した時点では
3 つぶんが見え、5 つ揃えば 5 つぶんになり、1 つ消しても整合する。v1 が `blab reindex`
で解いていた問題が、導出にしたことで**発生しなくなる**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Stat:
    n: int
    mean: float
    std: float | None
    min: float
    max: float

    def render(self, digits: int = 4) -> str:
        if self.std is None:
            return f"{self.mean:.{digits}f} (n=1)"
        return f"{self.mean:.{digits}f} ± {self.std:.{digits}f} (n={self.n})"


def aggregate(summaries: list[dict]) -> dict[str, Stat]:
    """summary の列から、キーごとの mean ± std を作る。

    `std` は**標本標準偏差（n-1）**。n=1 のときは `None`（0 と書くと「ばらつきが無い」
    という嘘になる）。数値でない値と、数値と非数値が混ざったキーは落とす。
    """
    buckets: dict[str, list[float]] = {}
    rejected: set[str] = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for key, value in summary.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                rejected.add(str(key))
                continue
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                rejected.add(str(key))
                continue
            buckets.setdefault(str(key), []).append(float(value))

    out: dict[str, Stat] = {}
    for key, values in sorted(buckets.items()):
        if key in rejected or not values:
            continue
        n = len(values)
        mean = sum(values) / n
        std = None
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            std = math.sqrt(variance)
        out[key] = Stat(n=n, mean=mean, std=std, min=min(values), max=max(values))
    return out

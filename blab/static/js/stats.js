// mean ± std。**集計は保存されていないので、読むときに導出する**（layout.md §6.2）。
//
// サーバ側（blab/aggregate.py）と同じ定義にしてある。std は標本標準偏差（n-1）で、
// n=1 のときは null。0 と書くと「ばらつきが無い」という嘘になるため。

export function aggregate(summaries) {
  const buckets = new Map();
  const rejected = new Set();

  for (const summary of summaries || []) {
    if (!summary || typeof summary !== 'object') continue;
    for (const [key, value] of Object.entries(summary)) {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        rejected.add(key);
        continue;
      }
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(value);
    }
  }

  const out = {};
  for (const [key, values] of [...buckets.entries()].sort()) {
    if (rejected.has(key) || !values.length) continue;
    const n = values.length;
    const mean = values.reduce((a, b) => a + b, 0) / n;
    let std = null;
    if (n > 1) {
      const variance = values.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1);
      std = Math.sqrt(variance);
    }
    out[key] = { n, mean, std, min: Math.min(...values), max: Math.max(...values) };
  }
  return out;
}

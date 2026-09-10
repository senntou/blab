"""Group 集計（design.md §4）。

配下の**葉の run** だけを再帰的に集める。内側の group 自身の ``summary.json``
は見ない（二重集計しない）。集計対象は ``status == "finished"`` な run のみで、
それ以外は ``n_excluded`` に数える。

``group/summary.json`` は 2 セクションからなる:

- ``values`` — **その group 自身に記録された値**（`Group.log_summary`）。CV 全体で
  1 つしか定義できない値（out-of-fold 指標、fold 対応差の検定など）の置き場所。
  fold 平均では作れないので、配下 run には置けない。**再生成できない正のデータ**。
- ``metrics`` / ``n_runs`` / ``n_excluded`` — 配下 run からの自動集計。キャッシュで
  あって、いつでも再計算できる（`blab reindex`）。

集計は ``values`` を必ず読み直して書き戻す。集計が人の記録を消してはいけない。
"""

from __future__ import annotations

import math
from pathlib import Path

from . import layout
from .io import read_json, write_json_atomic
from .layout import SUMMARY_NAME, isoformat, now
from .schema import SCHEMA_VERSION, STATUS_FINISHED

#: group の ``summary.json`` のうち、人（コード）が記録した値を入れるセクション。
VALUES_KEY = "values"


def read_group_summary(group_dir: Path) -> dict:
    data = read_json(Path(group_dir) / SUMMARY_NAME, {})
    return data if isinstance(data, dict) else {}


def read_group_values(group_dir: Path) -> dict:
    """group 自身に記録された値（``summary.json`` の ``values``）。"""
    values = read_group_summary(group_dir).get(VALUES_KEY)
    return dict(values) if isinstance(values, dict) else {}


def update_group_values(group_dir: Path, values: dict) -> dict:
    """group 自身の値を shallow merge（後勝ち）で書き込む。

    同じファイルの集計セクションはそのまま残す（読んで書き戻す）。
    """
    group_dir = Path(group_dir)
    data = read_group_summary(group_dir)
    merged = {**read_group_values(group_dir), **values}
    data[VALUES_KEY] = merged
    data.setdefault("schema_version", SCHEMA_VERSION)
    write_json_atomic(group_dir / SUMMARY_NAME, data)
    return merged


def iter_leaf_runs(group_dir: Path):
    """group 配下（ネストした group も辿る）の run ディレクトリを列挙する。"""
    for child in sorted(Path(group_dir).iterdir()):
        if not child.is_dir():
            continue
        meta = layout.read_meta(child)
        if not meta:
            continue
        kind = meta.get("kind")
        if kind == layout.KIND_RUN:
            yield child, meta
        elif kind == layout.KIND_GROUP:
            yield from iter_leaf_runs(child)


def _stats(values: list[float]) -> dict:
    n = len(values)
    mean = sum(values) / n
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)  # 標本標準偏差
        std = math.sqrt(var)
    else:
        std = 0.0
    return {
        "mean": mean,
        "std": std,
        "min": min(values),
        "max": max(values),
        "count": n,
    }


def aggregate_group(group_dir: Path) -> dict:
    """group の集計結果を計算して返す（書き込みはしない）。"""
    group_dir = Path(group_dir)
    values: dict[str, list[float]] = {}
    n_runs = 0
    n_excluded = 0
    for run_dir, meta in iter_leaf_runs(group_dir):
        n_runs += 1
        if meta.get("status") != STATUS_FINISHED:
            n_excluded += 1
            continue
        summary = read_json(run_dir / SUMMARY_NAME, {})
        if not isinstance(summary, dict):
            continue
        for key, value in summary.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue  # 数値でないキーは集計しない
            if not math.isfinite(value):
                continue
            values.setdefault(str(key), []).append(float(value))

    metrics = {k: _stats(v) for k, v in sorted(values.items()) if v}
    return {
        "schema_version": SCHEMA_VERSION,
        VALUES_KEY: read_group_values(group_dir),
        "n_runs": n_runs,
        "n_excluded": n_excluded,
        "metrics": metrics,
        "generated_at": isoformat(now()),
    }


def write_group_summary(group_dir: Path) -> dict:
    """集計して ``group/summary.json`` に書き出す。

    集計セクションはキャッシュ（再生成可能）だが、``values`` は正のデータなので
    :func:`aggregate_group` が読み直したものをそのまま書き戻す。
    """
    result = aggregate_group(group_dir)
    write_json_atomic(Path(group_dir) / SUMMARY_NAME, result)
    return result


def reindex(root: Path) -> list[Path]:
    """ルート配下の全 group の summary.json を再生成する。"""
    updated: list[Path] = []
    for meta_path in sorted(Path(root).rglob(layout.META_NAME)):
        d = meta_path.parent
        meta = layout.read_meta(d)
        if meta and meta.get("kind") == layout.KIND_GROUP:
            write_group_summary(d)
            updated.append(d)
    return updated

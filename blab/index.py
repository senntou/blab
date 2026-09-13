"""ログルートの走査（layout.md §4・§9）。

**ディレクトリを読むだけ。** 実行層が何をしたかは知らず、この仕様だけを前提に読む。
したがって blab を使わずに手で規約どおりのディレクトリを作っても読める。

集計は保存されていないので、group の値は読むときに導出する（§6.2）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .aggregate import Stat, aggregate
from .io import read_json
from .run import (
    KIND_GROUP,
    KIND_RUN,
    META_NAME,
    STALE_AFTER_SEC,
    STATUS_FINISHED,
    STATUS_RUNNING,
    SUMMARY_NAME,
)

STATUS_STALE = "stale"

#: 削除の予約 group 名（layout.md §9）。「削除」も内部的には `blab mv` と同じ操作で、
#: この名前の group へ移すだけ。新しい書き込み経路を増やさないための約束。
TRASH_GROUP = "_trash"


@dataclass
class Node:
    """Experiment / Group / Run のいずれか。種別は `meta.json` の `kind`。"""

    path: Path
    meta: dict
    children: list["Node"] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return str(self.meta.get("kind", ""))

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.path.name)

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def status(self) -> str:
        """`running` のまま heartbeat が途絶えたものは `stale` として見せる。"""
        status = str(self.meta.get("status", ""))
        if status != STATUS_RUNNING:
            return status
        beat = self.meta.get("heartbeat_at")
        if not beat:
            return status
        from datetime import datetime

        try:
            last = datetime.fromisoformat(str(beat)).timestamp()
        except ValueError:
            return status
        return STATUS_STALE if (time.time() - last) > STALE_AFTER_SEC else status

    @property
    def summary(self) -> dict:
        doc = read_json(self.path / SUMMARY_NAME, default=None)
        if not isinstance(doc, dict):
            return {}
        # group の summary.json は {"values": {...}}（自分自身の値だけ）。
        if self.kind == KIND_GROUP:
            values = doc.get("values")
            return values if isinstance(values, dict) else {}
        return doc

    def leaf_runs(self) -> list["Node"]:
        """配下の葉の run を再帰的に集める（内側の group の集計は見ない）。"""
        if self.kind == KIND_RUN:
            return [self]
        found: list[Node] = []
        for child in self.children:
            found.extend(child.leaf_runs())
        return found

    def aggregate(self) -> dict[str, Stat]:
        """`status == "finished"` な葉の run の summary から、その場で計算する。"""
        return aggregate(
            [r.summary for r in self.leaf_runs() if r.status == STATUS_FINISHED]
        )


def scan(root: Path) -> list[Node]:
    """ログルート直下の Experiment を走査して木を返す。"""
    root = Path(root)
    if not root.is_dir():
        return []
    return [node for node in (_read(child) for child in sorted(root.iterdir())) if node]


def _read(path: Path) -> Node | None:
    if not path.is_dir():
        return None
    meta = read_json(path / META_NAME, default=None)
    if not isinstance(meta, dict):
        return None  # meta.json を持たないディレクトリはノードではない
    node = Node(path=path, meta=meta)
    if node.kind != KIND_RUN:
        node.children = [
            child for child in (_read(c) for c in sorted(path.iterdir())) if child
        ]
    return node


def walk(nodes: list[Node]):
    """木の全ノードを深さ優先で列挙する。"""
    for node in nodes:
        yield node
        yield from walk(node.children)


def find(root: Path, needle: str) -> Node | None:
    """パス・ULID・短 ID・名前のどれかで 1 ノードを引く。"""
    candidate = Path(needle)
    if candidate.is_dir() and (candidate / META_NAME).is_file():
        return _read(candidate)
    for node in walk(scan(root)):
        if needle in (node.id, node.id[-4:].lower(), node.path.name, node.name):
            return node
    return None


# ------------------------------------------------------------------ 表の列


def flatten_config(doc: dict) -> dict[str, Any]:
    """`resolved.yaml` を、run テーブルの列にできる平たい辞書にする。

    「どのハッシュの resnet18 を使った run か」でソート・フィルタできるように、
    component の版そのものを列にする。

        {"run": "standard_trainer@db83d7b3", "run.epochs": 30,
         "dataset": "two_moons@415672ac", "dataset.n": 600}
    """
    from .resolved import iter_components

    out: dict[str, Any] = {}
    for path, node in iter_components(doc):
        key = path[len("run.") :] if path.startswith("run.") else path
        label = node.get("label") or str(node.get("hash", ""))[7:15]
        out[key] = f"{node.get('use')}@{label}"
        for name, value in _column_args(node).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[f"{key}.{name}"] = value
    return out


def _column_args(node: dict) -> dict:
    """列にできる引数。build ごとに違う値は列にしない（1 行に収まらないため）。"""
    if "args" in node:
        return node.get("args") or {}
    builds = [b.get("args") or {} for b in (node.get("builds") or []) if isinstance(b, dict)]
    if not builds:
        return {}
    if len(builds) == 1:
        return builds[0]
    shared = dict(builds[0])
    for other in builds[1:]:
        for name in list(shared):
            if name not in other or other[name] != shared[name]:
                del shared[name]
    return shared


def row(node: Node, root: Path) -> dict:
    """run / group の 1 行分。"""
    from .resolved import RESOLVED_NAME, load

    out: dict[str, Any] = {
        "path": relpath(root, node.path),
        "kind": node.kind,
        "name": node.name,
        "id": node.id,
        "status": node.status,
        "created_at": node.meta.get("created_at"),
        "duration_sec": node.meta.get("duration_sec"),
        "tags": node.meta.get("tags") or [],
        "notes": node.meta.get("notes") or "",
        "summary": node.summary,
        "config": {},
    }
    if node.kind == KIND_RUN:
        path = node.path / RESOLVED_NAME
        if path.is_file():
            try:
                out["config"] = flatten_config(load(path))
            except Exception:  # noqa: BLE001 - 壊れた run で一覧を落とさない
                out["config"] = {}
        for key in ("source", "replay_of", "moved_from", "exit"):
            if node.meta.get(key):
                out[key] = node.meta[key]
    else:
        stats = node.aggregate()
        out["n_runs"] = len(node.leaf_runs())
        out["aggregate"] = {
            k: {"n": s.n, "mean": s.mean, "std": s.std, "min": s.min, "max": s.max}
            for k, s in stats.items()
        }
    return out


def relpath(root: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


def is_trashed(root: Path, path: Path) -> bool:
    """`_trash` 予約 group の配下か（そのノード自身が `_trash` の場合も含む）。"""
    return TRASH_GROUP in Path(relpath(root, path)).parts


def safe_join(root: Path, relative: str) -> Path:
    """ルート配下の相対パスを解決する。ルート外への脱出は拒否する。"""
    from .errors import BlabError

    raw = str(relative or "").strip()
    if Path(raw).is_absolute():
        raise BlabError(f"安全でないパスです: {relative!r}")
    rel = raw.strip("/")
    if not rel:
        return Path(root).resolve()
    if any(p == ".." for p in Path(rel).parts):
        raise BlabError(f"安全でないパスです: {relative!r}")
    root = Path(root).resolve()
    # symlink 経由の脱出も realpath 解決後に弾く。
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise BlabError(f"ルートの外を指しています: {relative!r}")
    return target


# ------------------------------------------------------------------- metrics


def read_metrics(
    run_path: Path, keys: list[str] | None = None, max_points: int | None = 2000
) -> dict:
    """`metrics.jsonl` を読んで、キーごとの系列にする。"""
    from .io import read_jsonl_from

    records, _ = read_jsonl_from(Path(run_path) / "metrics.jsonl", 0)
    series: dict[str, dict[str, list]] = {}
    for record in records:
        for key, value in record.items():
            if key.startswith("_") or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            if keys and key not in keys:
                continue
            bucket = series.setdefault(key, {"step": [], "epoch": [], "time": [], "value": []})
            bucket["step"].append(record.get("_step"))
            bucket["epoch"].append(record.get("_epoch"))
            bucket["time"].append(record.get("_time"))
            bucket["value"].append(value)

    if max_points:
        for key, bucket in series.items():
            n = len(bucket["value"])
            if n > max_points:
                # 間引く。全部の列で同じ位置を取るので、系列の対応は崩れない。
                picked = [int(i * n / max_points) for i in range(max_points)]
                series[key] = {
                    column: [values[i] for i in picked] for column, values in bucket.items()
                }
    return {"series": series, "keys": sorted(series), "n_records": len(records)}


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_TIFF_SUFFIXES = {".tif", ".tiff"}
_TEXT_SUFFIXES = {".txt", ".log", ".md", ".yaml", ".yml", ".py", ".sh", ".cfg", ".ini", ".toml"}
_CSV_SUFFIXES = {".csv", ".tsv"}
_JSON_SUFFIXES = {".json", ".jsonl"}


def artifact_type(name: str) -> str:
    """拡張子からプレビュー方法を決める（内容は見ない。壊れた拡張子はそのまま「other」）。"""
    suffix = Path(name).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _TIFF_SUFFIXES:
        return "tiff"
    if suffix in _JSON_SUFFIXES:
        return "json"
    if suffix in _CSV_SUFFIXES:
        return "csv"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return "other"


def list_artifacts(run_path: Path) -> list[dict]:
    root = Path(run_path) / "artifacts"
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        name = path.relative_to(root).as_posix()
        out.append(
            {
                "name": name,
                "path": f"artifacts/{name}",
                "type": artifact_type(name),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return out


def list_logs(run_path: Path) -> list[dict]:
    root = Path(run_path) / "logs"
    if not root.is_dir():
        return []
    # UI は先頭のログを既定表示するので stdout.log を先頭に置く
    return [
        {"name": p.name, "size": p.stat().st_size}
        for p in sorted(root.iterdir(), key=lambda p: (p.name != "stdout.log", p.name))
        if p.is_file()
    ]


# ------------------------------------------------------------ component 逆引き


def runs_using(root: Path) -> dict[str, list[tuple[str, Node]]]:
    """component のハッシュ → それを使った run の一覧。

    `resolved.yaml` を読んでハッシュの逆写像を張る（layout.md §9）。
    """
    from .resolved import RESOLVED_NAME, iter_components, load

    out: dict[str, list[tuple[str, Node]]] = {}
    for node in walk(scan(root)):
        if node.kind != KIND_RUN:
            continue
        path = node.path / RESOLVED_NAME
        if not path.is_file():
            continue
        try:
            doc = load(path)
        except Exception:  # noqa: BLE001 - 壊れた run で一覧を落とさない
            continue
        for _, component in iter_components(doc):
            hash = component.get("hash")
            if hash:
                out.setdefault(str(hash), []).append((str(component.get("use")), node))
    return out

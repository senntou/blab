"""ディレクトリ走査とインメモリ索引（閲覧層が使う。書き込みは一切しない）。

- ``meta.json`` を持つディレクトリを収集してツリーを作る。
- run ごとに meta/params/summary の mtime を覚え、変わったものだけ読み直す。
- 消えたディレクトリは索引から除去する。
- ``metrics.jsonl`` は読み終えたバイトオフセットを保持し、追記分だけ読む。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import layout, registry
from .aggregate import VALUES_KEY, aggregate_group
from .errors import BlabError
from .io import read_json, read_jsonl_from
from .layout import (
    ARTIFACTS_DIR,
    COMPONENTS_DIR,
    COMPONENTS_NAME,
    KIND_EXPERIMENT,
    KIND_GROUP,
    KIND_RUN,
    LOGS_DIR,
    META_NAME,
    PARAMS_NAME,
    SUMMARY_NAME,
    TRASH_DIR,
)
from .schema import flatten
from .snapshot import CODE_DIR

_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_TEXT = {".txt", ".log", ".md", ".json", ".jsonl", ".yaml", ".yml", ".py", ".toml"}
_CSV = {".csv", ".tsv"}


def artifact_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _IMAGE:
        return "image"
    if suffix in _CSV:
        return "csv"
    if suffix in _TEXT:
        return "text"
    return "other"


@dataclass
class MetricsCache:
    """1 run の metrics.jsonl を追記分だけ読み足すキャッシュ。"""

    offset: int = 0
    n_rows: int = 0
    keys: dict[str, dict[str, list]] = field(default_factory=dict)

    def update(self, path: Path) -> None:
        records, new_offset = read_jsonl_from(path, self.offset)
        if new_offset < self.offset:  # ファイルが作り直された
            self.keys.clear()
            self.n_rows = 0
        self.offset = new_offset
        for rec in records:
            self.n_rows += 1
            step = rec.get("_step")
            t = rec.get("_time")
            epoch = rec.get("_epoch")
            for k, v in rec.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                col = self.keys.get(k)
                if col is None:
                    col = self.keys[k] = {"step": [], "time": [], "epoch": [], "value": []}
                col["step"].append(step)
                col["time"].append(t)
                col["epoch"].append(epoch)
                col["value"].append(v)


def binding_label(binding: dict) -> str:
    """binding を 1 セルで表す文字列。``id@version``、dirty なら明示する。"""
    id = str(binding.get("id") or "?")
    if binding.get("dirty"):
        return f"{id}@dirty"
    version = binding.get("version")
    return f"{id}@{version}" if version else id


def binding_key(binding: dict) -> str:
    """列名。role があれば role、無ければ id（§4.3）。"""
    role = binding.get("role")
    return str(role) if role else str(binding.get("id") or "?")


def _arg_cell(value: Any) -> Any:
    """binding の args を 1 セルに落とす。参照と未記録は潰さずに見せる。"""
    if isinstance(value, dict):
        if "$ref" in value:
            return f"→#{value['$ref']}"
        if "$unrecorded" in value:
            return f"?{value['$unrecorded']}"
    if isinstance(value, (dict, list, tuple)):
        from .io import dumps

        return dumps(value)
    return value


def binding_columns(components: dict) -> dict[str, Any]:
    """``components.json`` を run テーブルの列に落とす（§4.3）。

    列名が衝突する場合（同じ role が 2 つ、role 無しで同じ id が 2 つ）は
    ``#index`` を付けて区別する。畳んで消すと別物が同じ列に見えるため。
    """
    bindings = components.get("bindings") if isinstance(components, dict) else None
    if not isinstance(bindings, list):
        return {}
    used: dict[str, int] = {}
    out: dict[str, Any] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        key = binding_key(binding)
        used[key] = used.get(key, 0) + 1
        if used[key] > 1:
            key = f"{key}#{binding.get('index')}"
        out[key] = binding_label(binding)
        args = binding.get("args")
        if isinstance(args, dict):
            for name, value in args.items():
                out[f"{key}.{name}"] = _arg_cell(value)
    return out


def components_summary(components: dict) -> dict[str, Any]:
    """run 一覧に出すバッジ用の要約。"""
    bindings = components.get("bindings") if isinstance(components, dict) else None
    bindings = [b for b in bindings if isinstance(b, dict)] if isinstance(bindings, list) else []
    entrypoints = components.get("entrypoints") if isinstance(components, dict) else None
    entrypoints = (
        [e for e in entrypoints if isinstance(e, dict)] if isinstance(entrypoints, list) else []
    )
    unresolved: list[str] = []
    skipped: list[str] = []
    for entry in entrypoints:
        unresolved.extend(str(x) for x in (entry.get("unresolved_imports") or []))
        skipped.extend(str(x) for x in (entry.get("skipped") or []))
    return {
        "n_bindings": len(bindings),
        "n_entrypoints": len(entrypoints),
        "dirty": any(b.get("dirty") for b in bindings),
        "failed": any(b.get("failed") for b in bindings),
        "unresolved_imports": unresolved,
        "skipped": skipped,
        "ids": sorted({str(b.get("id")) for b in bindings if b.get("id")}),
    }


@dataclass
class Node:
    """索引上の 1 ノード（experiment / group / run）。"""

    path: str  # ルートからの相対パス
    kind: str
    dir: Path
    meta: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    #: ``components.json``（bindings と entrypoints）。無い run では空 dict。
    components: dict = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    parent: str | None = None
    mtimes: dict[str, float] = field(default_factory=dict)
    metrics: MetricsCache = field(default_factory=MetricsCache)

    @property
    def name(self) -> str:
        return self.meta.get("name") or self.dir.name

    @property
    def status(self) -> str | None:
        return self.meta.get("status")

    @property
    def stale(self) -> bool:
        return layout.is_stale(self.meta)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class Index:
    """ルート配下の読み取り専用インメモリ索引。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.nodes: dict[str, Node] = {}
        self.roots: list[str] = []  # experiment のパス
        self._lock = threading.RLock()
        self.last_scan: float = 0.0

    # ------------------------------------------------------------ 走査

    def refresh(self) -> None:
        """ルートを走査して索引を更新する。"""
        with self._lock:
            seen: set[str] = set()
            roots: list[str] = []
            for child in self._iter_dirs(self.root):
                node = self._sync(child, parent=None)
                if node is None:
                    continue
                seen.add(node.path)
                if node.kind == KIND_EXPERIMENT:
                    roots.append(node.path)
                self._sync_children(node, seen)
            # 消えたディレクトリを索引から落とす
            for path in list(self.nodes):
                if path not in seen:
                    del self.nodes[path]
            self.roots = sorted(roots)
            self.last_scan = time.time()

    def _iter_dirs(self, path: Path) -> Iterable[Path]:
        try:
            entries = sorted(path.iterdir())
        except OSError:
            return []
        skip = (ARTIFACTS_DIR, LOGS_DIR, TRASH_DIR, CODE_DIR, COMPONENTS_DIR)
        return [p for p in entries if p.is_dir() and p.name not in skip]

    def _sync_children(self, node: Node, seen: set[str]) -> None:
        children: list[str] = []
        for child_dir in self._iter_dirs(node.dir):
            child = self._sync(child_dir, parent=node.path)
            if child is None:
                continue
            seen.add(child.path)
            children.append(child.path)
            if child.kind in (KIND_GROUP, KIND_EXPERIMENT):
                self._sync_children(child, seen)
        node.children = children

    def _sync(self, dir: Path, parent: str | None) -> Node | None:
        meta_path = dir / META_NAME
        if not meta_path.is_file():
            return None
        rel = layout.relpath(self.root, dir)
        node = self.nodes.get(rel)
        if node is None or node.dir != dir:
            node = Node(path=rel, kind="", dir=dir)
            self.nodes[rel] = node
        node.parent = parent

        m = _mtime(meta_path)
        if node.mtimes.get(META_NAME) != m:
            meta = read_json(meta_path, {})
            node.meta = meta if isinstance(meta, dict) else {}
            node.mtimes[META_NAME] = m
        node.kind = node.meta.get("kind") or KIND_RUN

        for fname, attr in (
            (PARAMS_NAME, "params"),
            (SUMMARY_NAME, "summary"),
            (COMPONENTS_NAME, "components"),
        ):
            fpath = dir / fname
            m = _mtime(fpath)
            if node.mtimes.get(fname) != m:
                data = read_json(fpath, {})
                setattr(node, attr, data if isinstance(data, dict) else {})
                node.mtimes[fname] = m
        return node

    # ------------------------------------------------------------ 参照 API

    def get(self, path: str) -> Node | None:
        with self._lock:
            return self.nodes.get(path.strip("/"))

    def require(self, path: str, kind: str | None = None) -> Node:
        node = self.get(path)
        if node is None:
            raise KeyError(path)
        if kind is not None and node.kind != kind:
            raise KeyError(f"{path} is a {node.kind}, not a {kind}")
        return node

    def rename(self, path: str, name: str) -> Node:
        with self._lock:
            node = self.require(path)
            layout.rename_node(node.dir, name)
        self.refresh()
        with self._lock:
            return self.require(path)

    def delete(self, path: str) -> Path:
        """node を丸ごとゴミ箱（``<root>/.blab-trash/``）へ退避する。実行中の run は含められない。"""
        with self._lock:
            node = self.require(path)
            blockers = [node, *self._descendants(node)]
            if any(n.kind == KIND_RUN and n.status == "running" and not n.stale for n in blockers):
                raise BlabError(f"実行中の run が含まれているため削除できません: {path}")
            dest = layout.trash_node(self.root, node.dir)
        self.refresh()
        return dest

    def tree(self) -> list[dict]:
        """軽量な階層ツリー（名前と status のみ）。"""
        with self._lock:
            return [self._tree_node(p) for p in self.roots]

    def _tree_node(self, path: str) -> dict:
        node = self.nodes[path]
        children = [self._tree_node(c) for c in node.children if c in self.nodes]
        out = {
            "path": node.path,
            "name": node.name,
            "kind": node.kind,
            "status": node.status,
            "stale": node.stale,
            "children": children,
        }
        if node.kind == KIND_EXPERIMENT:
            counts = {"run": 0, "group": 0, "running": 0}
            updated = 0.0
            for desc in self._descendants(node):
                if desc.kind == KIND_RUN:
                    counts["run"] += 1
                    if desc.status == "running":
                        counts["running"] += 1
                elif desc.kind == KIND_GROUP:
                    counts["group"] += 1
                updated = max(updated, max(desc.mtimes.values(), default=0.0))
            out["counts"] = counts
            out["updated_at"] = updated or None
        elif node.kind == KIND_GROUP:
            out["group_kind"] = node.meta.get("group_kind")
        return out

    def _descendants(self, node: Node) -> Iterable[Node]:
        for path in node.children:
            child = self.nodes.get(path)
            if child is None:
                continue
            yield child
            yield from self._descendants(child)

    def experiments(self) -> list[dict]:
        with self._lock:
            return [self._tree_node(p) | {"children": []} for p in self.roots]

    def rows(self, experiment: str | None = None, kind: str | None = None) -> list[dict]:
        """テーブル 1 行 = 1 run/group。meta + params(フラット) + summary を結合する。"""
        with self._lock:
            rows = []
            for node in self.nodes.values():
                if node.kind == KIND_EXPERIMENT:
                    continue
                if kind and node.kind != kind:
                    continue
                if experiment and not (
                    node.path == experiment or node.path.startswith(experiment + "/")
                ):
                    continue
                rows.append(self.row(node))
            rows.sort(key=lambda r: r["path"])
            return rows

    def row(self, node: Node) -> dict:
        meta = node.meta
        row = {
            "path": node.path,
            "parent": node.parent,
            "kind": node.kind,
            "id": meta.get("id"),
            "name": node.name,
            "status": meta.get("status"),
            "stale": node.stale,
            "created_at": meta.get("created_at"),
            "finished_at": meta.get("finished_at"),
            "duration_sec": meta.get("duration_sec"),
            "tags": meta.get("tags") or [],
            "notes": meta.get("notes") or "",
            "params": flatten(node.params),
            "summary": {},
            "bindings": binding_columns(node.components),
            "components": components_summary(node.components),
            "policy_violations": meta.get("policy_violations") or [],
        }
        if node.kind == KIND_GROUP:
            row["group_kind"] = meta.get("group_kind")
            agg = node.summary if isinstance(node.summary, dict) else {}
            metrics = agg.get("metrics") or {}
            # group 自身に記録された値（values）と、配下 run の集計（mean）を同じ
            # summary 欄に並べる。**同じキーがあれば group 自身の値が勝つ**——
            # OOF のように「fold 平均では作れない」値を明示的に書いたのだから、
            # 平均で上書きしてはいけない。
            own = flatten(agg.get(VALUES_KEY) or {})
            row["summary"] = {
                **{k: v.get("mean") for k, v in metrics.items() if isinstance(v, dict)},
                **own,
            }
            # UI が「これは10 fold の平均±SD」「これは group 自身の値」を区別するため。
            row["summary_stats"] = {
                k: v for k, v in metrics.items() if isinstance(v, dict) and k not in own
            }
            row["summary_own"] = sorted(own)
            row["aggregate"] = agg
            row["n_children"] = len(node.children)
            # group 自身は component を持たないので、配下 run の共通部分を出す。
            # 値が食い違う列は落とす（「この CV は mlp_classifier@v2 を使った」だけ言う）。
            row["bindings"] = self._common_bindings(node)
        else:
            row["summary"] = flatten(node.summary)
        return row

    def _common_bindings(self, node: Node) -> dict[str, Any]:
        common: dict[str, Any] | None = None
        for child in self._descendants(node):
            if child.kind != KIND_RUN:
                continue
            cols = binding_columns(child.components)
            if common is None:
                common = dict(cols)
                continue
            for key in list(common):
                if common.get(key) != cols.get(key):
                    del common[key]
        return common or {}

    # ------------------------------------------------------------ component（§6.1）

    def _binding_usage(self) -> dict[str, list[dict]]:
        """component id → 使用した run の一覧（逆引き）。

        各 run の ``components.json`` を走査して逆写像を張る。索引は毎回組み直すが、
        走査するのは既に読み込んである Node の中身だけなので I/O は増えない。
        """
        usage: dict[str, list[dict]] = {}
        for node in self.nodes.values():
            if node.kind != KIND_RUN or not node.components:
                continue
            bindings = node.components.get("bindings")
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict) or not binding.get("id"):
                    continue
                usage.setdefault(str(binding["id"]), []).append(
                    {
                        "path": node.path,
                        "name": node.name,
                        "status": node.meta.get("status"),
                        "created_at": node.meta.get("created_at"),
                        "version": binding.get("version"),
                        "dirty": bool(binding.get("dirty")),
                        "failed": bool(binding.get("failed")),
                        "role": binding.get("role"),
                        "index": binding.get("index"),
                        "args": binding.get("args") or {},
                        "loaded_by": binding.get("loaded_by"),
                        "summary": flatten(node.summary),
                    }
                )
        return usage

    def components(self, tags: list[str] | None = None) -> list[dict]:
        """component 一覧（§6.2-4）。使用 run 数と最終使用日を付ける。"""
        with self._lock:
            usage = self._binding_usage()
        want = set(tags or [])
        out = []
        for id in layout.iter_component_ids(self.root):
            try:
                info = registry.describe(self.root, id)
            except BlabError as e:
                out.append({"id": id, "error": str(e), "tags": [], "versions": []})
                continue
            if want and not want.issubset(set(info["tags"])):
                continue
            runs = usage.get(id, [])
            # 1 run が同じ component を複数回 load していても run 数は 1。
            info["n_runs"] = len({r["path"] for r in runs})
            info["n_bindings"] = len(runs)
            info["last_used"] = max((r["created_at"] or "" for r in runs), default=None) or None
            info["versions_used"] = sorted(
                {(r["version"] or ("dirty" if r["dirty"] else "?")) for r in runs}
            )
            out.append(info)
        return out

    def component(self, id: str) -> dict:
        """component 詳細（README / version / ソース / 逆引き）。"""
        info = registry.describe(self.root, id)
        readme, mtime = registry.readme_of(self.root, id)
        with self._lock:
            runs = self._binding_usage().get(id, [])
        info["readme"] = readme
        info["readme_mtime"] = mtime
        info["n_runs"] = len({r["path"] for r in runs})
        info["n_bindings"] = len(runs)
        info["runs"] = sorted(runs, key=lambda r: (r["created_at"] or ""), reverse=True)
        info["by_version"] = {}
        for run in info["runs"]:
            key = run["version"] or ("dirty" if run["dirty"] else "?")
            paths = info["by_version"].setdefault(key, [])
            if run["path"] not in paths:
                paths.append(run["path"])
        return info

    def component_source(self, id: str, version: str | None = None) -> dict:
        text, path = registry.source_of(self.root, id, version)
        return {
            "id": id,
            "version": version,
            "path": layout.relpath(self.root, path),
            "source": text,
        }

    def code(self, path: str, name: str) -> dict:
        """run に snapshot された entrypoint / first-party / component のソース。"""
        node = self.require(path, KIND_RUN)
        target = layout.safe_join(node.dir / CODE_DIR, name)
        if not target.is_file():
            raise KeyError(f"{path}/{CODE_DIR}/{name}")
        return {
            "path": path,
            "name": name,
            "hash": layout.hash_source(target),
            "source": target.read_text(encoding="utf-8", errors="replace"),
        }

    def code_files(self, node: Node) -> list[dict]:
        base = node.dir / CODE_DIR
        if not base.is_dir():
            return []
        out = []
        for file in sorted(base.rglob("*")):
            if not file.is_file():
                continue
            out.append(
                {
                    "name": str(file.relative_to(base)),
                    "size": file.stat().st_size,
                    "hash": layout.hash_source(file),
                }
            )
        return out

    def detail(self, path: str) -> dict:
        node = self.require(path)
        with self._lock:
            row = self.row(node)
            # 出力ディレクトリの絶対パス（UI からワンクリックでコピーできるように）。
            row["dir"] = str(node.dir)
            row["meta"] = node.meta
            row["params_raw"] = node.params
            row["summary_raw"] = node.summary
            row["children"] = [
                self.row(self.nodes[c]) for c in node.children if c in self.nodes
            ]
            # artifacts は group も持てる（CV 全体で 1 つの成果物）。
            if node.kind in (KIND_RUN, KIND_GROUP):
                row["artifacts"] = self.artifacts(path)
                row["logs"] = self._files(node.dir / LOGS_DIR, node.dir)
            if node.kind == KIND_RUN:
                node.metrics.update(node.dir / layout.METRICS_NAME)
                row["metric_keys"] = sorted(node.metrics.keys)
                row["n_metric_rows"] = node.metrics.n_rows
                # 構成パネル（§6.2-3）が使う生の binding と entrypoint
                row["components_raw"] = node.components or {}
                row["code"] = self.code_files(node)
            return row

    # ------------------------------------------------------------ metrics

    def metrics(
        self, path: str, keys: list[str] | None = None, max_points: int | None = None
    ) -> dict:
        node = self.require(path, KIND_RUN)
        with self._lock:
            node.metrics.update(node.dir / layout.METRICS_NAME)
            available = sorted(node.metrics.keys)
            wanted = [k for k in (keys or available) if k in node.metrics.keys]
            series = {}
            for key in wanted:
                col = node.metrics.keys[key]
                n = len(col["value"])
                stride = 1
                if max_points and max_points > 0 and n > max_points:
                    # 等間隔ストライドで間引く（見た目に不満が出たら LTTB 等を検討）。
                    stride = -(-n // max_points)
                series[key] = {
                    "step": col["step"][::stride],
                    "time": col["time"][::stride],
                    "epoch": col["epoch"][::stride],
                    "value": col["value"][::stride],
                    "n": n,
                    "stride": stride,
                }
            return {
                "path": path,
                "keys": available,
                "n_rows": node.metrics.n_rows,
                "series": series,
            }

    # ------------------------------------------------------------ その他

    def artifacts(self, path: str) -> list[dict]:
        node = self.require(path)
        return self._files(node.dir / ARTIFACTS_DIR, node.dir)

    def _files(self, base: Path, run_dir: Path) -> list[dict]:
        out: list[dict] = []
        if not base.is_dir():
            return out
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(run_dir).as_posix()
                st = p.stat()
            except (OSError, ValueError):
                continue
            out.append(
                {
                    "name": p.relative_to(base).as_posix(),
                    "path": rel,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "type": artifact_kind(p.name),
                }
            )
        return out

    def aggregate(self, path: str) -> dict:
        """group 集計。キャッシュ（summary.json）ではなくその場で再計算する。"""
        node = self.require(path, KIND_GROUP)
        return aggregate_group(node.dir)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            kinds: dict[str, int] = {}
            for node in self.nodes.values():
                kinds[node.kind] = kinds.get(node.kind, 0) + 1
            n_components = len(list(layout.iter_component_ids(self.root)))
            return {
                "root": str(self.root),
                "counts": kinds,
                "n_components": n_components,
                "last_scan": self.last_scan,
            }

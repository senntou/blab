"""ログルートの走査（layout.md §4・§9）。

**ディレクトリを読むだけ。** 実行層が何をしたかは知らず、この仕様だけを前提に読む。
したがって blab を使わずに手で規約どおりのディレクトリを作っても読める。

集計は保存されていないので、group の値は読むときに導出する（§6.2）。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .aggregate import Stat, aggregate
from .docs import list_docs
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


class FileCache:
    """ファイルから導出した値を、ファイルの (mtime, size) が変わるまで覚えておく（layout.md §9）。

    `ScanCache` が覚えない実行中のノードの `resolved.yaml` に使う（実行中でも、仮の
    `resolved.yaml` が書き換わるのは開始と終了の 2 回だけなので、毎回解析し直すほどではない）。
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, Path], tuple[tuple[int, int], Any]] = {}
        self._lock = threading.Lock()

    def get(self, path: Path, tag: str, derive: Callable[[Path], Any], default: Any = None) -> Any:
        """`derive(path)` の結果。ファイルが無ければ `default`。`tag` は導出の種類。"""
        path = Path(path)
        try:
            st = os.stat(path)
        except OSError:
            return default
        stamp = (st.st_mtime_ns, st.st_size)
        key = (tag, path)
        with self._lock:
            hit = self._entries.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        value = derive(path)
        with self._lock:
            self._entries[key] = (stamp, value)
        return value

    def retain(self, dirs: set[Path]) -> None:
        """`dirs` の直下のファイル以外の分を捨てる（消えた・移された run の分）。"""
        with self._lock:
            for key in [k for k in self._entries if k[1].parent not in dirs]:
                del self._entries[key]

    def __len__(self) -> int:
        return len(self._entries)


#: `ScanCache` の保存形式。中身の意味を変えたら上げる（古いものは読み捨てる）。
SCAN_CACHE_VERSION = 1


class _Entry:
    """1 ノード分の覚えた値。`stamp` はノードのディレクトリの (mtime, inode)。"""

    __slots__ = ("stamp", "meta", "values")

    def __init__(self, stamp: tuple[int, int], meta: dict, values: dict | None = None) -> None:
        self.stamp = stamp
        self.meta = meta
        self.values = values if values is not None else {}


class ScanCache:
    """走査の結果を、**ノードのディレクトリの mtime が変わるまで**覚えておく（layout.md §9）。

    ログルートが NFS 上にあると、ファイルを 1 つ開くたびにサーバへの往復が要り、run が
    数百あると一覧を開くだけで数秒〜十数秒かかる。そこで、ファイルではなくディレクトリを見る。

    - blab は `meta.json` / `summary.json` / `resolved.yaml` をすべて rename で原子的に書く
      ので、どれかが変わればそのディレクトリの mtime が変わる。ドキュメント（`*.md`）の追加・
      削除も同じ。ディレクトリの mtime は親の readdir でまとめて取れるので、変わっていない
      run のファイルは 1 つも開かない
    - 実行中（`status == "running"`）のノードは覚えない。heartbeat と summary は毎回読む
    - 覚えた値は `path`（ローカルの `~/.blab/cache/`）に保存し、`blab ui` を起動し直しても
      使い回す。消してよい（次の走査で作り直すだけ）

    rename せずに中身だけ書き換えたファイル（手でその場編集した summary.json など）は
    見落とす。そのときはキャッシュのファイルを消して開き直す。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.files = FileCache()
        self._entries: dict[str, _Entry] = {}
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._dirty = False
        if self.path is not None:
            self._load()

    def __len__(self) -> int:
        return len(self._entries)

    # ----------------------------------------------------------- 走査から使う

    def lookup(self, path: Path, stamp: tuple[int, int]) -> _Entry | None:
        key = str(path)
        with self._lock:
            self._seen.add(key)
            hit = self._entries.get(key)
        return hit if hit is not None and hit.stamp == stamp else None

    def store(self, path: Path, stamp: tuple[int, int], meta: dict) -> _Entry:
        entry = _Entry(stamp, meta)
        with self._lock:
            self._entries[str(path)] = entry
            self._dirty = True
        return entry

    def value(self, entry: _Entry, name: str, derive: Callable[[], Any]) -> Any:
        """`entry` に覚えた `name` の値。無ければ `derive()` して覚える。"""
        if name in entry.values:
            return entry.values[name]
        value = derive()
        with self._lock:
            entry.values[name] = value
            self._dirty = True
        return value

    def begin(self) -> None:
        with self._lock:
            self._seen = set()

    def end(self) -> None:
        """走査で見なかったノード（消えた・移された run）の分を捨てる。"""
        with self._lock:
            for key in [k for k in self._entries if k not in self._seen]:
                del self._entries[key]
                self._dirty = True
        self.files.retain({Path(k) for k in self._seen})

    # ---------------------------------------------------------------- 保存

    def save(self) -> None:
        """変わったところがあれば保存する。書けなくても走査は続ける（ただのキャッシュなので）。"""
        if self.path is None:
            return
        with self._lock:
            if not self._dirty:
                return
            doc = {
                "schema_version": SCAN_CACHE_VERSION,
                "entries": {
                    key: {"stamp": list(e.stamp), "meta": e.meta, "values": dict(e.values)}
                    for key, e in self._entries.items()
                },
            }
            self._dirty = False
        from .io import write_json_atomic

        try:
            write_json_atomic(self.path, doc)
        except (OSError, TypeError, ValueError):
            pass

    def _load(self) -> None:
        doc = read_json(self.path, default=None)
        if not isinstance(doc, dict) or doc.get("schema_version") != SCAN_CACHE_VERSION:
            return
        entries = doc.get("entries")
        if not isinstance(entries, dict):
            return
        for key, raw in entries.items():
            try:
                stamp = (int(raw["stamp"][0]), int(raw["stamp"][1]))
                meta, values = raw["meta"], raw["values"]
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if isinstance(meta, dict) and isinstance(values, dict):
                self._entries[key] = _Entry(stamp, meta, values)


def _read_summary(path: Path) -> dict | None:
    doc = read_json(path, default=None)
    return doc if isinstance(doc, dict) else None


@dataclass
class Node:
    """Experiment / Group / Run のいずれか。種別は `meta.json` の `kind`。"""

    path: Path
    meta: dict
    children: list["Node"] = field(default_factory=list)
    cache: ScanCache | None = field(default=None, repr=False, compare=False)
    #: `cache` に覚えた分（実行中のノードは None）
    entry: _Entry | None = field(default=None, repr=False, compare=False)

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

    def remember(self, name: str, derive: Callable[[], Any]) -> Any:
        """ディレクトリが変わるまで覚えておける値。実行中のノードは毎回 `derive()` する。"""
        if self.cache is not None and self.entry is not None:
            return self.cache.value(self.entry, name, derive)
        return derive()

    @property
    def summary(self) -> dict:
        doc = self.remember("summary", lambda: _read_summary(self.path / SUMMARY_NAME))
        if doc is None:
            return {}
        # group の summary.json は {"values": {...}}（自分自身の値だけ）。
        if self.kind == KIND_GROUP:
            values = doc.get("values")
            return values if isinstance(values, dict) else {}
        return doc

    @property
    def doc_names(self) -> list[str]:
        """直下のドキュメントの名前（一覧用。大きさ・更新時刻は要るときに `list_docs` で読む）。"""
        return self.remember("docs", lambda: [d["name"] for d in list_docs(self.path)])

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


def scan(root: Path, cache: ScanCache | None = None) -> list[Node]:
    """ログルート直下の Experiment を走査して木を返す。

    `cache` を渡すと、ディレクトリが変わっていないノードはファイルを読まずに覚えた値を使う
    （`ScanCache`）。実行中のノードの `meta.json`（状態と heartbeat）は毎回読む。
    """
    root = Path(root)
    if not root.is_dir():
        return []
    if cache is not None:
        cache.begin()
    nodes = _children(root, cache)
    if cache is not None:
        cache.end()
    return nodes


def _children(path: Path, cache: ScanCache | None) -> list[Node]:
    # scandir は種別（と NFS ならディレクトリの属性）を readdir でまとめて取るので、
    # 子ごとに stat しに行かない。
    try:
        with os.scandir(path) as it:
            entries = sorted((e for e in it if _is_dir(e)), key=lambda e: e.name)
    except OSError:
        return []
    return [node for node in (_read(Path(e.path), cache, e) for e in entries) if node]


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _read(
    path: Path, cache: ScanCache | None = None, dir_entry: os.DirEntry | None = None
) -> Node | None:
    if dir_entry is None and not path.is_dir():
        return None
    entry = stamp = None
    if cache is not None:
        try:
            # 読む前に取る（読んだ後だと、その間の書き換えを取りこぼす）。
            st = dir_entry.stat() if dir_entry is not None else os.stat(path)
            stamp = (st.st_mtime_ns, st.st_ino)
            entry = cache.lookup(path, stamp)
        except OSError:
            stamp = None
    if entry is not None:
        meta = entry.meta
    else:
        meta = read_json(path / META_NAME, default=None)
        if not isinstance(meta, dict):
            return None  # meta.json を持たないディレクトリはノードではない
        if cache is not None and stamp is not None and meta.get("status") != STATUS_RUNNING:
            entry = cache.store(path, stamp, meta)
    node = Node(path=path, meta=meta, cache=cache, entry=entry)
    if node.kind != KIND_RUN:
        node.children = _children(path, cache)
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


def _resolved_info(path: Path) -> dict | None:
    """`resolved.yaml` のうち、一覧と component の逆引きに要る部分だけ。壊れていれば None。

    中身そのものは返さない（`.build()` に渡した症例リストなどで大きくなることがあるため）。
    """
    from .resolved import iter_components, load

    try:
        doc = load(path)
        components = [
            (str(c.get("use")), str(c["hash"])) for _, c in iter_components(doc) if c.get("hash")
        ]
        return {"config": flatten_config(doc), "components": components}
    except Exception:  # noqa: BLE001 - 壊れた run で一覧を落とさない
        return None


def resolved_info(node: Node) -> dict | None:
    """run の `_resolved_info`。`node.cache` があればそこに覚える。"""
    from .resolved import RESOLVED_NAME

    path = node.path / RESOLVED_NAME
    if node.entry is not None:
        return node.remember("resolved", lambda: _resolved_info(path))
    if node.cache is not None:
        return node.cache.files.get(path, "resolved", _resolved_info)
    return _resolved_info(path) if path.is_file() else None


def row(node: Node, root: Path) -> dict:
    """run / group の 1 行分。"""
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
        "docs": node.doc_names,
        "summary": node.summary,
        "config": {},
    }
    if node.kind == KIND_RUN:
        info = resolved_info(node)
        if info is not None:
            out["config"] = info["config"]
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
    # 走査したノードはルートの下に組み立てたパスなので、まずはそのまま比べる。resolve() は
    # パスの各段を lstat するので、NFS 上で行ごとに呼ぶと遅い。
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        pass
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


def runs_using(
    root: Path, nodes: list[Node] | None = None
) -> dict[str, list[tuple[str, Node]]]:
    """component のハッシュ → それを使った run の一覧。

    `resolved.yaml` を読んでハッシュの逆写像を張る（layout.md §9）。`nodes` に走査済みの木を
    渡すと走査し直さない（UI はキャッシュ付きで走査した木を渡す）。
    """
    out: dict[str, list[tuple[str, Node]]] = {}
    for node in walk(scan(root) if nodes is None else nodes):
        if node.kind != KIND_RUN:
            continue
        info = resolved_info(node)
        if info is None:
            continue
        for use, hash in info["components"]:
            out.setdefault(hash, []).append((use, node))
    return out

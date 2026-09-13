"""外部ファイルの記録（layout.md §5.6）。

学習データ本体、正解ラベル、CV の fold split を書いた CSV — これらも再現性の一部。
**新しい概念は足さない。データの所在を宣言するのも component である。**

**記録するのは同一性であって実体ではない。**

| | |
| --- | --- |
| 小さいファイル（既定 1 MB 未満） | **run にコピーする。** fold split や label CSV は一番失われやすく、一番復元したい |
| 大きいファイル | パス・サイズ・更新時刻・全バイトハッシュ |
| 巨大な単一ファイル（既定 64 MB 以上） | 全バイトは読まない。**先頭 chunk + サイズ**からハッシュを作る（`partial`） |
| 巨大なディレクトリ | 毎回全バイトを読まない。ファイル一覧から manifest ハッシュを作り、`.blab/index/` にキャッシュする |

巨大な単一ファイル（lmdb など）は、比較ビューで「同じデータを指しているか」を見比べられれば
十分で、常に全バイトを読む必要はない。先頭 chunk とサイズが一致すれば実務上は別物とは
考えにくく、末尾までの一致を毎回確認するコストのほうが高い。厳密な全バイト一致を確認したい
場合は、そのファイルを直接 `sha256sum` すればよい（blab はそこまでは保証しない）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .io import read_json, write_json_atomic
from .project import SCHEMA_VERSION

#: これより小さいファイルは run にコピーする。
COPY_UNDER_BYTES = 1024 * 1024

#: これ以上の単一ファイルは全バイトを読まず、先頭 chunk + サイズだけでハッシュする。
HASH_FULL_UNDER_BYTES = 64 * 1024 * 1024

#: 巨大ファイルのハッシュに使う先頭 chunk のサイズ。
PARTIAL_HASH_CHUNK_BYTES = 1024 * 1024

DATA_NAME = "data.json"
DATA_DIR = "data"

HASH_KIND_BYTES = "bytes"
HASH_KIND_PARTIAL = "partial"
HASH_KIND_MANIFEST = "manifest"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _partial_hash_file(path: Path, size: int) -> str:
    """先頭 chunk + サイズだけからハッシュを作る（巨大な単一ファイル用）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(PARTIAL_HASH_CHUNK_BYTES))
    h.update(f"\0{size}".encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _manifest_hash(root: Path) -> tuple[str, int, int]:
    """ディレクトリの manifest ハッシュ。

    **毎回全バイトを読まない。** 相対パス・サイズ・更新時刻の列からハッシュを作る。
    厳密な全バイトハッシュは明示的に要求されたときだけ。
    """
    h = hashlib.sha256()
    count = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            h.update(f"{rel}\0{stat.st_size}\0{int(stat.st_mtime)}\0".encode("utf-8"))
            count += 1
            total += stat.st_size
    return "sha256:" + h.hexdigest(), count, total


def record(
    resolved: dict[str, Path], run_path: Path, *, cache_dir: Path | None = None
) -> dict:
    """解決済みの外部ファイルを `<run>/data.json` に記録する。"""
    doc: dict = {"schema_version": SCHEMA_VERSION}
    for name, path in sorted(resolved.items()):
        doc[name] = _entry(name, Path(path), run_path, cache_dir)
    if len(doc) > 1:
        write_json_atomic(run_path / DATA_NAME, doc, indent=2)
    return doc


def _entry(name: str, path: Path, run_path: Path, cache_dir: Path | None) -> dict:
    try:
        stat = path.stat()
    except OSError as e:
        return {"path": str(path), "$unavailable": f"stat できない: {e}"}

    if path.is_dir():
        hash, n_files, size = _cached_manifest(path, stat, cache_dir)
        return {
            "path": str(path),
            "kind": "dir",
            "n_files": n_files,
            "size": size,
            "hash": hash,
            "hash_kind": HASH_KIND_MANIFEST,
            "copied_to": None,
        }

    if stat.st_size >= HASH_FULL_UNDER_BYTES:
        hash, hash_kind = _partial_hash_file(path, stat.st_size), HASH_KIND_PARTIAL
    else:
        hash, hash_kind = _hash_file(path), HASH_KIND_BYTES

    entry = {
        "path": str(path),
        "kind": "file",
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "hash": hash,
        "hash_kind": hash_kind,
        "copied_to": None,
    }
    if stat.st_size < COPY_UNDER_BYTES:
        target = run_path / DATA_DIR / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        entry["copied_to"] = f"{DATA_DIR}/{path.name}"
    return entry


def _cached_manifest(
    path: Path, stat: os.stat_result, cache_dir: Path | None
) -> tuple[str, int, int]:
    """manifest ハッシュを `.blab/index/` にキャッシュする。"""
    if cache_dir is None:
        return _manifest_hash(path)
    key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    cache_path = Path(cache_dir) / "data" / f"{key}.json"
    cached = read_json(cache_path, default=None)
    if isinstance(cached, dict) and cached.get("mtime") == stat.st_mtime:
        return cached["hash"], cached["n_files"], cached["size"]
    hash, n_files, size = _manifest_hash(path)
    write_json_atomic(
        cache_path,
        {"path": str(path), "mtime": stat.st_mtime, "hash": hash,
         "n_files": n_files, "size": size},
    )
    return hash, n_files, size

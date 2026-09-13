"""FastAPI アプリ（layout.md §9）。

**ディレクトリを読むだけ。** 例外は run の group 付け替え（`blab mv`）だけで、それも
UI がディレクトリを直接触るのではなく実行層の同じ操作を呼ぶ。

閲覧層はこの仕様だけを前提に読むので、blab を使わずに手で規約どおりのディレクトリを
作っても閲覧できる。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import index as index_mod
from ..components import Meta, hash_dir, iter_hashed_files, list_ids, short_hash
from ..errors import BlabError
from ..io import read_json
from ..project import Project
from ..resolved import RESOLVED_NAME, load as load_resolved
from ..run import COMPONENTS_DIR, KIND_GROUP, KIND_RUN

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

#: 索引の再走査を抑制する間隔（UI のポーリングは 3 秒間隔）。
RESCAN_INTERVAL_SEC = 1.0

#: ソースとしてそのまま表示してよい拡張子。
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini"}
MAX_TEXT_BYTES = 2 * 1024 * 1024


class View:
    """走査結果の薄いキャッシュ。真実はディレクトリ側にある。"""

    def __init__(self, project: Project, runs_dir: Path) -> None:
        self.project = project
        self.runs_dir = Path(runs_dir)
        self._nodes: list = []
        self._at = 0.0

    def nodes(self) -> list:
        if time.time() - self._at > RESCAN_INTERVAL_SEC:
            self._nodes = index_mod.scan(self.runs_dir)
            self._at = time.time()
        return self._nodes

    def all(self):
        return list(index_mod.walk(self.nodes()))

    def node(self, path: str):
        try:
            target = index_mod.safe_join(self.runs_dir, path)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        for node in self.all():
            if node.path == target:
                return node
        raise HTTPException(status_code=404, detail=f"見つかりません: {path}")


def create_app(project: Project, runs_dir: Path | None = None) -> FastAPI:
    root = project.runs_dir(runs_dir)
    view = View(project, root)
    app = FastAPI(title="blab", version=__version__, docs_url=None, redoc_url=None)

    # ------------------------------------------------------------- 一覧

    @app.get("/api/status")
    def api_status() -> dict:
        nodes = view.all()
        runs = [n for n in nodes if n.kind == KIND_RUN]
        return {
            "version": __version__,
            "project": project.name,
            "project_uid": project.uid,
            "project_root": str(project.root),
            "runs_dir": str(root),
            "n_runs": len(runs),
            "n_running": sum(1 for n in runs if n.status in ("running", "stale")),
            "scanned_at": view._at,
        }

    @app.get("/api/experiments")
    def api_experiments() -> dict:
        out = []
        for node in view.nodes():
            runs = node.leaf_runs()
            out.append(
                {
                    "path": index_mod.relpath(root, node.path),
                    "name": node.name,
                    "n_runs": len(runs),
                    "n_running": sum(1 for r in runs if r.status in ("running", "stale")),
                    "updated_at": max(
                        (r.meta.get("created_at") or "" for r in runs), default=None
                    ),
                }
            )
        return {"experiments": out}

    @app.get("/api/nodes")
    def api_nodes(
        experiment: str | None = None,
        kind: str | None = Query(default=None, pattern="^(run|group)$"),
    ) -> dict:
        rows = []
        for node in view.all():
            if node.kind not in (KIND_RUN, KIND_GROUP):
                continue
            if kind and node.kind != kind:
                continue
            relative = index_mod.relpath(root, node.path)
            if experiment and not relative.startswith(experiment):
                continue
            rows.append(index_mod.row(node, root))
        return {"rows": rows, "scanned_at": view._at}

    # --------------------------------------------------------------- run

    @app.get("/api/nodes/{path:path}/detail")
    def api_detail(path: str) -> dict:
        node = view.node(path)
        out = index_mod.row(node, root)
        out["meta"] = node.meta
        if node.kind == KIND_RUN:
            resolved_path = node.path / RESOLVED_NAME
            out["resolved"] = (
                load_resolved(resolved_path) if resolved_path.is_file() else None
            )
            out["env"] = read_json(node.path / "env.json", default=None)
            out["data"] = read_json(node.path / "data.json", default=None)
            out["artifacts"] = index_mod.list_artifacts(node.path)
            out["logs"] = index_mod.list_logs(node.path)
            out["components"] = _baked_components(node.path)
        else:
            out["runs"] = [
                index_mod.row(child, root)
                for child in index_mod.walk(node.children)
                if child.kind in (KIND_RUN, KIND_GROUP)
            ]
        return out

    @app.get("/api/nodes/{path:path}/metrics")
    def api_metrics(
        path: str,
        keys: str | None = None,
        max_points: int = Query(default=2000, ge=0, le=200_000),
    ) -> dict:
        node = view.node(path)
        if node.kind != KIND_RUN:
            raise HTTPException(status_code=400, detail="run ではありません")
        wanted = [k for k in (keys or "").split(",") if k] or None
        return index_mod.read_metrics(node.path, keys=wanted, max_points=max_points or None)

    @app.get("/api/nodes/{path:path}/source")
    def api_run_source(path: str, file: str) -> dict:
        """run に焼き込まれた component のソースを読む（layout.md §5.3）。"""
        node = view.node(path)
        return _read_text(node.path / COMPONENTS_DIR, file)

    @app.get("/api/nodes/{path:path}/log")
    def api_log(path: str, name: str, tail: int = Query(default=0, ge=0)) -> dict:
        node = view.node(path)
        target = index_mod.safe_join(node.path / "logs", name)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="ログがありません")
        text = target.read_text(encoding="utf-8", errors="replace")
        if tail:
            text = "\n".join(text.splitlines()[-tail:])
        return {"name": name, "text": text, "size": target.stat().st_size}

    # --------------------------------------------------------- component

    @app.get("/api/components")
    def api_components(tags: str | None = None) -> dict:
        wanted = {t for t in (tags or "").split(",") if t}
        using = index_mod.runs_using(root)
        out = []
        for id in list_ids(project):
            meta = Meta.load(project, id)
            if wanted and not wanted & set(meta.tags):
                continue
            try:
                current = hash_dir(project.components_dir / id)
            except BlabError:
                current = None
            hashes = {h for h, runs in using.items() if any(n == id for n, _ in runs)}
            n_runs = sum(len(using[h]) for h in hashes)
            last = max(
                (n.meta.get("created_at") or "" for h in hashes for _, n in using[h]),
                default=None,
            )
            out.append(
                {
                    "id": id,
                    "hash": current,
                    "short": short_hash(current) if current else None,
                    "label": meta.label_of(current) if current else None,
                    "tags": meta.tags,
                    "labels": meta.labels,
                    "n_runs": n_runs,
                    "n_versions": len(hashes),
                    "last_used": last,
                }
            )
        return {"components": out}

    @app.get("/api/components/{id}")
    def api_component(id: str) -> dict:
        path = project.components_dir / id
        meta = Meta.load(project, id)
        current = hash_dir(path) if path.is_dir() else None

        versions = {}
        if current:
            versions[current] = {"hash": current, "source": "working", "label": meta.label_of(current)}
        for entry in meta.labels:
            versions.setdefault(
                str(entry["hash"]),
                {"hash": str(entry["hash"]), "source": "label", "label": entry.get("name")},
            )

        using = index_mod.runs_using(root)
        by_version: dict[str, list] = {}
        for hash, runs in using.items():
            hits = [n for name, n in runs if name == id]
            if not hits:
                continue
            versions.setdefault(hash, {"hash": hash, "source": "run", "label": meta.label_of(hash)})
            by_version[hash] = [index_mod.row(n, root) for n in hits]

        readme = None
        for name in ("README.ja.md", "README.md"):
            candidate = path / name
            if candidate.is_file():
                readme = candidate.read_text(encoding="utf-8", errors="replace")
                break

        return {
            "id": id,
            "tags": meta.tags,
            "labels": meta.labels,
            "current": current,
            "readme": readme,
            "versions": sorted(versions.values(), key=lambda v: v["hash"]),
            "by_version": by_version,
        }

    @app.get("/api/components/{id}/files")
    def api_component_files(id: str, hash: str | None = None) -> dict:
        directory = _version_dir(id, hash)
        return {
            "id": id,
            "hash": hash_dir(directory),
            "files": [
                {"name": rel, "size": p.stat().st_size} for rel, p in iter_hashed_files(directory)
            ],
        }

    @app.get("/api/components/{id}/source")
    def api_component_source(id: str, file: str, hash: str | None = None) -> dict:
        return _read_text(_version_dir(id, hash), file)

    @app.get("/api/components/{id}/diff")
    def api_component_diff(id: str, a: str, b: str) -> dict:
        import difflib

        left, right = _version_dir(id, a), _version_dir(id, b)
        names = sorted(
            {rel for rel, _ in iter_hashed_files(left)}
            | {rel for rel, _ in iter_hashed_files(right)}
        )
        out = []
        for rel in names:
            before = _lines(left / rel)
            after = _lines(right / rel)
            if before == after:
                continue
            out.append(
                {
                    "file": rel,
                    "diff": "".join(
                        difflib.unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}")
                    ),
                }
            )
        return {"id": id, "a": a, "b": b, "files": out}

    # ------------------------------------------------------------- 書き込み

    @app.post("/api/nodes/{path:path}/group")
    def api_move(path: str, group: str | None = Body(default=None, embed=True)) -> dict:
        """**UI が行う唯一の書き込み。** `blab mv` と同じ操作を呼ぶ（layout.md §9）。"""
        node = view.node(path)
        if node.kind != KIND_RUN:
            raise HTTPException(status_code=400, detail="run ではありません")
        from ..mv import move_run

        try:
            dest = move_run(root, node.path, group)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        view._at = 0.0
        return {"path": index_mod.relpath(root, dest)}

    # ---------------------------------------------------------------- files

    @app.get("/files/{path:path}")
    def api_file(path: str):
        try:
            target = index_mod.safe_join(root, path)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not target.is_file():
            raise HTTPException(status_code=404, detail="見つかりません")
        return FileResponse(target)

    # ------------------------------------------------------------- 補助

    def _version_dir(id: str, hash: str | None) -> Path:
        """id と（任意の）ハッシュから、読むべきディレクトリを決める。"""
        from ..components import find_frozen, find_frozen_by_prefix

        working = project.components_dir / id
        if not hash:
            if not working.is_dir():
                raise HTTPException(status_code=404, detail=f"component {id} がありません")
            return working
        full = hash if hash.startswith("sha256:") else f"sha256:{hash}"
        if working.is_dir() and hash_dir(working) == full:
            return working
        found = find_frozen(project, id, full)
        if found is not None:
            return found
        hits = find_frozen_by_prefix(project, id, full.split(":", 1)[1])
        if len(hits) == 1:
            return hits[0][1]
        # 凍結もラベルも無い版は、それを使った run に焼き込まれている。
        for node in view.all():
            if node.kind != KIND_RUN:
                continue
            baked = node.path / COMPONENTS_DIR / id
            if baked.is_dir() and hash_dir(baked) == full:
                return baked
        raise HTTPException(status_code=404, detail=f"{id}@{hash} の実体が見つかりません")

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _read_text(base: Path, file: str) -> dict:
    try:
        target = index_mod.safe_join(base, file)
    except BlabError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"見つかりません: {file}")
    if target.stat().st_size > MAX_TEXT_BYTES:
        raise HTTPException(status_code=413, detail="大きすぎて表示できません")
    return {
        "file": file,
        "text": target.read_text(encoding="utf-8", errors="replace"),
        "size": target.stat().st_size,
    }


def _baked_components(run_path: Path) -> list[dict]:
    root = Path(run_path) / COMPONENTS_DIR
    if not root.is_dir():
        return []
    out = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        out.append(
            {
                "id": directory.name,
                "hash": hash_dir(directory),
                "files": [rel for rel, _ in iter_hashed_files(directory)],
            }
        )
    return out


def serve(
    project: Project,
    runs_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8420,
    open_browser: bool = False,
) -> None:
    """uvicorn で UI を起動する。既定で 127.0.0.1 のみ listen する。"""
    import uvicorn

    url = f"http://{host}:{port}/"
    print(f"[blab] {project.runs_dir(runs_dir)} を {url} で配信します")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(project, runs_dir), host=host, port=port, log_level="warning")

"""FastAPI アプリ。実験データはほぼ読むだけ。

書き込むのは UI 設定（``<root>/.blab-ui/prefs.json``）に加えて、
UI からの明示的なリネーム（meta.json の name フィールド）と削除
（ディレクトリごと ``<root>/.blab-trash/`` へ退避）のみ。記録データの
中身（metrics/artifacts など）には一切触れない。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, layout
from ..errors import BlabError
from ..index import Index
from ..prefs import PrefsStore

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
#: 索引の再走査を抑制する間隔（UI のポーリングは 2〜5 秒間隔）。
RESCAN_INTERVAL_SEC = 1.0


def create_app(root: Path) -> FastAPI:
    root = Path(root).resolve()
    index = Index(root)
    index.refresh()
    prefs = PrefsStore(root)

    app = FastAPI(title="blab", version=__version__, docs_url=None, redoc_url=None)

    def fresh() -> Index:
        if time.time() - index.last_scan > RESCAN_INTERVAL_SEC:
            index.refresh()
        return index

    def node_or_404(path: str, kind: str | None = None):
        try:
            return fresh().require(path, kind)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/status")
    def api_status() -> dict:
        return {"version": __version__, **fresh().stats()}

    @app.get("/api/tree")
    def api_tree() -> dict:
        idx = fresh()
        return {"root": str(idx.root), "experiments": idx.tree()}

    @app.get("/api/nodes")
    def api_nodes(
        experiment: str | None = None,
        kind: str | None = Query(default=None, pattern="^(run|group)$"),
    ) -> dict:
        idx = fresh()
        return {"rows": idx.rows(experiment=experiment, kind=kind), "scanned_at": idx.last_scan}

    @app.get("/api/runs/{path:path}/metrics")
    def api_metrics(
        path: str,
        keys: str | None = None,
        max_points: int = Query(default=2000, ge=0, le=200_000),
    ) -> dict:
        node_or_404(path, layout.KIND_RUN)
        key_list = [k for k in (keys or "").split(",") if k] or None
        return fresh().metrics(path, keys=key_list, max_points=max_points or None)

    @app.get("/api/runs/{path:path}/artifacts")
    def api_artifacts(path: str) -> dict:
        node_or_404(path)
        return {"artifacts": fresh().artifacts(path)}

    # ------------------------------------------------------------ component（★ blab）

    @app.get("/api/components")
    def api_components(tags: str | None = None) -> dict:
        want = [t for t in (tags or "").split(",") if t]
        return {"components": fresh().components(want or None)}

    @app.get("/api/components/{id}")
    def api_component(id: str) -> dict:
        try:
            return fresh().component(id)
        except BlabError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/components/{id}/source")
    def api_component_source(id: str, version: str | None = None) -> dict:
        try:
            return fresh().component_source(id, version)
        except BlabError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/components/{id}/diff")
    def api_component_diff(id: str, a: str, b: str) -> dict:
        from ..registry import diff_versions

        try:
            return {"id": id, "a": a, "b": b, "diff": diff_versions(root, id, a, b)}
        except BlabError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/components/{id}/runs")
    def api_component_runs(id: str) -> dict:
        try:
            detail = fresh().component(id)
        except BlabError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"id": id, "runs": detail["runs"], "by_version": detail["by_version"]}

    @app.get("/api/runs/{path:path}/code/{name:path}")
    def api_code(path: str, name: str) -> dict:
        node_or_404(path, layout.KIND_RUN)
        try:
            return fresh().code(path, name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/groups/{path:path}/aggregate")
    def api_aggregate(path: str) -> dict:
        node_or_404(path, layout.KIND_GROUP)
        return fresh().aggregate(path)

    @app.get("/api/runs/{path:path}")
    def api_run(path: str) -> dict:
        node_or_404(path)
        return fresh().detail(path)

    # run / group / experiment の名前変更・削除。名前変更は meta.json の name
    # フィールドを書き換えるだけ（ディレクトリは動かさない）。削除はディレクトリ
    # ごと <root>/.blab-trash/ へ退避する（即座には消さない）。
    @app.patch("/api/nodes/{path:path}")
    def api_rename_node(path: str, name: str = Body(embed=True)) -> dict:
        node_or_404(path)
        try:
            node = fresh().rename(path, name)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"path": node.path, "name": node.name}

    @app.delete("/api/nodes/{path:path}")
    def api_delete_node(path: str) -> dict:
        node_or_404(path)
        try:
            dest = fresh().delete(path)
        except BlabError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"ok": True, "trashed_to": str(dest)}

    # UI 設定だけは書き込む。書く先はルート直下の .blab-ui/ のみで、
    # run / group / experiment のディレクトリには一切触れない。
    @app.get("/api/prefs")
    def api_prefs() -> dict:
        return {"prefs": prefs.all()}

    @app.put("/api/prefs")
    def api_put_pref(
        key: str = Query(min_length=1, max_length=200), value: Any = Body(default=None)
    ) -> dict:
        try:
            return {"prefs": prefs.set(key, value)}
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"設定を保存できません: {e}") from e

    @app.get("/files/{path:path}")
    def api_file(path: str):
        try:
            target = layout.safe_join(root, path)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.exception_handler(KeyError)
    def _key_error(_request, exc: KeyError):  # pragma: no cover - 保険
        return JSONResponse({"detail": str(exc)}, status_code=404)

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


def serve(
    root: Path, host: str = "127.0.0.1", port: int = 8420, open_browser: bool = False
) -> None:
    """uvicorn で UI を起動する。既定で 127.0.0.1 のみ listen する。"""
    import uvicorn

    url = f"http://{host}:{port}/"
    print(f"[blab] serving {root} at {url}")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(root), host=host, port=port, log_level="warning")

"""FastAPI アプリ（layout.md §9）。

**ディレクトリを読むだけ。** 例外は run の group 付け替え（`blab mv`）だけで、それも
UI がディレクトリを直接触るのではなく実行層の同じ操作を呼ぶ。

閲覧層はこの仕様だけを前提に読むので、blab を使わずに手で規約どおりのディレクトリを
作っても閲覧できる。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import index as index_mod
from ..components import Meta, hash_dir, iter_hashed_files, list_ids, short_hash
from ..docs import DOC_SUFFIX, list_docs
from ..errors import BlabError
from ..io import read_json
from ..project import PROJECT_MARKER, GlobalIndex, Project, blab_home
from ..resolved import FAST_YAML, RESOLVED_NAME, iter_components, load as load_resolved
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
        # 1 回だけ実パスにしておく（ノードのパスと API のパスを resolve() なしで比べるため）。
        self.runs_dir = Path(runs_dir).resolve()
        self._nodes: list = []
        self._at = 0.0
        self._lock = threading.Lock()
        #: ディレクトリが変わっていないノードの値（index.ScanCache）。ローカルに保存して使い回す。
        self.cache = index_mod.ScanCache(scan_cache_path(self.runs_dir))

    def nodes(self) -> list:
        # 同時に来たリクエストで同じ走査を重ねない。
        with self._lock:
            if time.time() - self._at > RESCAN_INTERVAL_SEC:
                # 前の走査のあとに導出した値（summary・resolved.yaml）もここで保存される。
                self.cache.save()
                self._nodes = index_mod.scan(self.runs_dir, cache=self.cache)
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


class Projects:
    """UI から開けるプロジェクト。グローバル索引（`~/.blab/projects.json`）が候補になる。

    開けるのは索引に載っているものと、起動したディレクトリのプロジェクトだけ。任意のパスを
    UI から開かせない（`--host` で外に出したとき、どのディレクトリでも読めてしまうため）。
    """

    def __init__(self, default: Project | None, runs_dir: Path | None = None) -> None:
        self.default = default
        self._runs_dir = runs_dir
        self._views: dict[str, View] = {}

    def entries(self) -> list[dict]:
        """候補の一覧。索引は他のシェルの `blab init` で増えるので毎回読み直す。"""
        index = GlobalIndex.load().projects
        out = []
        if self.default is not None:
            out.append(self._entry(self.default.uid, self.default.name, self.default.root))
        for uid, entry in sorted(index.items(), key=lambda kv: kv[1].get("name", "")):
            if self.default is not None and uid == self.default.uid:
                continue
            out.append(self._entry(uid, entry.get("name", ""), Path(entry.get("path", ""))))
        return out

    def _entry(self, uid: str, name: str, root: Path) -> dict:
        return {
            "uid": uid,
            "name": name,
            "path": str(root),
            "available": (root / PROJECT_MARKER).is_file(),
        }

    def view(self, uid: str) -> View:
        cached = self._views.get(uid)
        if cached is not None:
            return cached
        if self.default is not None and uid == self.default.uid:
            view = View(self.default, self.default.runs_dir(self._runs_dir))
        else:
            view = View(*self._load(uid))
        self._views[uid] = view
        return view

    def _load(self, uid: str) -> tuple[Project, Path]:
        root = GlobalIndex.load().by_uid(uid)
        if root is None:
            raise HTTPException(status_code=404, detail=f"索引に無いプロジェクトです: {uid}")
        try:
            project = Project.at(root)
        except BlabError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if project.uid != uid:
            # 索引が古く、同じパスに別のプロジェクトが置かれている。
            raise HTTPException(
                status_code=404,
                detail=f"{root} のプロジェクトは {uid} ではありません（索引が古い）。"
                "`blab link` で登録し直してください",
            )
        return project, project.runs_dir()


def create_app(project: Project | None = None, runs_dir: Path | None = None) -> FastAPI:
    """`project` は起動したディレクトリのプロジェクト（UI が最初に開く）。無くてもよい。

    `runs_dir` の上書きは `project` にだけ効く。他のプロジェクトは各自の設定で開く。
    """
    projects = Projects(project, runs_dir)
    app = FastAPI(title="blab", version=__version__, docs_url=None, redoc_url=None)
    api = APIRouter(prefix="/api/p/{uid}")

    def view_of(uid: str) -> View:
        return projects.view(uid)

    # ------------------------------------------------------------- プロジェクト

    @app.get("/api/projects")
    def api_projects() -> dict:
        return {
            "version": __version__,
            "default": project.uid if project is not None else None,
            "projects": projects.entries(),
        }

    # ------------------------------------------------------------- 一覧

    @api.get("/status")
    def api_status(view: View = Depends(view_of)) -> dict:
        nodes = view.all()
        runs = [n for n in nodes if n.kind == KIND_RUN]
        return {
            "version": __version__,
            "project": view.project.name,
            "project_uid": view.project.uid,
            "project_root": str(view.project.root),
            "runs_dir": str(view.runs_dir),
            "n_runs": len(runs),
            "n_running": sum(1 for n in runs if n.status in ("running", "stale")),
            "scanned_at": view._at,
        }

    @api.get("/experiments")
    def api_experiments(view: View = Depends(view_of)) -> dict:
        out = []
        for node in view.nodes():
            runs = [r for r in node.leaf_runs() if not index_mod.is_trashed(view.runs_dir, r.path)]
            out.append(
                {
                    "path": index_mod.relpath(view.runs_dir, node.path),
                    "name": node.name,
                    "docs": node.doc_names,
                    "n_runs": len(runs),
                    "n_running": sum(1 for r in runs if r.status in ("running", "stale")),
                    "updated_at": max(
                        (r.meta.get("created_at") or "" for r in runs), default=None
                    ),
                }
            )
        return {"experiments": out}

    @api.get("/nodes")
    def api_nodes(
        experiment: str | None = None,
        kind: str | None = Query(default=None, pattern="^(run|group)$"),
        include_trash: bool = False,
        view: View = Depends(view_of),
    ) -> dict:
        rows = []
        for node in view.all():
            if node.kind not in (KIND_RUN, KIND_GROUP):
                continue
            if kind and node.kind != kind:
                continue
            if not include_trash and index_mod.is_trashed(view.runs_dir, node.path):
                continue
            relative = index_mod.relpath(view.runs_dir, node.path)
            if experiment and not relative.startswith(experiment):
                continue
            rows.append(index_mod.row(node, view.runs_dir))
        return {"rows": rows, "scanned_at": view._at}

    @api.get("/trash")
    def api_trash(view: View = Depends(view_of)) -> dict:
        """experiment ごとの `_trash` の中身。完全に消すのは CLI（`blab rm`）で、UI は見せるだけ。

        `blab rm` に渡せるよう、パスは絶対パスも返す。
        """
        out = []
        for experiment in view.nodes():
            trash = next(
                (c for c in experiment.children if c.path.name == index_mod.TRASH_GROUP), None
            )
            if trash is None:
                continue
            runs = trash.leaf_runs()
            out.append(
                {
                    "experiment": index_mod.relpath(view.runs_dir, experiment.path),
                    "name": experiment.name,
                    "path": index_mod.relpath(view.runs_dir, trash.path),
                    "abs_path": str(trash.path),
                    "runs": [
                        {**index_mod.row(r, view.runs_dir), "abs_path": str(r.path)} for r in runs
                    ],
                }
            )
        return {"runs_dir": str(view.runs_dir), "experiments": out}

    # --------------------------------------------------------------- run

    @api.get("/nodes/{path:path}/detail")
    def api_detail(path: str, view: View = Depends(view_of)) -> dict:
        node = view.node(path)
        out = index_mod.row(node, view.runs_dir)
        out["meta"] = node.meta
        out["docs"] = list_docs(node.path)
        if node.kind == KIND_RUN:
            resolved_path = node.path / RESOLVED_NAME
            try:
                resolved = load_resolved(resolved_path)
            except BlabError:
                resolved = None
            out["resolved"] = resolved
            out["env"] = read_json(node.path / "env.json", default=None)
            out["data"] = read_json(node.path / "data.json", default=None)
            out["artifacts"] = index_mod.list_artifacts(node.path)
            out["logs"] = index_mod.list_logs(node.path)
            out["components"] = _baked_components(node.path, resolved)
        else:
            out["runs"] = [
                index_mod.row(child, view.runs_dir)
                for child in index_mod.walk(node.children)
                if child.kind in (KIND_RUN, KIND_GROUP)
            ]
        return out

    @api.get("/nodes/{path:path}/metrics")
    def api_metrics(
        path: str,
        keys: str | None = None,
        max_points: int = Query(default=2000, ge=0, le=200_000),
        view: View = Depends(view_of),
    ) -> dict:
        node = view.node(path)
        if node.kind != KIND_RUN:
            raise HTTPException(status_code=400, detail="run ではありません")
        wanted = [k for k in (keys or "").split(",") if k] or None
        return index_mod.read_metrics(node.path, keys=wanted, max_points=max_points or None)

    @api.get("/nodes/{path:path}/source")
    def api_run_source(path: str, file: str, view: View = Depends(view_of)) -> dict:
        """run に焼き込まれた component のソースを読む（layout.md §5.3）。"""
        node = view.node(path)
        return _read_text(node.path / COMPONENTS_DIR, file)

    @api.get("/nodes/{path:path}/experiment-source")
    def api_experiment_source(path: str, view: View = Depends(view_of)) -> dict:
        """この run を生んだ experiments/*.yaml そのもの（宣言 = 何を書いたか）。"""
        node = view.node(path)
        rel = node.meta.get("source")
        if not rel:
            raise HTTPException(status_code=404, detail="元の yaml が記録されていません")
        return _read_text(view.project.root, rel)

    @api.get("/nodes/{path:path}/docs")
    def api_docs(path: str, view: View = Depends(view_of)) -> dict:
        """ドキュメントの一覧と notes だけ（experiment ページ用。detail より軽い）。"""
        node = view.node(path)
        return {
            "path": index_mod.relpath(view.runs_dir, node.path),
            "kind": node.kind,
            "name": node.name,
            "notes": node.meta.get("notes") or "",
            "docs": list_docs(node.path),
        }

    @api.get("/nodes/{path:path}/doc")
    def api_doc(path: str, name: str, view: View = Depends(view_of)) -> dict:
        """ノード直下の Markdown（layout.md §5.7）。直下の `*.md` 以外は読ませない。"""
        node = view.node(path)
        if "/" in name or "\\" in name or not name.lower().endswith(DOC_SUFFIX):
            raise HTTPException(status_code=400, detail=f"ドキュメントではありません: {name}")
        return _read_text(node.path, name)

    @api.get("/nodes/{path:path}/log")
    def api_log(
        path: str,
        name: str,
        tail: int = Query(default=0, ge=0),
        view: View = Depends(view_of),
    ) -> dict:
        node = view.node(path)
        target = index_mod.safe_join(node.path / "logs", name)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="ログがありません")
        text = target.read_text(encoding="utf-8", errors="replace")
        if tail:
            text = "\n".join(text.splitlines()[-tail:])
        return {"name": name, "text": text, "size": target.stat().st_size}

    # --------------------------------------------------------- component

    @api.get("/components")
    def api_components(tags: str | None = None, view: View = Depends(view_of)) -> dict:
        wanted = {t for t in (tags or "").split(",") if t}
        using = index_mod.runs_using(view.runs_dir, view.nodes())
        out = []
        for id in list_ids(view.project):
            meta = Meta.load(view.project, id)
            if wanted and not wanted & set(meta.tags):
                continue
            try:
                current = hash_dir(view.project.components_dir / id)
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

    @api.get("/components/{id}")
    def api_component(id: str, view: View = Depends(view_of)) -> dict:
        path = view.project.components_dir / id
        meta = Meta.load(view.project, id)
        current = hash_dir(path) if path.is_dir() else None

        versions = {}
        if current:
            versions[current] = {"hash": current, "source": "working", "label": meta.label_of(current)}
        for entry in meta.labels:
            versions.setdefault(
                str(entry["hash"]),
                {"hash": str(entry["hash"]), "source": "label", "label": entry.get("name")},
            )

        using = index_mod.runs_using(view.runs_dir, view.nodes())
        by_version: dict[str, list] = {}
        for hash, runs in using.items():
            hits = [n for name, n in runs if name == id]
            if not hits:
                continue
            versions.setdefault(hash, {"hash": hash, "source": "run", "label": meta.label_of(hash)})
            by_version[hash] = [index_mod.row(n, view.runs_dir) for n in hits]

        readme = _readme(path)

        return {
            "id": id,
            "tags": meta.tags,
            "labels": meta.labels,
            "current": current,
            "readme": readme,
            "versions": sorted(versions.values(), key=lambda v: v["hash"]),
            "by_version": by_version,
        }

    @api.get("/components/{id}/readme")
    def api_component_readme(id: str, view: View = Depends(view_of)) -> dict:
        """README だけ（run ページの構成ツリー用）。`/components/{id}` は全 run の逆引きと
        ハッシュ計算をするので重い。"""
        return {"id": id, "readme": _readme(view.project.components_dir / id)}

    @api.get("/components/{id}/files")
    def api_component_files(
        id: str, hash: str | None = None, view: View = Depends(view_of)
    ) -> dict:
        directory = _version_dir(view, id, hash)
        return {
            "id": id,
            "hash": hash_dir(directory),
            "files": [
                {"name": rel, "size": p.stat().st_size} for rel, p in iter_hashed_files(directory)
            ],
        }

    @api.get("/components/{id}/source")
    def api_component_source(
        id: str, file: str, hash: str | None = None, view: View = Depends(view_of)
    ) -> dict:
        return _read_text(_version_dir(view, id, hash), file)

    @api.get("/components/{id}/diff")
    def api_component_diff(id: str, a: str, b: str, view: View = Depends(view_of)) -> dict:
        import difflib

        left, right = _version_dir(view, id, a), _version_dir(view, id, b)
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

    @api.post("/nodes/{path:path}/group")
    def api_move(
        path: str,
        group: str | None = Body(default=None, embed=True),
        view: View = Depends(view_of),
    ) -> dict:
        """**UI が行う唯一の書き込み。** `blab mv` と同じ操作を呼ぶ（layout.md §9）。

        UI の「削除」もこれを呼ぶだけ：予約 group `_trash`（`index.TRASH_GROUP`）へ
        移すことを削除として扱う。新しい書き込み経路は増やさない。
        """
        node = view.node(path)
        if node.kind != KIND_RUN:
            raise HTTPException(status_code=400, detail="run ではありません")
        from ..mv import move_run

        try:
            dest = move_run(view.runs_dir, node.path, group)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        view._at = 0.0
        return {"path": index_mod.relpath(view.runs_dir, dest)}

    # ---------------------------------------------------------------- files

    @app.get("/files/{uid}/{path:path}")
    def api_file(path: str, view: View = Depends(view_of)):
        try:
            target = index_mod.safe_join(view.runs_dir, path)
        except BlabError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not target.is_file():
            raise HTTPException(status_code=404, detail="見つかりません")
        return FileResponse(target)

    # ------------------------------------------------------------- 補助

    def _version_dir(view: View, id: str, hash: str | None) -> Path:
        """id と（任意の）ハッシュから、読むべきディレクトリを決める。"""
        from ..components import find_frozen, find_frozen_by_prefix

        working = view.project.components_dir / id
        if not hash:
            if not working.is_dir():
                raise HTTPException(status_code=404, detail=f"component {id} がありません")
            return working
        full = hash if hash.startswith("sha256:") else f"sha256:{hash}"
        if working.is_dir() and hash_dir(working) == full:
            return working
        found = find_frozen(view.project, id, full)
        if found is not None:
            return found
        hits = find_frozen_by_prefix(view.project, id, full.split(":", 1)[1])
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

    app.include_router(api)
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


def _readme(path: Path) -> str | None:
    for name in ("README.ja.md", "README.md"):
        candidate = path / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


def _baked_components(run_path: Path, resolved: dict | None) -> list[dict]:
    """焼き込まれた component の一覧。ハッシュは `resolved.yaml` に記録されたものを使う
    （ここで計算し直すと、run を開くたびに全ソースを読むことになる）。"""
    root = Path(run_path) / COMPONENTS_DIR
    if not root.is_dir():
        return []
    recorded: dict[str, str] = {}
    if isinstance(resolved, dict):
        try:
            for _, component in iter_components(resolved):
                if component.get("hash"):
                    recorded.setdefault(str(component.get("use")), str(component["hash"]))
        except Exception:  # noqa: BLE001 - 壊れた resolved.yaml でも一覧は出す
            pass
    out = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        out.append(
            {
                "id": directory.name,
                "hash": recorded.get(directory.name),
                "files": [rel for rel, _ in iter_hashed_files(directory)],
            }
        )
    return out


def scan_cache_path(runs_dir: Path) -> Path:
    """ログルートごとの走査キャッシュの置き場所（ローカルの `~/.blab/cache/`）。"""
    digest = hashlib.sha256(str(runs_dir).encode("utf-8")).hexdigest()[:16]
    return blab_home() / "cache" / f"scan-{digest}.json"


def serve(
    project: Project | None,
    runs_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8420,
    open_browser: bool = False,
) -> None:
    """uvicorn で UI を起動する。既定で 127.0.0.1 のみ listen する。"""
    import uvicorn

    url = f"http://{host}:{port}/"
    if project is not None:
        print(f"[blab] {project.runs_dir(runs_dir)} を {url} で配信します")
    else:
        print(f"[blab] {url} で配信します（プロジェクトは UI で選びます）")
    if not FAST_YAML:
        import sys

        print(
            "[blab] 警告: PyYAML が libyaml なしで入っているため、resolved.yaml の読み込みが"
            "遅くなります（run の一覧が数秒〜数十秒かかることがあります）。"
            "PyPI の wheel には libyaml が入っているので、pip install --force-reinstall pyyaml "
            "で入れ直してください（確認: python -c 'import yaml; print(yaml.__with_libyaml__)'）",
            file=sys.stderr,
        )
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(project, runs_dir), host=host, port=port, log_level="warning")

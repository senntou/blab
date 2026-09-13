"""プロジェクト（`blab.json`）とグローバル索引。

仕様は layout.md §1。要点は 3 つ。

- **1 git リポジトリ = 1 プロジェクト。** `blab.json` があるディレクトリがルート
- **ディレクトリ名を同一性の根拠にしない。** 主キーは `blab.json` の `project_uid`
- **`.blab/` には真実を 1 つも置かない。** 消しても作業コピーと run から再構築できる
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BlabError
from .ids import new_ulid
from .io import read_json, write_json_atomic

SCHEMA_VERSION = 2

PROJECT_MARKER = "blab.json"
LOCAL_MARKER = "blab.local.json"
CACHE_DIR = ".blab"

#: `.gitignore` に blab が書き足す行。すでにある行は足さない。
GITIGNORE_LINES = [
    "# blab",
    "/.blab/",
    "/blab.local.json",
    "/runs/",
]

_GITIGNORE_HEADER = "# blab"


# --------------------------------------------------------------- グローバル索引


def blab_home() -> Path:
    """`~/.blab`。テストと分離運用のため `BLAB_HOME` で差し替えられる。"""
    env = os.environ.get("BLAB_HOME")
    return Path(env).expanduser() if env else Path.home() / ".blab"


@dataclass
class GlobalIndex:
    """`~/.blab/projects.json`。**ただのキャッシュ**で、壊れても再登録で直る。

    別プロジェクトの component 参照（`seg-baseline/unet@v2`）をパスに解決するために
    だけ要る。ここに真実は無い（真実は各プロジェクトの `blab.json`）。
    """

    path: Path
    projects: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls) -> GlobalIndex:
        path = blab_home() / "projects.json"
        doc = read_json(path, default=None) or {}
        projects = doc.get("projects")
        return cls(path=path, projects=projects if isinstance(projects, dict) else {})

    def save(self) -> None:
        write_json_atomic(
            self.path, {"schema_version": SCHEMA_VERSION, "projects": self.projects}
        )

    def register(self, uid: str, name: str, root: Path) -> None:
        self.projects[uid] = {"name": name, "path": str(Path(root).resolve())}
        self.save()

    def by_name(self, name: str) -> Path | None:
        """プロジェクト名から実パスを引く。

        同名が複数あれば曖昧なのでエラーにする（黙ってどちらかを選ばない）。
        """
        hits = [e for e in self.projects.values() if e.get("name") == name]
        if not hits:
            return None
        if len(hits) > 1:
            paths = ", ".join(str(h.get("path")) for h in hits)
            raise BlabError(
                f"プロジェクト名 {name!r} がグローバル索引に {len(hits)} 件あります（{paths}）。"
                "blab.json の project を一意な名前に直してください"
            )
        return Path(hits[0]["path"])

    def by_uid(self, uid: str) -> Path | None:
        entry = self.projects.get(uid)
        return Path(entry["path"]) if entry else None


# ------------------------------------------------------------------ プロジェクト


def find_root(start: Path | None = None) -> Path | None:
    """`blab.json` を持つディレクトリを上位に向かって探す。"""
    cur = Path(start or Path.cwd()).resolve()
    for path in [cur, *cur.parents]:
        if (path / PROJECT_MARKER).is_file():
            return path
    return None


@dataclass
class Project:
    """1 つのプロジェクト。`blab.json` と `blab.local.json` を合わせた view。"""

    root: Path
    uid: str
    name: str
    config: dict
    local: dict

    # ---- 読み込み

    @classmethod
    def load(cls, start: Path | None = None) -> Project:
        root = find_root(start)
        if root is None:
            where = Path(start or Path.cwd()).resolve()
            raise BlabError(
                f"{where} から上位に {PROJECT_MARKER} が見つかりません。"
                "プロジェクトのルートで `blab init` を実行してください"
            )
        return cls.at(root)

    @classmethod
    def at(cls, root: Path) -> Project:
        root = Path(root).resolve()
        config = read_json(root / PROJECT_MARKER, default=None)
        if not isinstance(config, dict):
            raise BlabError(f"{root / PROJECT_MARKER} を読めません（壊れているか JSON でない）")
        uid = config.get("project_uid")
        if not uid:
            raise BlabError(
                f"{root / PROJECT_MARKER} に project_uid がありません。"
                "これがプロジェクトの主キーなので、手で足すか `blab init` をやり直してください"
            )
        version = config.get("schema_version")
        if version != SCHEMA_VERSION:
            raise BlabError(
                f"{root / PROJECT_MARKER} の schema_version が {version!r} です"
                f"（この blab は {SCHEMA_VERSION} を読みます）"
            )
        local = read_json(root / LOCAL_MARKER, default=None)
        return cls(
            root=root,
            uid=str(uid),
            name=str(config.get("project", root.name)),
            config=config,
            local=local if isinstance(local, dict) else {},
        )

    # ---- ディレクトリ

    @property
    def components_dir(self) -> Path:
        return self.root / self.config.get("components_dir", "components")

    @property
    def experiments_dir(self) -> Path:
        return self.root / self.config.get("experiments_dir", "experiments")

    @property
    def cache_dir(self) -> Path:
        """`.blab/`。真実を置かない。いつ消しても安全。"""
        return self.root / CACHE_DIR

    @property
    def frozen_dir(self) -> Path:
        """ラベルの無い自動凍結版の置き場所（`.blab/frozen/`）。"""
        return self.cache_dir / "frozen"

    @property
    def labeled_dir(self) -> Path:
        """ラベルの付いた版の置き場所（`components/.frozen/`）。git 管理下。"""
        return self.components_dir / ".frozen"

    @property
    def meta_dir(self) -> Path:
        """ラベル・タグの置き場所（`components/.meta/`）。git 管理下・ハッシュ対象外。"""
        return self.components_dir / ".meta"

    @property
    def index_dir(self) -> Path:
        """導出物のキャッシュ（`.blab/index/`）。"""
        return self.cache_dir / "index"

    def runs_dir(self, override: str | Path | None = None) -> Path:
        """ログルートを決める（layout.md §1.1）。

        優先順位: `BLAB_RUNS` → `--runs-dir` → `blab.local.json` → `blab.json` → `<project>/runs`。
        相対パスはプロジェクトルートからの相対。
        """
        for candidate in (
            os.environ.get("BLAB_RUNS"),
            override,
            self.local.get("runs_dir"),
            self.config.get("runs_dir"),
        ):
            if candidate:
                path = Path(candidate).expanduser()
                return path if path.is_absolute() else (self.root / path).resolve()
        return self.root / "runs"

    # ---- 設定

    @property
    def require_tags(self) -> list[str]:
        """事前検証で要求するタグ（layout.md §3.2-8）。"""
        tags = self.config.get("require_tags") or []
        return [str(t) for t in tags] if isinstance(tags, list) else []

    @property
    def data_paths(self) -> dict[str, str]:
        """外部ファイルの論理名 → 実パス（`blab.local.json` の `data`）。"""
        data = self.local.get("data") or {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


# ------------------------------------------------------------------------ init


def init(
    root: Path, name: str | None = None, *, runs_dir: str | None = None
) -> tuple[Project, bool]:
    """プロジェクトを作る。

    `blab.json` を書き、`components/` と `experiments/` を作り、`.gitignore` に
    blab の行を足し、グローバル索引に登録する。

    Returns:
        (プロジェクト, 新規に作ったか)。既存なら索引への再登録だけを行う。
    """
    root = Path(root).resolve()
    marker = root / PROJECT_MARKER
    if marker.exists():
        project = Project.at(root)
        GlobalIndex.load().register(project.uid, project.name, project.root)
        return project, False

    root.mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": SCHEMA_VERSION,
        "project": name or root.name,
        "project_uid": new_ulid(),
        # 既定値も明示して書く（挙動を隠さない）。
        "components_dir": "components",
        "experiments_dir": "experiments",
        "runs_dir": runs_dir,
        "require_tags": [],
    }
    write_json_atomic(marker, config, indent=2)

    project = Project.at(root)
    project.components_dir.mkdir(parents=True, exist_ok=True)
    project.experiments_dir.mkdir(parents=True, exist_ok=True)
    write_gitignore(root)
    GlobalIndex.load().register(project.uid, project.name, project.root)
    return project, True


def write_gitignore(root: Path) -> bool:
    """`.gitignore` に blab の行を足す。既にある行は足さない。

    Returns:
        書き足したか。
    """
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [line for line in GITIGNORE_LINES if line not in present]
    if not missing or missing == [_GITIGNORE_HEADER]:
        return False
    block = "\n".join(missing)
    sep = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{sep}{block}\n", encoding="utf-8")
    return True

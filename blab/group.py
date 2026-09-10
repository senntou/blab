"""Group オブジェクト（CV などで run をまとめる）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import layout, schema
from .aggregate import read_group_values, update_group_values, write_group_summary
from .errors import BlabError, guard
from .io import write_json_atomic
from .layout import META_NAME
from .run import Run, create_run
from .schema import STATUS_FAILED, STATUS_FINISHED, STATUS_KILLED, STATUS_RUNNING


class Group:
    """複数 run をまとめるノード。終了時に配下 run の summary を自動集計する。

    **group 自身にも値を記録できる**（:meth:`log_summary`）。fold 平均では作れない
    値——out-of-fold 指標や fold 対応差の検定など、CV 全体で 1 つしか定義できない
    もの——の置き場所で、配下 run の集計とは別セクションに入る（`aggregate` 参照）。

    直接は生成せず :func:`blab.group` / :meth:`Group.group` で作るか、
    :func:`blab.open` で既存の group を開き直す。
    """

    def __init__(self, dir: Path, root: Path, meta: dict, *, existing: bool = False) -> None:
        self.dir = Path(dir)
        self.root = Path(root)
        self._meta = meta
        self._finished = False
        self._reopened = bool(existing)
        self._started_monotonic = time.monotonic()
        if not existing:
            self._write_meta()

    # ------------------------------------------------------------- 属性

    @property
    def id(self) -> str:
        return self._meta["id"]

    @property
    def name(self) -> str | None:
        return self._meta.get("name")

    @property
    def status(self) -> str:
        return self._meta.get("status", STATUS_RUNNING)

    @property
    def path(self) -> str:
        return layout.relpath(self.root, self.dir)

    @property
    def meta(self) -> dict:
        return dict(self._meta)

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"<blab.Group {self.path} status={self.status}>"

    def _write_meta(self) -> None:
        write_json_atomic(self.dir / META_NAME, self._meta)

    # ------------------------------------------------------------- 記録 API

    @guard
    def log_summary(self, data: dict) -> None:
        """**この group 自身**の値。複数回呼ぶと shallow merge（後勝ち）。

        配下 run の集計（mean ± std）とは別枠で、``summary.json`` の ``values``
        セクションに入る。集計しても消えない。
        """
        update_group_values(self.dir, schema.normalize_summary(data))

    @property
    def summary(self) -> dict:
        """この group 自身に記録済みの値。"""
        return read_group_values(self.dir)

    @guard
    def log_artifact(
        self, path: str | os.PathLike, name: str | None = None, mode: str = "copy"
    ) -> Path | None:
        """ファイルを group の ``artifacts/`` に保存する（run と同じ規約）。

        「CV 全体で 1 つ」の成果物——fold 横断の per-fold CSV や比較プロットなど——を
        どの fold にも属さない形で置ける。
        """
        return layout.store_artifact(self.dir, Path(path), name, mode)

    @guard
    def set_notes(self, notes: str) -> None:
        self._meta["notes"] = str(notes or "")
        self._write_meta()

    # ------------------------------------------------------------- 子の作成

    def run(
        self,
        *,
        name: str | None = None,
        params: dict | None = None,
        tags=None,
        notes: str = "",
        flush_interval: float = 0.0,
    ) -> Run:
        """この group 配下に run を作る。"""
        from . import _register_run

        run = create_run(
            self.dir,
            self.root,
            name=name,
            params=params,
            tags=tags,
            notes=notes,
            flush_interval=flush_interval,
            on_finish=_release_run,
        )
        _register_run(run)
        return run

    def group(
        self,
        *,
        name: str | None = None,
        tags=None,
        notes: str = "",
        group_kind: str | None = None,
    ) -> "Group":
        """ネストした group を作る。"""
        return create_group(
            self.dir, self.root, name=name, tags=tags, notes=notes, group_kind=group_kind
        )

    # ------------------------------------------------------------- 終了

    @guard
    def finish(self, status: str | None = STATUS_FINISHED) -> None:
        """配下 run の summary を集計して書き出し、group を終了させる。

        ``status=None`` なら meta は書き換えず集計だけやり直す（開き直した group の
        既定）。後付けで値を足しただけで実行時刻を塗り替えない。
        """
        if self._finished:
            return
        self._finished = True
        if status is not None:
            finished_at = layout.now()
            self._meta["status"] = status
            self._meta["finished_at"] = layout.isoformat(finished_at)
            self._meta["duration_sec"] = round(time.monotonic() - self._started_monotonic, 3)
            self._write_meta()
        # 集計は status に関わらず行う（failed でも finished な配下 run だけで集計する）。
        write_group_summary(self.dir)

    def __enter__(self) -> "Group":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._reopened:
            self.finish(None)
            return False
        if exc_type is None:
            status = STATUS_FINISHED
        elif issubclass(exc_type, KeyboardInterrupt):
            status = STATUS_KILLED
        else:
            status = STATUS_FAILED
        self.finish(status)
        return False


def _release_run(run: Run) -> None:
    from . import _release

    _release(run)


def open_group(dir: Path, root: Path) -> Group:
    """既存の group ディレクトリを開き直して :class:`Group` を返す。"""
    dir = Path(dir)
    meta = layout.read_meta(dir)
    if meta is None:
        raise BlabError(f"group ではありません（meta.json が無い）: {dir}")
    if meta.get("kind") != layout.KIND_GROUP:
        raise BlabError(f"{dir} は {meta.get('kind')} であって group ではありません")
    return Group(dir, root, meta, existing=True)


def create_group(
    parent: Path,
    root: Path,
    *,
    name: str | None = None,
    tags=None,
    notes: str = "",
    group_kind: str | None = None,
) -> Group:
    path, ulid, created = layout.create_node_dir(Path(parent), name)
    meta = schema.new_group_meta(
        id=ulid, name=name, created_at=created, tags=tags, notes=notes, group_kind=group_kind
    )
    return Group(path, root, meta)

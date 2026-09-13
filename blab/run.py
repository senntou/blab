"""記録 API と run ディレクトリ（layout.md §4-5）。

`execute(run)` が受け取る `run` が記録の入口。

    run.log({"train/loss": 0.31}, epoch=3)       # 時系列 → metrics.jsonl
    run.log_summary({"test/acc": 0.87})          # 1 run に 1 つの値 → summary.json
    run.log_artifact("outputs/cm.png")           # ファイル → artifacts/
    run.path                                     # run ディレクトリ

**記録は決して学習を落とさない。** 記録系の失敗は警告に落とす（`BLAB_STRICT=1` で送出）。
唯一の例外は事前検証で、そちらは実行前に止まるので学習時間を失わない。

ハイパラは component の引数であり、`resolved.yaml` に全部入っている（`params.json` は持たない）。
"""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import BlabError, guard, strict, warn
from .ids import isoformat, now
from .io import JsonlWriter, write_json_atomic
from .project import SCHEMA_VERSION

KIND_EXPERIMENT = "experiment"
KIND_GROUP = "group"
KIND_RUN = "run"

STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"

META_NAME = "meta.json"
SUMMARY_NAME = "summary.json"
METRICS_NAME = "metrics.jsonl"
ENV_NAME = "env.json"
DATA_NAME = "data.json"
ARTIFACTS_DIR = "artifacts"
LOGS_DIR = "logs"
COMPONENTS_DIR = "components"
DATA_DIR = "data"

#: heartbeat の更新間隔と、`stale` とみなすまでの時間（layout.md §5.1）。
HEARTBEAT_INTERVAL_SEC = 15
STALE_AFTER_SEC = 60

#: `metrics.jsonl` の予約キー。
RESERVED_METRIC_KEYS = ("_step", "_time", "_epoch")


class Run:
    """1 回の実験の記録。`blab run` が作り、`execute(run)` に渡す。"""

    def __init__(self, path: Path, ulid: str, created: datetime, meta: dict) -> None:
        self.path = Path(path)
        self.id = ulid
        self.created_at = created
        self._meta = meta
        self._summary: dict[str, Any] = {}
        self._metrics: JsonlWriter | None = None
        self._step = 0
        self._lock = threading.Lock()
        self._heartbeat: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ 記録 API

    @guard
    def log(self, values: dict, *, step: int | None = None, epoch: int | None = None) -> None:
        """時系列の値を 1 行追記する。

        `step` 省略時は run 内部のカウンタで自動採番する（0 始まりで +1。明示指定が
        あればカウンタはその値に追従する）。
        """
        if not isinstance(values, dict):
            raise BlabError("run.log() にはマッピングを渡してください")
        with self._lock:
            if step is None:
                step = self._step
            self._step = int(step) + 1
            record: dict[str, Any] = {"_step": int(step), "_time": time.time()}
            if epoch is not None:
                record["_epoch"] = int(epoch)
            record.update(_clean_metric_keys(values))
            if self._metrics is None:
                self._metrics = JsonlWriter(self.path / METRICS_NAME)
            self._metrics.write(record)

    @guard
    def log_summary(self, values: dict) -> None:
        """1 run に 1 つのスカラ値。複数回呼ぶと shallow merge（同キーは後勝ち）。"""
        if not isinstance(values, dict):
            raise BlabError("run.log_summary() にはマッピングを渡してください")
        with self._lock:
            self._summary.update(values)
            write_json_atomic(self.path / SUMMARY_NAME, self._summary)

    @guard
    def log_artifact(self, source: Path | str, name: str | None = None) -> Path | None:
        """ファイルを `artifacts/` にコピーする。"""
        source = Path(source)
        if not source.exists():
            raise BlabError(f"アーティファクトがありません: {source}")
        target = self.path / ARTIFACTS_DIR / (name or source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        return target

    @property
    def summary(self) -> dict:
        return dict(self._summary)

    # ------------------------------------------------------------ 状態

    def _write_meta(self) -> None:
        write_json_atomic(self.path / META_NAME, self._meta, indent=2)

    def start_heartbeat(self) -> None:
        """`heartbeat_at` を 15 秒毎に更新する（プロセス強制終了の検出用）。"""
        if self._heartbeat is not None:
            return

        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_INTERVAL_SEC):
                try:
                    self._meta["heartbeat_at"] = isoformat(now())
                    self._write_meta()
                except Exception:  # noqa: BLE001 - 記録は学習を落とさない
                    return

        self._heartbeat = threading.Thread(target=beat, daemon=True, name="blab-heartbeat")
        self._heartbeat.start()

    def finish(self, status: str, exit: dict | None = None) -> None:
        """run を終了状態にする。"""
        self._stop.set()
        finished = now()
        self._meta["status"] = status
        self._meta["finished_at"] = isoformat(finished)
        self._meta["duration_sec"] = round(
            (finished - self.created_at).total_seconds(), 3
        )
        self._meta["heartbeat_at"] = isoformat(finished)
        if exit is not None:
            self._meta["exit"] = exit
        self._write_meta()
        if self._metrics is not None:
            self._metrics.close()
            self._metrics = None


def _clean_metric_keys(values: dict) -> dict:
    """予約キーとドットを弾く。

    UI は `optim.lr` のようにドット区切りでフラット化して列にするので、**キーに
    ドットは使えない**。既定では警告して `_` に置換、`BLAB_STRICT=1` ならエラー。
    """
    out = {}
    for key, value in values.items():
        name = str(key)
        if name in RESERVED_METRIC_KEYS:
            raise BlabError(f"{name!r} は blab の予約キーです")
        if "." in name:
            if strict():
                raise BlabError(f"metric のキーにドットは使えません: {name!r}")
            warn(f"metric のキー {name!r} のドットを _ に置き換えました")
            name = name.replace(".", "_")
        out[name] = value
    return out


# ------------------------------------------------------------- ディレクトリ


def read_meta(path: Path) -> dict | None:
    from .io import read_json

    doc = read_json(Path(path) / META_NAME, default=None)
    return doc if isinstance(doc, dict) else None


def new_meta(
    kind: str,
    ulid: str,
    created: datetime,
    *,
    name: str | None = None,
    project_uid: str | None = None,
    source: str | None = None,
    extra: dict | None = None,
) -> dict:
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "id": ulid,
        "name": name,
        "created_at": isoformat(created),
    }
    if kind == KIND_RUN:
        meta.update(
            {
                "status": STATUS_RUNNING,
                "project_uid": project_uid,
                "source": source,
                "finished_at": None,
                "duration_sec": None,
                "heartbeat_at": isoformat(created),
                "exit": None,
            }
        )
    meta["tags"] = []
    meta["notes"] = ""
    if extra:
        meta.update(extra)
    return meta


def ensure_container(path: Path, kind: str, name: str) -> None:
    """Experiment / Group のディレクトリと `meta.json` を用意する。

    **複数プロセスが競合しうる**（同じ group 名を名乗る run を並列で回す場合）ので、
    `makedirs(exist_ok=True)` と「無ければ書く」で扱い、既にあれば黙って使う。
    """
    import os

    from .ids import new_ulid

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    meta_path = path / META_NAME
    if meta_path.exists():
        return
    meta = new_meta(kind, new_ulid(), now(), name=name)
    from .io import dumps

    try:
        fd = os.open(meta_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return  # 別プロセスが先に書いた。
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dumps(meta, indent=2) + "\n")

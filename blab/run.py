"""Run オブジェクト（記録層の中心）。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from . import layout, registry, schema, snapshot
from .errors import BlabError, BlabUsageError, guard, warn
from .io import JsonlWriter, last_jsonl_record, read_json, write_json_atomic
from .layout import (
    ARTIFACTS_DIR,
    COMPONENTS_NAME,
    HEARTBEAT_INTERVAL_SEC,
    LOGS_DIR,
    META_NAME,
    METRICS_NAME,
    PARAMS_NAME,
    SUMMARY_NAME,
)
from .schema import STATUS_FAILED, STATUS_FINISHED, STATUS_KILLED, STATUS_RUNNING

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class Run:
    """1 つの実行。ディレクトリに書くだけのオブジェクト。

    直接は生成せず :func:`blab.init` / :meth:`blab.Group.run` で作るか、
    :func:`blab.open` で既存の run を開き直す。
    """

    def __init__(
        self,
        dir: Path,
        root: Path,
        meta: dict,
        params: dict | None = None,
        *,
        flush_interval: float = 0.0,
        on_finish=None,
        existing: bool = False,
        resume: bool = False,
    ) -> None:
        self.dir = Path(dir)
        self.root = Path(root)
        self._meta = meta
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._next_step = 0
        self._summary: dict[str, Any] = {}
        self._finished = False
        self._policy_error: BlabError | None = None
        self._on_finish = on_finish
        self._started_monotonic = time.monotonic()
        # 開き直した run（existing）は params を書き直さない。resume=True のときだけ
        # status を running に戻し、heartbeat と経過時間の計測を再開する。
        self._reopened = bool(existing)
        self._resumed = bool(existing and resume)
        self._elapsed_before = 0.0

        (self.dir / ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
        (self.dir / LOGS_DIR).mkdir(parents=True, exist_ok=True)
        if existing:
            stored = read_json(self.dir / SUMMARY_NAME, {})
            self._summary = dict(stored) if isinstance(stored, dict) else {}
            self._next_step = _next_step_of(self.dir / METRICS_NAME)
            self._elapsed_before = float(self._meta.get("duration_sec") or 0.0)
            if self._resumed:
                self._meta["status"] = STATUS_RUNNING
                self._meta["finished_at"] = None
                self._write_meta()
        else:
            self._write_meta()
            write_json_atomic(self.dir / PARAMS_NAME, schema.normalize_params(params or {}))
        self._metrics = JsonlWriter(self.dir / METRICS_NAME, flush_interval=flush_interval)
        self._init_components()

        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        if not existing or self._resumed:
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop, name=f"blab-heartbeat-{self.id}", daemon=True
            )
            self._heartbeat.start()

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
        """ルートからの相対パス（UI の ``{path}`` と同じ表記）。"""
        return layout.relpath(self.root, self.dir)

    @property
    def meta(self) -> dict:
        return dict(self._meta)

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"<blab.Run {self.path} status={self.status}>"

    # ------------------------------------------------------------- 内部

    def _write_meta(self) -> None:
        # fork した子プロセスからは書かない（rank0 / 単一プロセス書き込みの前提）。
        if os.getpid() != self._pid:
            return
        write_json_atomic(self.dir / META_NAME, self._meta)

    # --------------------------------------------------- 構成の記録（§4.1 / §4.2）

    def _init_components(self) -> None:
        """entrypoint を snapshot し、この run の binding 観測を始める。

        開き直した run では既存の binding / entrypoint を保ったまま積み増す。
        「この後付けの metric は何のコードが出したのか」に答えられるようにするため。
        """
        stored = read_json(self.dir / COMPONENTS_NAME, {}) or {}
        if not isinstance(stored, dict):
            stored = {}
        existing = stored.get("bindings")
        self._bindings_before: list[dict] = [b for b in existing if isinstance(b, dict)] if isinstance(existing, list) else []
        entrypoints = stored.get("entrypoints")
        self._entrypoints: list[dict] = [e for e in entrypoints if isinstance(e, dict)] if isinstance(entrypoints, list) else []

        self._entrypoint_index = self._push_entrypoint(self._snapshot_entrypoint())
        self._recorder = registry.BindingRecorder(
            root=self.root,
            start_index=len(self._bindings_before),
            entrypoint=self._entrypoint_index,
            on_change=lambda _rec: self._write_components(),
        )
        self._recorder_restore = registry.set_recorder(self._recorder)
        self._warned_no_components = False
        self._write_components()

    def _push_entrypoint(self, record: dict) -> int:
        """entrypoint を積む。同じスクリプトの同じ内容・同じ argv なら 1 件に畳む。

        同じ後付けスクリプトを何度も当てても配列が伸び続けないようにするが、
        **いつ最初に走っていつ最後に走ったか**は残す（回数を黙って消さない）。
        """
        key = (record.get("name"), record.get("hash"), record.get("argv"))
        for index, existing in enumerate(self._entrypoints):
            if (existing.get("name"), existing.get("hash"), existing.get("argv")) != key:
                continue
            existing["last_recorded_at"] = record.get("recorded_at")
            existing["n_recorded"] = int(existing.get("n_recorded") or 1) + 1
            for field in ("path", "first_party", "unresolved_imports", "skipped", "project_root"):
                if record.get(field):
                    existing[field] = record[field]
            return index
        self._entrypoints.append(record)
        return len(self._entrypoints) - 1

    def _snapshot_entrypoint(self) -> dict:
        try:
            return snapshot.snapshot_entrypoint(self.dir, self.root, argv=schema.cmdline())
        except Exception as e:  # noqa: BLE001 - snapshot の失敗で学習を止めない
            warn(f"entrypoint を snapshot できません: {e!r}")
            return {
                "name": None,
                "path": None,
                "hash": None,
                "recorded_at": layout.isoformat(layout.now()),
                "argv": schema.cmdline(),
                "unresolved_imports": [f"snapshot に失敗しました: {e!r}"],
            }

    def _write_components(self) -> None:
        """``components.json`` を書く。binding が増えるたびに呼ばれる。"""
        if os.getpid() != self._pid:
            return
        bindings = []
        for binding in self._recorder.bindings:
            if binding.dirty and binding.snapshot is None and binding.source is not None:
                binding.snapshot = snapshot.snapshot_dirty_component(
                    self.dir, binding.id, binding.source
                )
            bindings.append(binding.to_json())
        write_json_atomic(
            self.dir / COMPONENTS_NAME,
            schema.new_components_doc(self._bindings_before + bindings, self._entrypoints),
        )

    def _all_bindings(self) -> list[dict]:
        return self._bindings_before + self._recorder.to_json()

    @property
    def components(self) -> list[dict]:
        """この run で観測された binding（開き直す前のものを含む）。"""
        return self._all_bindings()

    def _check_policy(self) -> list[str]:
        """``blab.json`` のポリシーを検査する（§3.5）。判定のみ。"""
        try:
            policy = schema.read_policy(self.root)
            if policy.get("on_missing") == schema.ON_MISSING_IGNORE:
                return []
            return schema.check_policy(self.root, policy, self._all_bindings())
        except Exception as e:  # noqa: BLE001 - 検査自体の失敗で学習を落とさない
            warn(f"ポリシーを検査できません: {e!r}")
            return []

    def _warn_if_no_components(self) -> None:
        """初回 log() での早期検知。8 時間学習してから知らされるのでは遅い（§3.5）。

        load が log より後に来る書き方もありうるので、ここでは止めない。
        """
        if self._warned_no_components or self._all_bindings():
            return
        self._warned_no_components = True
        try:
            policy = schema.read_policy(self.root)
        except Exception:  # noqa: BLE001
            return
        if policy.get("on_missing") == schema.ON_MISSING_ERROR and policy.get(
            "require_components"
        ):
            warn(
                "component を 1 つも load していません。"
                "blab.json の on_missing=error なので、このままだと finish() で失敗します"
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                with self._lock:
                    if self._finished:
                        return
                    self._meta["heartbeat_at"] = layout.isoformat(layout.now())
                    self._write_meta()
            except Exception as e:  # noqa: BLE001 - 学習本体に波及させない
                warn(f"heartbeat failed: {e!r}")

    # ------------------------------------------------------------- 記録 API

    @guard
    def log(self, data: dict, step: int | None = None, epoch: int | None = None) -> None:
        """1 レコードを ``metrics.jsonl`` に追記する。

        ``step`` 省略時は内部カウンタで自動採番する（0 始まりで +1）。明示指定が
        あればカウンタはその値に追従する。
        """
        record = schema.normalize_metrics(data)
        self._warn_if_no_components()
        with self._lock:
            if self._finished:
                raise BlabUsageError("log() called on a finished run")
            if step is None:
                step = self._next_step
            else:
                step = int(step)
            self._next_step = step + 1
            row = {"_step": step, "_time": time.time()}
            if epoch is not None:
                row["_epoch"] = int(epoch)
            row.update(record)
            self._metrics.write(row)

    @guard
    def log_summary(self, data: dict) -> None:
        """1 run につき 1 つのスカラ値群。複数回呼ぶと shallow merge（後勝ち）。"""
        values = schema.normalize_summary(data)
        with self._lock:
            self._summary.update(values)
            write_json_atomic(self.dir / SUMMARY_NAME, self._summary)

    @guard
    def log_artifact(self, path: str | os.PathLike, name: str | None = None, mode: str = "copy") -> Path | None:
        """ファイルを ``artifacts/`` に保存する。``mode`` は ``copy``（既定）か ``move``。"""
        return layout.store_artifact(self.dir, Path(path), name, mode)

    @guard
    def log_image(self, key: str, image: Any) -> Path | None:
        """画像を ``artifacts/`` に保存する薄いヘルパ。

        パスならそのままコピーし、配列なら PIL で PNG として保存する。
        """
        if isinstance(image, (str, os.PathLike)):
            src = Path(image)
            suffix = src.suffix if src.suffix.lower() in _IMAGE_SUFFIXES else ".png"
            return self.log_artifact(src, name=f"{key}{suffix}")
        try:
            from PIL import Image  # type: ignore
        except ImportError as e:  # pragma: no cover - 環境依存
            raise BlabError("log_image() with an array requires Pillow") from e
        img = image if isinstance(image, Image.Image) else Image.fromarray(_as_uint8(image))
        dest = layout.safe_join(self.dir / ARTIFACTS_DIR, f"{key}.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest)
        return dest

    # ------------------------------------------------------------- 終了

    def finish(self, status: str | None = STATUS_FINISHED) -> None:
        """run を終了させ、meta に最終状態を書く。二度目以降は何もしない。

        ``status=None`` なら meta を書き換えず、書き込みだけ閉じる。**後付け記録の
        ために開き直した run（``blab.open(..., resume=False)``）の既定はこちら**で、
        終わった実験の status / 実行時間を後から塗り替えない。

        ``blab.json`` の ``on_missing="error"`` でポリシー違反があった場合は、
        **すべて書き切ってから**例外を投げる（§3.5）。データは失わせない。
        """
        self._finish(status)
        error, self._policy_error = self._policy_error, None
        if error is not None:
            raise error

    @guard
    def _finish(self, status: str | None) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._stop.set()
            self._metrics.close()
            # 1. 構成の記録を締める。entrypoint の first-party 追跡は、実行が進んだ
            #    あとの sys.modules を見るほうが完全になるのでここで撮り直す。
            self._close_components()
            # 2. ポリシーを検査して、違反を meta に永続化する（端末は揮発する）
            violations = self._check_policy()
            if violations:
                self._meta["policy_violations"] = violations
            # 3. status は finished のまま確定させる。ポリシー違反は記録の規律の
            #    問題であって、実験の失敗ではない
            if status is not None:
                finished_at = layout.now()
                self._meta["status"] = status
                self._meta["finished_at"] = layout.isoformat(finished_at)
                self._meta["duration_sec"] = round(
                    self._elapsed_before + time.monotonic() - self._started_monotonic, 3
                )
                self._meta["heartbeat_at"] = layout.isoformat(finished_at)
            self._meta.setdefault("env", {})
            if isinstance(self._meta.get("env"), dict):
                # 実体を snapshot しないインストール済みパッケージは、名前と version を残す
                self._meta["env"]["packages"] = snapshot.installed_packages()
            self._write_meta()
            # 4. そのうえで例外を投げる（送出は guard の外側の finish() で行う）
            if violations and self._policy_mode() == schema.ON_MISSING_ERROR:
                self._policy_error = BlabError(
                    "blab.json のポリシー違反: " + " / ".join(violations)
                )
            elif violations:
                warn("blab.json のポリシー違反: " + " / ".join(violations))
        if self._on_finish is not None:
            self._on_finish(self)

    def _policy_mode(self) -> str:
        try:
            return str(schema.read_policy(self.root).get("on_missing"))
        except Exception:  # noqa: BLE001
            return schema.ON_MISSING_WARN

    def _close_components(self) -> None:
        registry.set_recorder(self._recorder_restore)
        try:
            refreshed = self._snapshot_entrypoint()
            current = self._entrypoints[self._entrypoint_index]
            # 初回の recorded_at と回数は保ったまま、追跡結果だけ更新する
            for field in ("path", "hash", "first_party", "unresolved_imports", "skipped", "project_root"):
                if field in refreshed:
                    current[field] = refreshed[field]
            self._write_components()
        except Exception as e:  # noqa: BLE001 - 記録の失敗で学習を落とさない
            warn(f"components を締められません: {e!r}")

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._reopened and not self._resumed:
            # 後付け記録のために開いただけ。元の実験の status は触らない。
            status: str | None = None
        elif exc_type is None:
            status = STATUS_FINISHED
        elif issubclass(exc_type, KeyboardInterrupt):
            status = STATUS_KILLED
        else:
            status = STATUS_FAILED
        self._finish(status)
        error, self._policy_error = self._policy_error, None
        if error is None:
            return False
        if exc_type is not None:
            # 学習が落ちた原因のほうが重い。ポリシー違反は警告に落として隠さない。
            warn(str(error))
            return False
        raise error


def _as_uint8(array: Any):
    """float 配列（0..1 想定）を uint8 に直す。それ以外はそのまま返す。"""
    try:
        import numpy as np  # type: ignore
    except ImportError:  # pragma: no cover - 環境依存
        return array
    arr = np.asarray(array)
    if arr.dtype.kind == "f":
        hi = float(arr.max()) if arr.size else 1.0
        arr = arr * 255.0 if hi <= 1.0 else arr
        arr = np.clip(arr, 0, 255)
    return arr.astype("uint8")


def create_run(
    parent: Path,
    root: Path,
    *,
    name: str | None = None,
    params: dict | None = None,
    tags=None,
    notes: str = "",
    flush_interval: float = 0.0,
    on_finish=None,
) -> Run:
    """``parent`` の下に run ディレクトリを作って :class:`Run` を返す。"""
    path, ulid, created = layout.create_node_dir(Path(parent), name)
    meta = schema.new_run_meta(id=ulid, name=name, created_at=created, tags=tags, notes=notes)
    return Run(path, root, meta, params, flush_interval=flush_interval, on_finish=on_finish)


def _next_step_of(metrics_path: Path) -> int:
    """既存 ``metrics.jsonl`` の続きの step 番号（最終行 + 1、無ければ 0）。"""
    last = last_jsonl_record(metrics_path)
    if not last:
        return 0
    try:
        return int(last.get("_step", -1)) + 1
    except (TypeError, ValueError):
        return 0


def open_run(
    dir: Path,
    root: Path,
    *,
    resume: bool = False,
    flush_interval: float = 0.0,
    on_finish=None,
) -> Run:
    """既存の run ディレクトリを開き直して :class:`Run` を返す。

    ``params.json`` は書き換えず、``summary.json`` は読み込んでからマージする
    （後付けの `log_summary` が既存キーを消さない）。``metrics.jsonl`` は追記で、
    step は最終行の続きから採番する。
    """
    dir = Path(dir)
    meta = layout.read_meta(dir)
    if meta is None:
        raise BlabError(f"run ではありません（meta.json が無い）: {dir}")
    if meta.get("kind") != layout.KIND_RUN:
        raise BlabError(f"{dir} は {meta.get('kind')} であって run ではありません")
    return Run(
        dir, root, meta,
        flush_interval=flush_interval, on_finish=on_finish,
        existing=True, resume=resume,
    )


def load_summary(run_dir: Path) -> dict:
    data = read_json(Path(run_dir) / SUMMARY_NAME, {})
    return data if isinstance(data, dict) else {}

"""`blab run` の本体（layout.md §3.2・§5）。

    YAML 読み込み → 参照解決 → ハッシュ → 凍結 → 凍結版から import → 検査 → 実行

検査で止まるので、8 時間学習してからタイポに気づく、ということが構造的に起きない。
"""

from __future__ import annotations

import signal
import traceback
from dataclasses import dataclass
from pathlib import Path

from . import data as data_mod
from . import env as env_mod
from . import resolved as resolved_mod
from .builder import Recorder, instantiate_root
from .capture import Capture
from .components import Resolver, copy_hashed
from .errors import BlabError
from .ids import create_run_dir, slugify
from .preflight import Prepared, Report, preflight
from .project import Project
from .run import (
    COMPONENTS_DIR,
    ENV_NAME,
    KIND_EXPERIMENT,
    KIND_GROUP,
    KIND_RUN,
    LOGS_DIR,
    STATUS_FAILED,
    STATUS_FINISHED,
    STATUS_KILLED,
    Run,
    ensure_container,
    new_meta,
)
from .spec import Experiment


class Interrupted(BaseException):
    """`SIGTERM` / `SIGINT` を受けた。`killed` として記録する。"""


@dataclass
class Outcome:
    run: Run
    report: Report
    status: str
    unused: list[str]
    #: 再実行で、記録と今回の実行時引数が食い違った点（layout.md §7）。
    mismatches: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.mismatches is None:
            self.mismatches = []


def execute(
    project: Project,
    experiment: Experiment,
    *,
    runs_dir: Path | None = None,
    capture: bool = True,
    report: Report | None = None,
    replay_of: dict | None = None,
    recorded: dict | None = None,
) -> Outcome:
    """事前検証 → run ディレクトリ作成 → 組み立て → `execute(run)`。"""
    if report is None:
        report = preflight(project, experiment, resolver=Resolver(project))
    report.raise_if_failed()
    assert report.root is not None

    run, path = _create_run(project, experiment, runs_dir, replay_of=replay_of)
    try:
        _bake_components(report.root, path / COMPONENTS_DIR)
        data_mod.record(report.data, path, cache_dir=project.index_dir)
        from .io import write_json_atomic

        write_json_atomic(path / ENV_NAME, env_mod.collect(project.root), indent=2)
    except Exception as e:  # noqa: BLE001 - 記録の失敗で学習を始められなくはしない
        from .errors import warn

        warn(f"run の初期記録に失敗しました: {e!r}")

    source = _relative_source(project, experiment)
    resolved_path = path / resolved_mod.RESOLVED_NAME

    def record(*, provisional: bool) -> None:
        resolved_mod.dump(
            resolved_path,
            resolved_mod.build_document(
                experiment,
                recorder,
                project_uid=project.uid,
                source=source,
                provisional=provisional,
            ),
        )

    def record_provisional() -> None:
        # 実行中に構成を見られるようにするための仮の記録。書けなくても学習は止めない。
        try:
            record(provisional=True)
        except Exception as e:  # noqa: BLE001
            from .errors import warn

            warn(f"仮の {resolved_mod.RESOLVED_NAME} を書けませんでした: {e!r}")

    recorder = Recorder(
        report.root,
        overrides=experiment.overrides,
        data=report.data,
        on_change=record_provisional,
    )
    # 引数はまだ 1 つも観測していないが、構成（どの component のどの版か）はもう決まっている。
    record_provisional()

    status = STATUS_FINISHED
    exit_info: dict | None = None
    run.start_heartbeat()

    with _signals(), Capture(path / LOGS_DIR, enabled=capture):
        try:
            root = instantiate_root(recorder)
            root.execute(run)
        except Interrupted as e:
            status, exit_info = STATUS_KILLED, {"type": "signal", "message": str(e)}
        except KeyboardInterrupt:
            status, exit_info = STATUS_KILLED, {"type": "KeyboardInterrupt", "message": ""}
        except BaseException as e:  # noqa: BLE001 - 落ちても記録は書き切る
            status = STATUS_FAILED
            exit_info = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }
        finally:
            # 例外で終わっても、それまでの観測は残す。ここで仮の記録を確定版に置き換える。
            record(provisional=False)

    unused = resolved_mod.unused_children(recorder.root)
    mismatches: list[str] = []
    if recorded:
        from .replay import compare

        mismatches = compare(recorded, recorder.root)
    run.finish(status, exit_info)
    return Outcome(
        run=run, report=report, status=status, unused=unused, mismatches=mismatches
    )


def _create_run(
    project: Project,
    experiment: Experiment,
    runs_dir: Path | None,
    *,
    replay_of: dict | None = None,
) -> tuple[Run, Path]:
    root = project.runs_dir(runs_dir)
    parent = root / slugify(experiment.experiment)
    ensure_container(parent, KIND_EXPERIMENT, experiment.experiment)

    if experiment.group:
        # **グループは実行が作るのではなく、宣言が作る。** 同名を名乗った run が集まる。
        parent = parent / slugify(experiment.group)
        ensure_container(parent, KIND_GROUP, experiment.group)

    path, ulid, created = create_run_dir(parent, experiment.name)
    meta = new_meta(
        KIND_RUN,
        ulid,
        created,
        name=experiment.name,
        project_uid=project.uid,
        source=_relative_source(project, experiment),
        extra={"replay_of": replay_of} if replay_of else None,
    )
    run = Run(path, ulid, created, meta)
    run._write_meta()
    return run, path


def _relative_source(project: Project, experiment: Experiment) -> str | None:
    if experiment.source is None:
        return None
    try:
        return str(Path(experiment.source).resolve().relative_to(project.root))
    except ValueError:
        return str(experiment.source)


def _bake_components(root: Prepared, target: Path) -> None:
    """使った component の実体を run に焼き込む（layout.md §5.3）。

    **run はログルートに置かれ、プロジェクトの外に出る。** リポジトリが無くても、
    プロジェクトを消しても、run 単体でコードが読めて再実行できるようにする。

    コピーはハッシュ対象と同じフィルタを通す。凍結ディレクトリは import された時点で
    `__pycache__` が書き込まれるので、素朴にコピーすると run の `components/` と
    `resolved.yaml` の `hash` が 1:1 で対応しなくなる。
    """
    target.mkdir(parents=True, exist_ok=True)
    for prepared in _walk(root):
        dest = target / prepared.resolved.id
        if dest.exists():
            continue
        dest.mkdir(parents=True)
        copy_hashed(prepared.resolved.path, dest)


def _walk(prepared: Prepared):
    yield prepared
    for _, child in prepared.children():
        yield from _walk(child)


class _signals:
    """`SIGTERM` を例外に変えて、`killed` として記録できるようにする。"""

    def __init__(self) -> None:
        self._previous: dict = {}

    def __enter__(self):
        def handler(signum, frame):
            raise Interrupted(signal.Signals(signum).name)

        for sig in (signal.SIGTERM,):
            try:
                self._previous[sig] = signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # メインスレッド以外では張れない
        return self

    def __exit__(self, *exc):
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass

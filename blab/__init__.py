"""blab — ディレクトリに書くだけの実験管理。

    import blab

    with blab.init(experiment="mnist-cnn", params={"lr": 3e-4}) as run:
        run.log({"train/loss": 0.5}, step=0)
        run.log_summary({"test/acc": 0.93})

    # 実験の構成要素はレジストリから id で引く。何を何の引数で使ったかが記録される
    student = blab.load("resnet18_linear_classifier@v3", role="student", k=100)

    # 後から評価した値を、その run に書き足す
    blab.open("a1b2").log_summary({"probe/macro_f1": 0.71})

記録層は標準ライブラリのみで動く。UI (``blab ui``) は extra 依存。
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import layout
from .aggregate import aggregate_group
from .entry import entry
from .errors import BlabError, BlabUsageError
from .group import Group, create_group, open_group
from .registry import load
from .run import Run, create_run, open_run

__version__ = "0.1.0"
__all__ = [
    "init",
    "group",
    "open",
    "load",
    "entry",
    "Run",
    "Group",
    "aggregate_group",
    "BlabError",
    "BlabUsageError",
    "__version__",
]

_active_lock = threading.Lock()
_active_run: Run | None = None


def _register_run(run: Run) -> None:
    global _active_run
    with _active_lock:
        _active_run = run


def _release(run: Run) -> None:
    global _active_run
    with _active_lock:
        if _active_run is run:
            _active_run = None


def _check_no_active_run() -> None:
    with _active_lock:
        active = _active_run
    if active is not None and not active._finished:
        raise BlabUsageError(
            f"a run is already active ({active.path}); "
            "nested blab.init() is not allowed — call finish() first"
        )


def init(
    *,
    experiment: str,
    name: str | None = None,
    params: dict | None = None,
    tags=None,
    notes: str = "",
    dir: str | Path | None = None,
    flush_interval: float = 0.0,
) -> Run:
    """新しい run を作って返す。``with`` で使うと例外時に ``failed`` を記録する。

    Args:
        experiment: Experiment 名（ルート直下のディレクトリになる）。
        name: run 名。ディレクトリ名の末尾に slug として入る。
        params: ハイパラなど。任意のネスト JSON。キーに ``.`` は使えない。
        dir: ルートディレクトリ。省略時は ``BLAB_DIR`` → 上位探索 → ``./blab``。
        flush_interval: metrics の flush 間隔（秒）。既定 0 は毎回 flush。
    """
    _check_no_active_run()
    root = layout.resolve_root(dir)
    exp_dir = layout.ensure_experiment(root, experiment)
    run = create_run(
        exp_dir,
        root,
        name=name,
        params=params,
        tags=tags,
        notes=notes,
        flush_interval=flush_interval,
        on_finish=_release,
    )
    _register_run(run)
    return run


def open(  # noqa: A001 - 「実験を開き直す」の意味。builtins.open は builtins 経由で使う
    target: str | Path,
    *,
    dir: str | Path | None = None,
    resume: bool = False,
) -> Run | Group:
    """**既に記録済みの run / group を開き直す。**

    学習が終わったあとに評価した値を、その run 自身に書き足すための入口。
    後付けの記録がファイルを手で書く作業にならないようにする。

        blab.open("cell_cluster_regress/20260814-070000_c3d4_cv10").log_summary({...})
        blab.open("a1b2").log_artifact("output/probe/per_fold.csv")

    Args:
        target: ルートからの相対パス、実パス、ディレクトリ名、ULID、短 ID（4 文字）、
            または ``name``。走査で複数一致したら曖昧としてエラーにする。
        dir: ルートディレクトリ（既定の解決順は :func:`init` と同じ）。
        resume: run を **実行中に戻す**（status を ``running`` にし heartbeat を
            再開する）。中断した学習の再開用。既定 ``False`` では status も
            実行時間も書き換えず、記録の追加だけを行う。

    Returns:
        :class:`Run` または :class:`Group`。どちらも通常どおり ``log`` /
        ``log_summary`` / ``log_artifact`` が使える（group に ``log`` は無い）。
    """
    root = layout.resolve_root(dir, create=False)
    node_dir = layout.resolve_node(root, target)
    kind = layout.kind_of(node_dir)
    if kind == layout.KIND_GROUP:
        if resume:
            raise BlabUsageError("resume=True は run にしか使えません")
        return open_group(node_dir, root)
    if kind == layout.KIND_EXPERIMENT:
        raise BlabUsageError(f"{target!r} は experiment です。run か group を指定してください")
    run = open_run(node_dir, root, resume=resume, on_finish=_release)
    if resume:
        _check_no_active_run()
        _register_run(run)
    return run


def group(
    *,
    experiment: str,
    name: str | None = None,
    tags=None,
    notes: str = "",
    group_kind: str | None = None,
    dir: str | Path | None = None,
) -> Group:
    """新しい group を作って返す。``with`` を抜けるときに配下 run を集計する。

    Args:
        group_kind: ``"cv"`` など、UI の表示ヒント。
    """
    root = layout.resolve_root(dir)
    exp_dir = layout.ensure_experiment(root, experiment)
    return create_group(
        exp_dir, root, name=name, tags=tags, notes=notes, group_kind=group_kind
    )

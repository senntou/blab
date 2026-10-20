"""run の所属 group を変える（layout.md §6.3）。

group を宣言で決める以上、付け忘れと打ち間違いは必ず起きるので救済手段を用意する。
**黙って履歴を書き換えない**ため、移動の事実は `moved_from` として記録に残す。

UI からも同じ操作を呼ぶ（UI がディレクトリを直接触ることはない）。実装を 1 か所に保つ。
"""

from __future__ import annotations

from pathlib import Path

from .errors import BlabError
from .ids import isoformat, now, slugify
from .index import STATUS_RUNNING, Node
from .io import write_json_atomic
from .run import KIND_GROUP, META_NAME, ensure_container, read_meta


def move_run(runs_dir: Path, run_path: Path, group: str | None) -> Path:
    """`run_path` を `group` の下へ移す。`group` が `None` なら experiment 直下へ戻す。

    Returns:
        移動後のパス。
    """
    runs_dir = Path(runs_dir).resolve()
    source = Path(run_path).resolve()
    meta = read_meta(source)
    if meta is None:
        raise BlabError(f"{source} は run ではありません（{META_NAME} がない）")
    # 実行中に移すと、生きているプロセスが元のパスへ meta.json を書き戻してディレクトリを
    # 作り直し、同じ run が 2 か所に分かれる。heartbeat が途絶えた（stale）ものは移せる。
    if Node(source, meta).status == STATUS_RUNNING:
        raise BlabError(f"{source.name} は実行中です。終わるか止めてから移動してください")

    experiment = _experiment_of(runs_dir, source)
    if experiment is None:
        raise BlabError(f"{source} はログルート {runs_dir} の下にありません")

    target_parent = experiment / slugify(group) if group else experiment
    if group:
        ensure_container(target_parent, KIND_GROUP, group)
    if target_parent == source.parent:
        return source

    dest = _free_name(target_parent, source.name)

    before = _relative(runs_dir, source)
    source.rename(dest)
    meta["moved_from"] = {"path": before, "at": isoformat(now())}
    write_json_atomic(dest / META_NAME, meta, indent=2)
    return dest


def _free_name(parent: Path, name: str) -> Path:
    """`parent / name` が空いていなければ `{name}_dup2`, `_dup3`, ... と空きを探す。

    同じ名前のディレクトリが 2 つできてしまった run（実行中に移された run を、止めたときに
    書き戻されたもの）も、ゴミ箱へ移して消せるようにする。元の場所は `moved_from` に残る。
    """
    dest = parent / name
    n = 2
    while dest.exists():
        dest = parent / f"{name}_dup{n}"
        n += 1
    return dest


def _experiment_of(runs_dir: Path, path: Path) -> Path | None:
    """ログルート直下（= Experiment）まで遡る。"""
    current = path.parent
    while current != runs_dir:
        if current.parent == runs_dir:
            return current
        if current == current.parent:
            return None
        current = current.parent
    return None


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

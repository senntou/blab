"""`blab tag` — ラベルを貼り、git 管理下へ昇格させる（layout.md §2.2-2.3）。

ハッシュは正しいが、人間の記憶とは接続しない。「v3 で BN を外した」という記憶に繋ぐため、
**後からラベルを貼る。**

昇格の条件は**「人間が意味を認めたか」**である。自動凍結は 1 日に何十回も起きるので、
参照の有無を基準にするとゴミが参照によって守られてしまう。`blab tag` を叩いたという
人間の行為だけを条件にすれば、**リポジトリの中身が、人間が意味を認めたものと一致する。**
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .components import Meta, copy_hashed, find_frozen, hash_dir
from .errors import BlabError
from .project import Project
from .resolved import RESOLVED_NAME, iter_components, load
from .run import COMPONENTS_DIR


def tag(
    project: Project,
    id: str,
    label: str,
    *,
    note: str | None = None,
    from_run: Path | None = None,
) -> tuple[str, Path]:
    """ラベルを貼って `components/.frozen/<id>/<label>/` へ昇格させる。

    Returns:
        `(ハッシュ, 昇格先のパス)`。
    """
    if from_run is not None:
        hash, source = _from_run(Path(from_run), id)
    else:
        hash, source = _from_working_copy(project, id)

    meta = Meta.load(project, id)
    meta.add_label(label, hash, note=note)  # ラベルは不変。付け替えはここで弾かれる

    dest = project.labeled_dir / id / label
    if dest.exists():
        if hash_dir(dest) != hash:
            raise BlabError(f"{dest} が既にあり、中身が {hash} と一致しません")
    else:
        dest.mkdir(parents=True)
        copy_hashed(source, dest)

    meta.save(project)
    return hash, dest


def _from_working_copy(project: Project, id: str) -> tuple[str, Path]:
    source = project.components_dir / id
    if not source.is_dir():
        raise BlabError(f"component {id!r} がありません（{source}）")
    hash = hash_dir(source)
    # 凍結済みがあればそちらを使う（作業コピーと同一内容であることは保証済み）。
    return hash, find_frozen(project, id, hash) or source


def _from_run(run_path: Path, id: str) -> tuple[str, Path]:
    """過去 run に焼き込まれた実体からラベルを貼る。

    キャッシュを消していても、その版を使った run が残っていれば **run の中に実体が
    焼き込まれている**ので復元できる。実際にはこれが一番よくある使い方になる。
    """
    resolved = run_path / RESOLVED_NAME
    if not resolved.is_file():
        raise BlabError(f"{run_path} は run ではありません（{RESOLVED_NAME} がない）")
    doc = load(resolved)
    for _, component in iter_components(doc):
        if component.get("use") != id:
            continue
        source = run_path / COMPONENTS_DIR / id
        if not source.is_dir():
            raise BlabError(f"{run_path} に {id} の実体が焼き込まれていません")
        recorded = str(component.get("hash"))
        actual = hash_dir(source)
        if actual != recorded:
            raise BlabError(
                f"{run_path} の {id} は記録されたハッシュと中身が食い違っています"
                f"（記録 {recorded} / 実体 {actual}）"
            )
        return recorded, source
    used = ", ".join(sorted({str(c.get("use")) for _, c in iter_components(doc)}))
    raise BlabError(f"{run_path} は component {id!r} を使っていません（使ったのは: {used}）")

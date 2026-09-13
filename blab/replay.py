"""過去 run の再実行（layout.md §7）。

    blab run runs/cifar100/20260913-063012_a1b2_distill/

run ディレクトリを渡すと、その中の `resolved.yaml` と `components/` **だけ**を見て
実行する。レジストリもプロジェクトも `blab.json` も参照しない。したがって、

- component を編集したあとでも、過去 run はその当時のコードで再実行される
- プロジェクトを消したあとでも、run のディレクトリさえあれば再実行できる
- 共同研究者から run をもらえば、そのまま再実行できる

**実行時引数は再現しない。** `resolved.yaml` には `.build()` に渡された値が記録されて
いるが、再実行時はコードが再びそれを計算する。記録と食い違ったら警告を出す（データが
変わった、ライブラリの挙動が変わった、などの検出になる）。

> 記録を再生するのではなく、コードを動かして一致を確かめる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .components import Ref, Resolved, hash_dir
from .errors import BlabError
from .loader import load_entry
from .preflight import Prepared, Report
from .resolved import RESOLVED_NAME, load
from .run import COMPONENTS_DIR
from .spec import Experiment, Node, ROOT_PATH


def is_run_dir(path: Path) -> bool:
    return (Path(path) / RESOLVED_NAME).is_file()


@dataclass
class Replay:
    experiment: Experiment
    report: Report
    source_id: str
    source_path: Path
    #: `run.build()` に渡された記録。実行時の値と突き合わせて食い違いを見る。
    recorded: dict[str, list[dict]] = field(default_factory=dict)


def prepare(run_path: Path) -> Replay:
    """run ディレクトリから、そのまま実行できる状態を組み立てる。"""
    run_path = Path(run_path).resolve()
    doc = load(run_path / RESOLVED_NAME)
    if doc.get("provisional"):
        raise BlabError(
            f"{run_path / RESOLVED_NAME} は実行途中の仮の記録です（run が実行中か、"
            "記録を書き切る前に強制終了されました）。引数が揃っていないので再実行できません"
        )
    components = run_path / COMPONENTS_DIR
    if not components.is_dir():
        raise BlabError(
            f"{run_path} に {COMPONENTS_DIR}/ がありません。"
            "この run は自己完結していないので再実行できません"
        )

    report = Report()
    recorded: dict[str, list[dict]] = {}
    root_doc = doc.get("run")
    if not isinstance(root_doc, dict):
        raise BlabError(f"{run_path / RESOLVED_NAME} に run がありません")

    report.root = _prepare(root_doc, ROOT_PATH, components, report, recorded, is_root=True)

    experiment = Experiment(
        experiment=str(doc.get("experiment") or "replay"),
        run=report.root.node if report.root else Node(ref=Ref(id="?"), path=ROOT_PATH),
        name=doc.get("name"),
        group=doc.get("group"),
        source=None,
        overrides=doc.get("overrides") or {},
    )
    # run の ULID は meta.json が持つ（resolved.yaml は構成の記録であって run の身元ではない）。
    from .run import read_meta

    meta = read_meta(run_path) or {}
    return Replay(
        experiment=experiment,
        report=report,
        source_id=str(meta.get("id") or ""),
        source_path=run_path,
        recorded=recorded,
    )


def _prepare(
    node_doc: dict,
    path: str,
    components: Path,
    report: Report,
    recorded: dict,
    *,
    is_root: bool = False,
) -> Prepared | None:
    id = str(node_doc.get("use") or "")
    hash = str(node_doc.get("hash") or "")
    source = components / id
    if not source.is_dir():
        report.error(f"{COMPONENTS_DIR}/{id} が run の中にありません", path)
        return None

    actual = hash_dir(source)
    if hash and actual != hash:
        # 焼き込まれた実体が記録と食い違う。黙って動かさない。
        report.error(
            f"component {id!r} の実体が記録されたハッシュと違います"
            f"（記録 {hash} / 実体 {actual}）。run のディレクトリが改変されています",
            path,
        )
        return None

    try:
        entry_name, entry_obj, _ = load_entry(id, actual, source)
    except BaseException as e:  # noqa: BLE001
        report.error(f"component {id!r} を import できません: {type(e).__name__}: {e}", path)
        return None

    resolved = Resolved(
        ref=Ref(id=id, text=id),
        id=id,
        project_uid=str(node_doc.get("project_uid") or ""),
        project_name="",
        hash=actual,
        path=source,
        label=node_doc.get("label"),
        tags=[],
    )

    # YAML 由来の引数だけを復元する。`runtime` / `default` はコードが再び決める。
    if is_root:
        args_source = node_doc.get("args") or {}
        args_from = node_doc.get("args_from") or {}
    else:
        builds = node_doc.get("builds") or []
        recorded[path] = [b for b in builds if isinstance(b, dict)]
        first = builds[0] if builds else {}
        args_source = first.get("args") or {}
        args_from = first.get("args_from") or {}

    args = {
        name: value
        for name, value in args_source.items()
        if args_from.get(name) in ("yaml", "override") and not _is_marker(value)
    }

    node = Node(ref=Ref(id=id, text=id), args=dict(args), path=path)
    prepared = Prepared(
        node=node, resolved=resolved, entry_name=entry_name, entry_obj=entry_obj
    )

    children = node_doc.get("children") or {}
    for key, child_doc in children.items() if isinstance(children, dict) else []:
        if not isinstance(child_doc, dict):
            continue
        child_path = f"{path}.{key}"
        child = _prepare(child_doc, child_path, components, report, recorded)
        if child is not None:
            node.args[key] = child.node
            prepared.args[key] = child

    for name, value in args.items():
        prepared.args.setdefault(name, value)

    from .preflight import _check_signature

    _check_signature(prepared, report, is_root=is_root)
    return prepared


def _is_marker(value: Any) -> bool:
    """`{"$ref": ...}` などの記録用マーカーは引数として復元しない。"""
    return isinstance(value, dict) and any(
        isinstance(k, str) and k.startswith("$") for k in value
    )


def compare(recorded: dict[str, list[dict]], observed_root) -> list[str]:
    """記録と、今回の実行で観測された `.build()` 引数を突き合わせる。

    **記録を再生するのではなく、コードを動かして一致を確かめる**という立場。
    """
    from .builder import Observed

    messages: list[str] = []

    def walk(node: Observed) -> None:
        before = recorded.get(node.path)
        if before is not None:
            now = [{"args": b.args, "args_from": b.args_from} for b in node.builds]
            if len(before) != len(now):
                messages.append(
                    f"{node.path}: build の回数が違います（記録 {len(before)} / 今回 {len(now)}）"
                )
            else:
                for i, (old, new) in enumerate(zip(before, now)):
                    for key in sorted(set(old.get("args", {})) | set(new.get("args", {}))):
                        a = old.get("args", {}).get(key, "（無し）")
                        b = new.get("args", {}).get(key, "（無し）")
                        if a != b:
                            messages.append(
                                f"{node.path}#{i}: 引数 {key} が違います（記録 {a!r} / 今回 {b!r}）"
                            )
        for child in node.children.values():
            walk(child)

    walk(observed_root)
    return messages

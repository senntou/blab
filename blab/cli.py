"""``blab`` コマンド。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .components import Meta, hash_dir, list_ids, short_hash
from .errors import BlabError
from .preflight import ERROR, preflight
from .project import GlobalIndex, Project, init as project_init, write_gitignore
from .spec import load_experiment


def _fail(message: str) -> None:
    sys.exit(f"[blab] {message}")


def _project(args) -> Project:
    return Project.load(getattr(args, "dir", None))


# ------------------------------------------------------------------ init / link


def cmd_init(args) -> None:
    root = Path(args.path or Path.cwd()).resolve()
    project, created = project_init(root, name=args.name, runs_dir=args.runs_dir)
    if created:
        print(f"[blab] プロジェクトを作りました: {project.root}")
    else:
        print(f"[blab] 既にプロジェクトです（索引に再登録しました）: {project.root}")
        if write_gitignore(project.root):
            print("[blab] .gitignore に blab の行を足しました")
    print(f"  project     {project.name}")
    print(f"  project_uid {project.uid}")
    print(f"  components  {project.components_dir}")
    print(f"  experiments {project.experiments_dir}")
    print(f"  runs        {project.runs_dir()}")


def cmd_link(args) -> None:
    root = Path(args.path).expanduser().resolve()
    try:
        project = Project.at(root)
    except BlabError as e:
        _fail(str(e))
        return
    GlobalIndex.load().register(project.uid, project.name, project.root)
    print(f"[blab] グローバル索引に登録しました: {project.name} -> {project.root}")


def cmd_projects(args) -> None:
    index = GlobalIndex.load()
    if not index.projects:
        print("(登録されたプロジェクトはありません)")
        return
    if args.json:
        print(json.dumps(index.projects, ensure_ascii=False, indent=2))
        return
    width = max(len(e.get("name", "")) for e in index.projects.values())
    for uid, entry in sorted(index.projects.items(), key=lambda kv: kv[1].get("name", "")):
        mark = "" if Path(entry.get("path", "")).joinpath("blab.json").is_file() else "  (見つかりません)"
        print(f"{entry.get('name', ''):<{width}}  {uid}  {entry.get('path', '')}{mark}")


# ------------------------------------------------------------------- component

TEMPLATE_MAIN = '''"""{id}。"""

import blab


@blab.entry
class {cls}:
    def __init__(self):
        ...
'''

TEMPLATE_README = """# {id}

何をするコンポーネントかを書く。

## 引数

| 名前 | 意味 |
| --- | --- |
"""


def _class_name(id: str) -> str:
    return "".join(part.capitalize() or "_" for part in id.split("_"))


def cmd_new(args) -> None:
    project = _project(args)
    from .components import validate_id

    validate_id(args.id)
    path = project.components_dir / args.id
    if path.exists():
        _fail(f"{path} は既にあります")
    path.mkdir(parents=True)
    (path / "main.py").write_text(
        TEMPLATE_MAIN.format(id=args.id, cls=_class_name(args.id)), encoding="utf-8"
    )
    (path / "README.ja.md").write_text(TEMPLATE_README.format(id=args.id), encoding="utf-8")
    print(f"[blab] component を作りました: {path}")
    print("  main.py       @blab.entry を 1 つだけ置く")
    print("  README.ja.md  何をするものかを書く")


def cmd_components(args) -> None:
    project = _project(args)
    ids = list_ids(project)
    if args.tags:
        wanted = set(args.tags)
        ids = [i for i in ids if wanted & set(Meta.load(project, i).tags)]
    if not ids:
        print("(component がありません)")
        return

    rows = []
    for id in ids:
        meta = Meta.load(project, id)
        path = project.components_dir / id
        try:
            hash = hash_dir(path)
        except BlabError:
            hash = None
        rows.append(
            {
                "id": id,
                "hash": hash,
                "label": meta.label_of(hash) if hash else None,
                "tags": meta.tags,
                "labels": [e.get("name") for e in meta.labels],
            }
        )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    width = max(len(r["id"]) for r in rows)
    for row in rows:
        hash = short_hash(row["hash"]) if row["hash"] else "-"
        label = f" @{row['label']}" if row["label"] else ""
        tags = " ".join(f"#{t}" for t in row["tags"])
        print(f"{row['id']:<{width}}  {hash}{label}  {tags}")


# ----------------------------------------------------------------------- check


def cmd_check(args) -> None:
    project = _project(args)
    try:
        experiment = load_experiment(
            Path(args.yaml), overrides=args.set, name=args.name, group=args.group
        )
    except BlabError as e:
        _fail(str(e))
        return

    report = preflight(project, experiment)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "experiment": experiment.experiment,
                    "name": experiment.name,
                    "group": experiment.group,
                    "overrides": experiment.overrides,
                    "findings": [
                        {"level": f.level, "path": f.path, "message": f.message}
                        for f in report.findings
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(0 if report.ok else 1)

    for finding in report.findings:
        print(finding.render(), file=sys.stderr if finding.level == ERROR else sys.stdout)

    if report.root is not None:
        print(f"\n{experiment.experiment}" + (f" / {experiment.name}" if experiment.name else ""))
        _print_tree(report.root, indent=2)

    if not report.ok:
        sys.exit(f"\n[blab] 事前検証に失敗しました（エラー {len(report.errors)} 件）")
    extra = f"（警告 {len(report.warnings)} 件）" if report.warnings else ""
    print(f"\n[blab] 事前検証を通りました{extra}")


def _print_tree(prepared, indent: int, label: str = "run") -> None:
    resolved = prepared.resolved
    version = f"@{resolved.label}" if resolved.label else f"@{short_hash(resolved.hash)}"
    froze = "  (今回凍結)" if resolved.froze_now else ""
    print(f"{' ' * indent}{label}: {resolved.id}{version}{froze}")

    scalars = {
        k: v
        for k, v in prepared.args.items()
        if not _contains_prepared(v)
    }
    if scalars:
        rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(scalars.items()))
        print(f"{' ' * (indent + 2)}{rendered}")
    if prepared.defaults:
        rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(prepared.defaults.items()))
        print(f"{' ' * (indent + 2)}既定値: {rendered}")
    if prepared.runtime_required:
        print(
            f"{' ' * (indent + 2)}実行時に渡される想定: "
            f"{', '.join(prepared.runtime_required)}"
        )
    for path, child in prepared.children():
        _print_tree(child, indent + 2, label=path.split(".", 1)[-1])


def _contains_prepared(value) -> bool:
    from .preflight import Prepared

    if isinstance(value, Prepared):
        return True
    if isinstance(value, dict):
        return any(_contains_prepared(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_prepared(v) for v in value)
    return False


# ------------------------------------------------------------------------- run


def cmd_run(args) -> None:
    from .replay import is_run_dir
    from .runner import execute
    from .run import STATUS_FINISHED

    target = Path(args.yaml)
    if is_run_dir(target):
        return _run_replay(args, target)

    project = _project(args)
    experiment = load_experiment(
        target, overrides=args.set, name=args.name, group=args.group
    )

    report = preflight(project, experiment)
    for finding in report.warnings:
        print(finding.render())
    if not report.ok:
        for finding in report.errors:
            print(finding.render(), file=sys.stderr)
        sys.exit(f"[blab] 事前検証に失敗しました（エラー {len(report.errors)} 件）")

    outcome = execute(
        project,
        experiment,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
        capture=not args.no_capture,
        report=report,
    )

    print(f"\n[blab] {outcome.status}  {outcome.run.path}")
    if outcome.run.summary:
        for key, value in sorted(outcome.run.summary.items()):
            print(f"  {key} = {value}")
    _report_outcome(outcome)


def _run_replay(args, run_path: Path) -> None:
    """過去 run の再実行。レジストリもプロジェクトも参照しない（layout.md §7）。"""
    from .replay import prepare
    from .runner import execute

    if args.set:
        sys.exit("[blab] 再実行に --set は使えません（記録された構成をそのまま動かします）")

    replay = prepare(run_path)
    if not replay.report.ok:
        for finding in replay.report.errors:
            print(finding.render(), file=sys.stderr)
        sys.exit(f"[blab] 再実行できません（エラー {len(replay.report.errors)} 件）")

    # run の中だけで完結させるため、ログの置き場所は元 run の隣を既定にする。
    project = _project(args)
    outcome = execute(
        project,
        replay.experiment,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
        capture=not args.no_capture,
        report=replay.report,
        replay_of={"id": replay.source_id, "path": str(replay.source_path)},
        recorded=replay.recorded,
    )
    print(f"[blab] {replay.source_path} を再実行しました")
    _report_outcome(outcome)


def _report_outcome(outcome) -> None:
    from .run import STATUS_FINISHED

    print(f"\n[blab] {outcome.status}  {outcome.run.path}")
    if outcome.run.summary:
        for key, value in sorted(outcome.run.summary.items()):
            print(f"  {key} = {value}")
    for path in outcome.unused:
        # 宣言されたのに使われなかったことを黙って消さない。
        print(f"[警告] {path} は YAML で宣言されましたが、一度も build されませんでした")
    for message in outcome.mismatches:
        # 記録を再生するのではなく、コードを動かして一致を確かめる。
        print(f"[警告] 記録と食い違います — {message}")
    if outcome.status != STATUS_FINISHED:
        exit_info = outcome.run._meta.get("exit") or {}
        message = exit_info.get("message") or exit_info.get("type") or ""
        sys.exit(f"[blab] {outcome.status}: {message}")


# ------------------------------------------------------------------ ls / show


def _runs_root(args, project: Project) -> Path:
    return project.runs_dir(Path(args.runs_dir) if getattr(args, "runs_dir", None) else None)


def cmd_ls(args) -> None:
    from .index import scan, walk
    from .run import KIND_GROUP, KIND_RUN

    project = _project(args)
    nodes = scan(_runs_root(args, project))
    if args.experiment:
        nodes = [n for n in nodes if args.experiment in (n.name, n.path.name)]
    if not nodes:
        print("(run がありません)")
        return

    for experiment in nodes:
        print(f"\n{experiment.name}")
        for node in walk(experiment.children):
            depth = len(node.path.relative_to(experiment.path).parts)
            indent = "  " * depth
            if node.kind == KIND_RUN:
                summary = " ".join(
                    f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in sorted(node.summary.items())
                )
                print(f"{indent}{node.path.name:<40} {node.status:<9} {summary}")
            elif node.kind == KIND_GROUP:
                stats = node.aggregate()
                rendered = " ".join(f"{k}={s.render()}" for k, s in stats.items())
                n = len(node.leaf_runs())
                print(f"{indent}{node.path.name}/  ({n} runs)  {rendered}")


def cmd_show(args) -> None:
    target = Path(args.target)
    if target.is_dir() and (target / "meta.json").is_file():
        return _show_node(args, target)
    return _show_component(args)


def _show_node(args, path: Path) -> None:
    from .index import _read
    from .resolved import RESOLVED_NAME
    from .run import KIND_RUN

    node = _read(path)
    if node is None:
        _fail(f"{path} はノードではありません")
        return
    print(f"{node.kind}  {node.path}")
    for key in ("id", "name", "status", "created_at", "duration_sec", "source"):
        if node.meta.get(key) is not None:
            print(f"  {key:<13} {node.meta[key]}")
    if node.meta.get("replay_of"):
        print(f"  replay_of     {node.meta['replay_of'].get('path')}")
    if node.meta.get("moved_from"):
        print(f"  moved_from    {node.meta['moved_from'].get('path')}")

    exit_info = node.meta.get("exit")
    if exit_info:
        print(f"\n  終了: {exit_info.get('type')}: {exit_info.get('message')}")

    if node.kind == KIND_RUN:
        summary = node.summary
        if summary:
            print("\n  summary")
            for key, value in sorted(summary.items()):
                print(f"    {key} = {value}")
        resolved_path = node.path / RESOLVED_NAME
        if resolved_path.is_file():
            print(f"\n  構成 ({RESOLVED_NAME})")
            print(_indent(resolved_path.read_text(encoding="utf-8"), 4))
    else:
        stats = node.aggregate()
        if stats:
            print(f"\n  集計（葉の run {len(node.leaf_runs())} 本から導出）")
            for key, stat in stats.items():
                print(f"    {key} = {stat.render()}")


def _show_component(args) -> None:
    from .components import Meta, hash_dir, parse_ref, short_hash
    from .index import runs_using

    project = _project(args)
    ref = parse_ref(args.target)
    path = project.components_dir / ref.id
    meta = Meta.load(project, ref.id)

    print(f"component {ref.id}")
    if path.is_dir():
        current = hash_dir(path)
        label = meta.label_of(current)
        print(f"  作業コピー    {short_hash(current)}" + (f" @{label}" if label else ""))
    if meta.tags:
        print(f"  タグ          {' '.join('#' + t for t in meta.tags)}")
    if meta.labels:
        print("  ラベル")
        for entry in meta.labels:
            note = f"  {entry.get('note')}" if entry.get("note") else ""
            print(
                f"    {entry.get('name'):<10} {short_hash(str(entry.get('hash')))}"
                f"  {entry.get('tagged_at', '')}{note}"
            )

    readme = next((p for p in (path / "README.ja.md", path / "README.md") if p.is_file()), None)
    if readme:
        print(f"\n{_indent(readme.read_text(encoding='utf-8'), 2)}")

    using = runs_using(_runs_root(args, project))
    hits = [
        (hash, runs) for hash, runs in using.items()
        if any(name == ref.id for name, _ in runs)
    ]
    if hits:
        print("  この component を使った run")
        for hash, runs in sorted(hits):
            label = meta.label_of(hash)
            head = f"{short_hash(hash)}" + (f" @{label}" if label else "")
            print(f"    {head}  {len(runs)} 本")
            for _, node in runs[:5]:
                print(f"      {node.path.name}")


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.rstrip().splitlines())


# --------------------------------------------------------------------- tag/diff


def cmd_tag(args) -> None:
    from .components import short_hash
    from .tag import tag

    project = _project(args)
    hash, dest = tag(
        project,
        args.id,
        args.label,
        note=args.note,
        from_run=Path(args.from_run) if args.from_run else None,
    )
    print(f"[blab] {args.id}@{args.label} = {short_hash(hash)}")
    print(f"  昇格先 {dest}（git 管理下）")


def cmd_diff(args) -> None:
    import difflib

    from .components import Resolver, iter_hashed_files, parse_ref

    project = _project(args)
    resolver = Resolver(project)
    left = resolver.resolve(parse_ref(args.left))
    right = resolver.resolve(parse_ref(args.right))
    if left.hash == right.hash:
        print(f"[blab] 同じ版です（{left.hash}）")
        return

    files = sorted(
        {rel for rel, _ in iter_hashed_files(left.path)}
        | {rel for rel, _ in iter_hashed_files(right.path)}
    )
    for rel in files:
        a = left.path / rel
        b = right.path / rel
        a_lines = a.read_text(encoding="utf-8").splitlines(keepends=True) if a.is_file() else []
        b_lines = b.read_text(encoding="utf-8").splitlines(keepends=True) if b.is_file() else []
        if a_lines == b_lines:
            continue
        sys.stdout.writelines(
            difflib.unified_diff(
                a_lines, b_lines, fromfile=f"{args.left}/{rel}", tofile=f"{args.right}/{rel}"
            )
        )


# ------------------------------------------------------------------- mv / rm


def cmd_mv(args) -> None:
    from .mv import move_run

    project = _project(args)
    root = _runs_root(args, project)
    source = Path(args.run).resolve()
    dest = move_run(root, source, args.group)
    print(f"[blab] {source.name} -> {dest.relative_to(root)}")


def cmd_ui(args) -> None:
    project = _project(args)
    try:
        from .server import serve
    except ImportError:
        _fail("UI の依存（fastapi / uvicorn）が入っていません。blab を `[ui]` extras 付きでインストールしてください")
        return
    serve(
        project,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
        host=args.host,
        port=args.port,
        open_browser=args.open,
    )


def cmd_rm(args) -> None:
    import shutil

    from .run import read_meta

    target = Path(args.target).resolve()
    meta = read_meta(target)
    if meta is None:
        _fail(f"{target} はノードではありません（meta.json がない）")
        return
    if not args.yes:
        answer = input(f"{target} を削除します。よろしいですか [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            print("[blab] やめました")
            return
    shutil.rmtree(target)
    print(f"[blab] 削除しました: {target}")


# ------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blab", description="実験管理ツール blab")
    parser.add_argument("--version", action="version", version=f"blab {__version__}")
    parser.add_argument(
        "--dir", help="プロジェクトを探し始めるディレクトリ（既定: カレント）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="プロジェクトを作る")
    p.add_argument("path", nargs="?", help="プロジェクトルート（既定: カレント）")
    p.add_argument("--name", help="プロジェクト名（既定: ディレクトリ名）")
    p.add_argument("--runs-dir", help="ログルート（blab.json に書く。チーム共通の既定値）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("link", help="別のプロジェクトをグローバル索引に登録する")
    p.add_argument("path")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("projects", help="グローバル索引の一覧")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("new", help="component の雛形を作る")
    p.add_argument("id")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("components", help="component の一覧")
    p.add_argument("--tags", nargs="*", default=None, help="このタグを持つものだけ")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_components)

    p = sub.add_parser("run", help="事前検証 → 凍結 → 実行")
    p.add_argument("yaml", help="実験 YAML")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="YAML の値を 1 か所上書きする（例: --set run.lr=1e-4）")
    p.add_argument("--name", help="run 名")
    p.add_argument("--group", help="所属 group")
    p.add_argument("--runs-dir", help="ログの置き場所")
    p.add_argument("--no-capture", action="store_true",
                   help="stdout/stderr を捕捉しない（デバッガや進捗バーを使うとき）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="事前検証だけして実行しない")
    p.add_argument("yaml", help="実験 YAML")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="YAML の値を 1 か所上書きする（例: --set run.lr=1e-4）")
    p.add_argument("--name", help="run 名")
    p.add_argument("--group", help="所属 group")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("ls", help="run / group の一覧（group の集計は読むときに導出する）")
    p.add_argument("experiment", nargs="?")
    p.add_argument("--runs-dir")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("show", help="run / group / component の詳細")
    p.add_argument("target", help="run のパス、または component id[@label]")
    p.add_argument("--runs-dir")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("tag", help="ラベルを貼り、git 管理下へ昇格させる")
    p.add_argument("id")
    p.add_argument("label")
    p.add_argument("--note")
    p.add_argument("--from-run", help="過去 run に焼き込まれた実体から貼る")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("diff", help="component の版間 diff")
    p.add_argument("left", help="例: resnet18@v1")
    p.add_argument("right", help="例: resnet18@v3")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("mv", help="run の所属 group を変える")
    p.add_argument("run")
    p.add_argument("--group", help="移動先の group（省略で experiment 直下へ戻す）")
    p.add_argument("--runs-dir")
    p.set_defaults(func=cmd_mv)

    p = sub.add_parser("rm", help="run / group を削除する")
    p.add_argument("target")
    p.add_argument("-y", "--yes", action="store_true", help="確認しない")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("ui", help="ローカル UI（既定で 127.0.0.1 のみ listen）")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--open", action="store_true", help="ブラウザを開く")
    p.add_argument("--runs-dir")
    p.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BlabError as e:
        _fail(str(e))


if __name__ == "__main__":
    main()

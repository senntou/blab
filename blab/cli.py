"""``blab`` コマンド。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__, layout, registry
from .aggregate import reindex
from .errors import BlabError
from .index import Index
from .io import read_json


def _root(args) -> Path:
    return layout.resolve_root(args.dir, create=False)


def _require_root(args) -> Path:
    root = _root(args)
    if not layout.is_root(root):
        sys.exit(f"[blab] not an blab root: {root} (no {layout.ROOT_MARKER})")
    return root


def _ensure_root(args) -> Path:
    """書き込む側（new / register）用。無ければルートを作る。"""
    return layout.resolve_root(args.dir, create=True)


def _fail(e: Exception) -> None:
    sys.exit(f"[blab] {e}")


def _fmt_duration(sec) -> str:
    if not isinstance(sec, (int, float)):
        return "-"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cmd_ls(args) -> None:
    root = _require_root(args)
    index = Index(root)
    index.refresh()
    rows = index.rows(experiment=args.experiment, kind=args.kind)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("(no runs)")
        return
    summary_keys: list[str] = []
    for row in rows:
        for k in row["summary"]:
            if k not in summary_keys:
                summary_keys.append(k)
    summary_keys = summary_keys[: args.max_cols]

    header = ["path", "kind", "status", "dur"] + summary_keys
    table = [header]
    for row in rows:
        status = row["status"] or "-"
        if row.get("stale"):
            status += "(stale)"
        line = [row["path"], row["kind"], status, _fmt_duration(row.get("duration_sec"))]
        for k in summary_keys:
            v = row["summary"].get(k)
            line.append(f"{v:.4g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else ("" if v is None else str(v)))
        table.append(line)
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    for i, line in enumerate(table):
        print("  ".join(c.ljust(w) for c, w in zip(line, widths)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))


def cmd_show(args) -> None:
    root = _require_root(args)
    # show は component と run/group の両方を受ける（§7）。component id は
    # レジストリにディレクトリがあるかで判定し、無ければノードのパスとして扱う。
    target = args.path.strip("/")
    id, _, _version = target.partition("@")
    if "/" not in target and (layout.components_dir(root) / id).is_dir():
        _show_component(root, target, args.json)
        return
    index = Index(root)
    index.refresh()
    try:
        detail = index.detail(args.path.strip("/"))
    except KeyError:
        sys.exit(f"[blab] not found: {args.path}")
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
        return
    meta = detail["meta"]
    print(f"{detail['path']}  [{detail['kind']}]")
    for key in ("id", "name", "status", "created_at", "finished_at", "duration_sec", "cmd"):
        if meta.get(key) is not None:
            print(f"  {key:<12} {meta[key]}")
    if meta.get("git"):
        print(f"  {'git':<12} {meta['git']}")
    # group では「自分で記録した値」と「配下 run の平均」が同じ summary 欄に並ぶ。
    # 取り違えると意味が変わるので、平均のほうに ± と n を付けて区別する。
    stats = detail.get("summary_stats") or {}
    for title, data in (("params", detail["params"]), ("summary", detail["summary"])):
        if data:
            print(f"  {title}:")
            for k, v in sorted(data.items()):
                st = stats.get(k)
                suffix = f"  (mean ± {st['std']:.4g}, n={st['count']})" if st else ""
                print(f"    {k} = {v}{suffix}")
    if detail.get("metric_keys"):
        print(f"  metrics: {', '.join(detail['metric_keys'])} ({detail['n_metric_rows']} rows)")
    if detail.get("artifacts"):
        print("  artifacts:")
        for a in detail["artifacts"]:
            print(f"    {a['name']}  ({a['size']} bytes, {a['type']})")


def cmd_new(args) -> None:
    root = _ensure_root(args)
    try:
        cdir = registry.create_component(root, args.id, tags=_split_tags(args.tags))
    except BlabError as e:
        _fail(e)
    rel = layout.relpath(root, cdir)
    print(f"[blab] created {rel}/")
    print(f"  1. {rel}/{layout.CURRENT_DIR}/{args.id}.py を書く（入口に @blab.entry）")
    print(f"  2. {rel}/{layout.COMPONENT_README} に「これは何か」を書く")
    print(f"  3. blab register {args.id} v1 --note '初版'")


def cmd_register(args) -> None:
    root = _ensure_root(args)
    try:
        record = registry.register(
            root, args.id, args.version, note=args.note, tags=_split_tags(args.tags)
        )
    except BlabError as e:
        _fail(e)
    print(f"[blab] registered {args.id}@{record['id']}  {record['hash'][:19]}…")
    if record["note"]:
        print(f"  note: {record['note']}")


def cmd_components(args) -> None:
    root = _require_root(args)
    want = set(_split_tags(args.tags) or [])
    rows = []
    for id in layout.iter_component_ids(root):
        try:
            info = registry.describe(root, id)
        except BlabError as e:
            rows.append({"id": id, "error": str(e)})
            continue
        if want and not want.issubset(set(info["tags"])):
            continue
        rows.append(info)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("[blab] no components")
        return
    width = max(len(r["id"]) for r in rows)
    for r in rows:
        if r.get("error"):
            print(f"{r['id']:<{width}}  ! {r['error']}")
            continue
        latest = r["latest"] or "-"
        # current が latest と違うことは、次の load が止まることを意味する。先に見せる。
        if r["current_matches_latest"] is False:
            latest += " (current が未登録)"
        tags = "[" + (",".join(r["tags"]) or "-") + "]"
        cells = [
            f"{r['id']:<{width}}",
            f"{latest:<24}",
            f"{tags:<24}",
            f"{len(r['versions'])} version(s)",
        ]
        if r["readme_stale"]:
            cells.append(f"{layout.COMPONENT_README} が latest より古い")
        print("  ".join(cells))


def cmd_diff(args) -> None:
    root = _require_root(args)
    id_a, ver_a = _split_spec(args.a)
    id_b, ver_b = _split_spec(args.b)
    if id_b is None:
        id_b = id_a
    if id_a != id_b:
        sys.exit(f"[blab] diff は同一 component の version 間だけです（{id_a} と {id_b}）")
    if ver_a is None or ver_b is None:
        sys.exit("[blab] diff には version が必要です（例: blab diff foo@v1 foo@v3）")
    try:
        print(registry.diff_versions(root, id_a, ver_a, ver_b), end="")
    except BlabError as e:
        _fail(e)


def _split_tags(value) -> list[str] | None:
    if value is None:
        return None
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _split_spec(spec: str) -> tuple[str, str | None]:
    id, _, version = str(spec).partition("@")
    return id, (version or None)


def _show_component(root: Path, spec: str, as_json: bool) -> None:
    id, version = _split_spec(spec)
    try:
        info = registry.describe(root, id)
        text, path = registry.source_of(root, id, version)
    except BlabError as e:
        _fail(e)
    readme, _ = registry.readme_of(root, id)
    if as_json:
        print(json.dumps({**info, "source": text, "readme": readme}, ensure_ascii=False, indent=2))
        return
    print(f"{id}  [component]")
    print(f"  {'entry':<12} {info['entry'] or '-'}")
    print(f"  {'tags':<12} {','.join(info['tags']) or '-'}")
    print(f"  {'latest':<12} {info['latest'] or '-'}")
    if info["current_matches_latest"] is False:
        print(f"  {'current':<12} latest と異なる（未登録の編集がある）")
    print("  versions:")
    for v in info["versions"]:
        mark = " <- latest" if v["id"] == info["latest"] else ""
        print(f"    {v['id']:<16} {v.get('registered_at', '-')}  {v.get('note', '')}{mark}")
    if info["readme_stale"]:
        print(f"  ! {layout.COMPONENT_README} が latest の登録より古い")
    if readme:
        print("  README:")
        for line in readme.splitlines():
            print(f"    {line}")
    print(f"  source ({layout.relpath(root, path)}):")
    for line in text.splitlines():
        print(f"    {line}")


def cmd_reindex(args) -> None:
    root = _require_root(args)
    updated = reindex(root)
    for d in updated:
        print(f"[blab] reindexed {layout.relpath(root, d)}")
    print(f"[blab] {len(updated)} group(s) reindexed")


def cmd_rm(args) -> None:
    root = _require_root(args)
    try:
        target = layout.safe_join(root, args.path)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[blab] {e}")
    if target == root or not target.is_dir():
        sys.exit(f"[blab] not found: {args.path}")
    meta = read_json(target / layout.META_NAME, {}) or {}
    kind = meta.get("kind", "?")
    if not args.yes:
        answer = input(f"delete {kind} {args.path}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("[blab] aborted")
            return
    shutil.rmtree(target)
    print(f"[blab] removed {args.path}")


def cmd_ui(args) -> None:
    root = _require_root(args)
    try:
        from .server import serve
    except ImportError:
        sys.exit("[blab] UI requires extras: pip install 'blab[ui]'")
    serve(root, host=args.host, port=args.port, open_browser=args.open)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="blab", description="blab: 実験の記録と閲覧")
    parser.add_argument("--version", action="version", version=f"blab {__version__}")
    parser.add_argument("--dir", default=None, help="ルートディレクトリ（既定: BLAB_DIR → 上位探索 → ./.blab）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ui = sub.add_parser("ui", help="ローカル UI を起動")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8420)
    p_ui.add_argument("--open", action="store_true", help="ブラウザを開く")
    p_ui.set_defaults(func=cmd_ui)

    p_ls = sub.add_parser("ls", help="run / group を一覧表示")
    p_ls.add_argument("experiment", nargs="?", default=None)
    p_ls.add_argument("--kind", choices=["run", "group"], default=None)
    p_ls.add_argument("--json", action="store_true")
    p_ls.add_argument("--max-cols", type=int, default=6, help="表示する summary 列の上限")
    p_ls.set_defaults(func=cmd_ls)

    p_show = sub.add_parser("show", help="component か run / group の詳細")
    p_show.add_argument("path", help="<component id>[@version] または run/group のパス")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_new = sub.add_parser("new", help="component の雛形を作る")
    p_new.add_argument("id")
    p_new.add_argument("--tags", default=None, help="カンマ区切り")
    p_new.set_defaults(func=cmd_new)

    p_reg = sub.add_parser("register", help="current/ を凍結して登録")
    p_reg.add_argument("id")
    p_reg.add_argument("version")
    p_reg.add_argument("--note", default=None, help="この version で何を変えたか（1 行）")
    p_reg.add_argument("--tags", default=None, help="カンマ区切り。指定時は置き換え")
    p_reg.set_defaults(func=cmd_register)

    p_comp = sub.add_parser("components", help="component 一覧")
    p_comp.add_argument("--tags", default=None, help="カンマ区切り。すべて持つものだけ")
    p_comp.add_argument("--json", action="store_true")
    p_comp.set_defaults(func=cmd_components)

    p_diff = sub.add_parser("diff", help="component の version 間 diff")
    p_diff.add_argument("a", help="<id>@<version>")
    p_diff.add_argument("b", help="<id>@<version>（id 省略可）")
    p_diff.set_defaults(func=cmd_diff)

    p_re = sub.add_parser("reindex", help="group の集計を再生成")
    p_re.set_defaults(func=cmd_reindex)

    p_rm = sub.add_parser("rm", help="run / group を削除")
    p_rm.add_argument("path")
    p_rm.add_argument("-y", "--yes", action="store_true", help="確認をスキップ")
    p_rm.set_defaults(func=cmd_rm)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()

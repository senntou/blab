"""ノードに添付するドキュメント（layout.md §5.7）。

**Experiment / Group / Run ディレクトリの直下にある `*.md` がドキュメント。**
「この実験は何を確かめるためのものか」「結果をどう読んだか」を、人間や LLM が
Markdown で書いて置いておく場所で、UI はそれをそのまま描画する（数式も描く）。

置き方は 3 通りあり、どれで置いても同じ扱いになる。

    experiments/*.yaml の docs:        run の作成時にコピーされる（run 用）
    blab doc add <node> <file>         あとから任意のノードに添付する
    run.log_doc("report.md", text)     component が実行中に書く

ファイルは直接置いてもよい（UI は規約だけを見て読む）。画像も同じ場所に置けば、
Markdown から相対パスで参照できる。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import BlabError

DOC_SUFFIX = ".md"
#: ドキュメントから相対パスで参照する画像。`blab doc add` / YAML の `docs:` で一緒に置ける。
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
ATTACHABLE_SUFFIXES = {DOC_SUFFIX} | ASSET_SUFFIXES

#: 一覧で先頭に出す名前（大文字小文字は区別しない）。
README_NAME = "README.md"


def list_docs(path: Path) -> list[dict]:
    """ノード直下の `*.md`。`README.md` が先頭、残りは名前順。"""
    path = Path(path)
    try:
        entries = list(path.iterdir())
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.name.startswith(".") or entry.suffix.lower() != DOC_SUFFIX:
            continue
        try:
            if not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        out.append({"name": entry.name, "size": stat.st_size, "mtime": stat.st_mtime})
    out.sort(key=lambda d: (d["name"].lower() != README_NAME.lower(), d["name"].lower()))
    return out


def check_name(name: str) -> str:
    """添付先のファイル名として使えるか。ノード直下に置くので、パス区切りは許さない。"""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise BlabError(f"ドキュメントの名前にパス区切りは使えません: {name!r}")
    if name.startswith("."):
        raise BlabError(f"ドットで始まる名前は使えません: {name!r}")
    if Path(name).suffix.lower() not in ATTACHABLE_SUFFIXES:
        allowed = " ".join(sorted(ATTACHABLE_SUFFIXES))
        raise BlabError(f"添付できるのは {allowed} だけです: {name!r}")
    return name


def attach(
    node: Path,
    source: Path | None = None,
    *,
    name: str | None = None,
    text: str | None = None,
    force: bool = False,
) -> Path:
    """`source`（または `text`）を `node` 直下に置く。既にある名前は `force` が無ければ拒む。"""
    node = Path(node)
    if (source is None) == (text is None):
        raise BlabError("source と text のどちらか一方を渡してください")
    if source is not None:
        source = Path(source)
        if not source.is_file():
            raise BlabError(f"ファイルがありません: {source}")
    target_name = check_name(name or (source.name if source is not None else README_NAME))
    target = node / target_name
    if target.exists() and not force:
        raise BlabError(f"{target} は既にあります（上書きするなら -f）")
    if source is not None:
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    else:
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    return target


def remove(node: Path, name: str) -> Path:
    target = Path(node) / check_name(name)
    if not target.is_file():
        raise BlabError(f"ありません: {target}")
    target.unlink()
    return target


# ------------------------------------------------------------- 実験 YAML の docs:


def resolve_sources(docs: list[str], source: Path | None) -> list[Path]:
    """YAML の `docs:` を実パスにする。相対パスは **YAML ファイルのあるディレクトリ** から。"""
    base = Path(source).resolve().parent if source else Path.cwd()
    return [(base / Path(entry).expanduser()).resolve() for entry in docs]


def check_sources(docs: list[str], source: Path | None) -> list[str]:
    """事前検証用。問題を文字列で返す（空なら OK）。"""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for entry, path in zip(docs, resolve_sources(docs, source)):
        if not path.is_file():
            problems.append(f"docs: {entry} がありません（{path}）")
            continue
        try:
            check_name(path.name)
        except BlabError as e:
            problems.append(f"docs: {e}")
            continue
        if path.name in seen:
            problems.append(
                f"docs: {entry} と {seen[path.name]} は run の中で同じ名前 {path.name} になります"
            )
        seen[path.name] = entry
    return problems


def copy_sources(docs: list[str], source: Path | None, node: Path) -> list[Path]:
    """`docs:` のファイルを run 直下にコピーする（run 作成時）。"""
    return [attach(node, path, force=True) for path in resolve_sources(docs, source)]

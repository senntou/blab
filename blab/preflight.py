"""事前検証（layout.md §3.2）。

**`blab run` は、学習を 1 秒でも始める前に、YAML を上から下まで歩いて検査する。**

検査には import が要り、import 元は凍結版でなければならないので、**凍結が検査に先立つ**。

    YAML 読み込み → 参照解決 → ハッシュ → 凍結 → 凍結版から import → 検査 → 実行

構文エラーを含む component も凍結されるが無害である。凍結は内容アドレスなので中身と
名前が食い違うことはなく、ラベルを貼らない限り `.blab/` に留まって git には載らない。

v1 と違い、**v2 の検査は遠慮なく止めてよい**。構成が実行前に全部分かっているので、
「必須の引数が無い」と「まだ load されていないだけ」の区別が付かない、という v1 の
問題が存在しない。
"""

from __future__ import annotations

import difflib
import inspect
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .components import Resolved, Resolver
from .errors import BlabError
from .loader import load_entry
from .project import Project
from .spec import DataRef, Experiment, Node, ROOT_PATH, _iter_values

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    level: str
    message: str
    path: str = ""

    def render(self) -> str:
        mark = "エラー" if self.level == ERROR else "警告"
        where = f"{self.path}: " if self.path else ""
        return f"[{mark}] {where}{self.message}"


@dataclass
class Prepared:
    """検証を通った component 1 つ分。Phase 4 以降の組み立てはこれを入力にする。"""

    node: Node
    resolved: Resolved
    entry_name: str
    entry_obj: Any
    #: `node.args` と同じ形。`Node` の位置に `Prepared` が入っている。
    args: dict[str, Any] = field(default_factory=dict)
    #: YAML にもデフォルトにも無い引数。`.build()` で渡される想定。
    runtime_required: list[str] = field(default_factory=list)
    #: YAML に無く、シグネチャのデフォルトが使われる引数（値も記録する）。
    defaults: dict[str, Any] = field(default_factory=dict)
    accepts_kwargs: bool = False

    @property
    def path(self) -> str:
        return self.node.path

    def children(self):
        """直下の子 `Prepared` を `(パス, Prepared)` で列挙する。"""
        for value in _iter_values(self.args):
            if isinstance(value, Prepared):
                yield value.path, value


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    root: Prepared | None = None
    #: 論理名 → 実パス（解決できたものだけ）。
    data: dict[str, Path] = field(default_factory=dict)

    def error(self, message: str, path: str = "") -> None:
        self.findings.append(Finding(ERROR, message, path))

    def warn(self, message: str, path: str = "") -> None:
        self.findings.append(Finding(WARNING, message, path))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_failed(self) -> None:
        if self.ok:
            return
        lines = "\n".join("  " + f.render() for f in self.findings)
        raise BlabError(f"事前検証に失敗しました（{len(self.errors)} 件）:\n{lines}")


def preflight(
    project: Project, experiment: Experiment, *, resolver: Resolver | None = None
) -> Report:
    """実験の構成を全部検査する。**実行はしない。**"""
    report = Report()
    resolver = resolver or Resolver(project)

    for message in experiment.warnings:
        report.warn(message)

    report.root = _prepare(project, experiment.run, resolver, report, is_root=True)
    _check_data(project, experiment.run, report)
    _check_require_tags(project, report)
    return report


# ------------------------------------------------------------------ component


def _prepare(
    project: Project,
    node: Node,
    resolver: Resolver,
    report: Report,
    *,
    is_root: bool = False,
) -> Prepared | None:
    # 1. 参照された component が存在するか（別プロジェクト参照を含む）
    try:
        resolved = resolver.resolve(node.ref)
    except BlabError as e:
        report.error(str(e), node.path)
        return None

    # 2-3. 凍結版から import できるか / @blab.entry がちょうど 1 つか
    try:
        entry_name, entry_obj, _ = load_entry(resolved.id, resolved.hash, resolved.path)
    except BlabError as e:
        report.error(str(e), node.path)
        return None
    except BaseException as e:  # noqa: BLE001 - component の import は何でも投げうる
        detail = _component_traceback(e, resolved)
        report.error(
            f"component {str(node.ref)!r} を import できません: {type(e).__name__}: {e}"
            + (f"\n{detail}" if detail else ""),
            node.path,
        )
        return None

    prepared = Prepared(
        node=node, resolved=resolved, entry_name=entry_name, entry_obj=entry_obj
    )

    # 4. root の component が execute を持つか
    if is_root and not _has_execute(entry_obj):
        report.error(
            f"root の component {str(node.ref)!r}（entry: {entry_name}）に execute がありません。"
            "root は blab が実体化して execute(run) を呼ぶので、"
            "`def execute(self, run):` が必要です",
            node.path,
        )

    # 子を先に用意する（引数の検査より前に、子の側のエラーを全部出すため）。
    prepared.args = _prepare_args(project, node.args, resolver, report)

    # 5-6. 引数名の照合と、実行時に渡される想定の引数
    _check_signature(prepared, report, is_root=is_root)
    return prepared


def _component_traceback(exc: BaseException, resolved: Resolved) -> str:
    """component の中のフレームだけを、作業コピーのパスで表示する。

    凍結ディレクトリのパス（`.blab/frozen/<id>/sha256-…/main.py`）をそのまま出しても
    人間は編集できない。直すべきファイルは作業コピーなので、そちらの表記に直す。
    blab 自身のフレームは省く（ユーザのコードの話ではないため）。
    """
    frozen = str(Path(resolved.path).resolve())
    lines: list[str] = []
    for frame in traceback.TracebackException.from_exception(exc).stack:
        filename = str(Path(frame.filename).resolve()) if frame.filename else ""
        if not filename.startswith(frozen):
            continue
        rel = Path(filename).relative_to(frozen).as_posix()
        lines.append(f"    {resolved.id}/{rel}:{frame.lineno} in {frame.name}")
        if frame.line:
            lines.append(f"      {frame.line.strip()}")

    # SyntaxError はスタックに乗らない（import 先のファイルが特定できる）。
    if isinstance(exc, SyntaxError) and exc.filename:
        filename = str(Path(exc.filename).resolve())
        if filename.startswith(frozen):
            rel = Path(filename).relative_to(frozen).as_posix()
            lines.append(f"    {resolved.id}/{rel}:{exc.lineno}")
            if exc.text:
                lines.append(f"      {exc.text.strip()}")
    return "\n".join(lines)


def _prepare_args(
    project: Project, value: Any, resolver: Resolver, report: Report
) -> Any:
    if isinstance(value, Node):
        return _prepare(project, value, resolver, report)
    if isinstance(value, dict):
        return {k: _prepare_args(project, v, resolver, report) for k, v in value.items()}
    if isinstance(value, list):
        return [_prepare_args(project, v, resolver, report) for v in value]
    return value


def _has_execute(entry_obj: Any) -> bool:
    if inspect.isclass(entry_obj):
        return callable(getattr(entry_obj, "execute", None))
    # ファクトリ関数は戻り値を静的に見られない。実行時に確かめるしかないので通す。
    return True


def _signature(entry_obj: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(entry_obj)
    except (TypeError, ValueError):
        return None


def _check_signature(prepared: Prepared, report: Report, *, is_root: bool) -> None:
    node = prepared.node
    signature = _signature(prepared.entry_obj)
    if signature is None:
        report.warn(
            f"component {str(node.ref)!r} の entry {prepared.entry_name} の引数を読めません。"
            "引数名の照合を飛ばします",
            node.path,
        )
        prepared.accepts_kwargs = True
        return

    params = signature.parameters
    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    prepared.accepts_kwargs = accepts_kwargs

    positional_only = [
        name for name, p in params.items() if p.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    if positional_only:
        report.error(
            f"component {str(node.ref)!r} の entry {prepared.entry_name} に位置専用引数が"
            f"あります（{', '.join(positional_only)}）。blab は引数を必ずキーワードで渡すので、"
            "位置専用（`/` の手前）にはできません",
            node.path,
        )

    nameable = {
        name
        for name, p in params.items()
        if p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }

    # 5. YAML の引数名が entry の実際の引数と合っているか
    if not accepts_kwargs:
        for name in node.args:
            if name in nameable:
                continue
            hint = difflib.get_close_matches(name, sorted(nameable), n=1)
            suggestion = f"（もしかして {hint[0]!r}?）" if hint else ""
            known = ", ".join(sorted(nameable)) or "（引数なし）"
            report.error(
                f"component {str(node.ref)!r} の entry {prepared.entry_name} に"
                f"引数 {name!r} はありません{suggestion}。受け取れるのは: {known}",
                node.path,
            )

    # 6. YAML にもデフォルトにも無い必須引数
    for name in nameable:
        if name in node.args:
            continue
        param = params[name]
        if param.default is not inspect.Parameter.empty:
            prepared.defaults[name] = param.default
            continue
        if is_root:
            # root は誰も `.build()` しないので、実行時に渡される経路が無い。
            report.error(
                f"root の component {str(node.ref)!r} に必須の引数 {name!r} が"
                "渡されていません。root は blab が直接実体化するので、"
                "引数は全部 YAML に書く必要があります",
                node.path,
            )
        else:
            prepared.runtime_required.append(name)

    prepared.runtime_required.sort()


# ----------------------------------------------------------------- 外部ファイル


def _check_data(project: Project, root: Node, report: Report) -> None:
    """7. `{data: NAME}` の論理名が解決でき、実パスが存在するか。"""
    seen: dict[str, list[str]] = {}
    for ref in _iter_all_data_refs(root):
        seen.setdefault(ref.name, []).append(ref.path)
    if not seen:
        return

    table = project.data_paths
    for name, where in sorted(seen.items()):
        path = where[0]
        if name not in table:
            report.error(
                f"外部ファイルの論理名 {name!r} が blab.local.json の data にありません。"
                f'{project.root / "blab.local.json"} に '
                f'"data": {{"{name}": "/実際のパス"}} を書いてください',
                path,
            )
            continue
        resolved = Path(table[name]).expanduser()
        if not resolved.is_absolute():
            resolved = (project.root / resolved).resolve()
        if not resolved.exists():
            report.error(
                f"外部ファイル {name!r} の実パス {resolved} がこのマシンにありません",
                path,
            )
            continue
        report.data[name] = resolved


def _iter_all_data_refs(node: Node):
    """木全体の `DataRef` を列挙する（`Node` の中にも降りる）。"""
    stack = [node]
    while stack:
        current = stack.pop()
        for value in _iter_values(current.args):
            if isinstance(value, Node):
                stack.append(value)
            elif isinstance(value, DataRef):
                yield value


# ------------------------------------------------------------------ ポリシー


def _check_require_tags(project: Project, report: Report) -> None:
    """8. `blab.json` の `require_tags` を満たすか。

    判定は「root が持つ」ではなく**「木の中に、要求されたタグを持つ component が
    1 つ以上ある」**。root は trainer なので、`require_tags: ["model"]` の目的
    （model の配線忘れを検出する）を満たすにはこちらでなければならない。
    """
    required = project.require_tags
    if not required or report.root is None:
        return
    present: set[str] = set()
    for prepared in _walk(report.root):
        present.update(prepared.resolved.tags)
    for tag in required:
        if tag not in present:
            report.error(
                f"blab.json の require_tags が {tag!r} を要求していますが、"
                f"この構成の中にそのタグを持つ component がありません"
                f"（タグは components/.meta/<id>.json に書きます）",
                ROOT_PATH,
            )


def _walk(prepared: Prepared):
    yield prepared
    for _, child in prepared.children():
        yield from _walk(child)

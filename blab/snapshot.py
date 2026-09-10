"""entrypoint の snapshot と first-party import の追跡（design.md §4.2）。

blab は ``__main__.__file__`` から自分を呼んだファイルを知っているので、ユーザは
何も宣言しなくてよい。component と違って entrypoint には自己完結を強制できないので、
**辿れる範囲を snapshot し、辿れなかったものは記録して UI に出す。**

「沈黙による嘘をしない」（§0）の適用として、この module は次を守る。

- 実体を残せたもの → ``code/`` にコピーし、ハッシュを記録する
- 実体を残せなかったもの → ``unresolved_imports`` / ``skipped`` に理由を残す
- インストール済みパッケージ → 実体は残さないが、名前と version を残す
"""

from __future__ import annotations

import ast
import functools
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

from . import layout
from .errors import warn
from .io import read_json

CODE_DIR = "code"
FIRST_PARTY_DIR = "_first_party"
COMPONENTS_SNAPSHOT_DIR = "_components"

#: 1 run にコピーする first-party ファイル数の上限。超えた分は理由を残して捨てる。
#: （editable install した自分のパッケージが project root 配下にある場合、
#: 数千ファイルを run ごとに複製しかねないため）
MAX_FIRST_PARTY_FILES = 200
#: 1 ファイルの上限。生成データや巨大な定数表を run に複製しない。
MAX_SOURCE_BYTES = 1 << 20


# ------------------------------------------------------------ project root


def entrypoint_file() -> Path | None:
    """``__main__`` のファイル。REPL / notebook では ``None``。"""
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    return resolved if resolved.is_file() else None


@functools.lru_cache(maxsize=32)
def git_toplevel(start: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return Path(top).resolve() if top else None


def project_root(root: Path, entry: Path | None) -> Path | None:
    """first-party の判定基準となる project root（§4.2）。

    1. ``blab.json`` の ``project_root``（明示指定。相対パスは blab.json からの相対）
    2. entrypoint のファイルから辿った git root
    3. entrypoint のあるディレクトリ

    **``BLAB_DIR`` の親は基準にしない。** blab のデータディレクトリはコードと無関係な
    場所に置けるので、その親を基準にするとコードとの対応が取れない。
    """
    marker = read_json(Path(root) / layout.ROOT_MARKER, {}) or {}
    declared = marker.get("project_root") if isinstance(marker, dict) else None
    if declared:
        path = Path(str(declared)).expanduser()
        if not path.is_absolute():
            path = Path(root) / path
        try:
            return path.resolve()
        except OSError:
            return None
    if entry is None:
        return None
    return git_toplevel(entry.parent) or entry.parent


# ------------------------------------------------------------ first-party の判定


@functools.lru_cache(maxsize=1)
def _installed_prefixes() -> tuple[Path, ...]:
    """site-packages と標準ライブラリの場所。ここにあるものは first-party ではない。"""
    keys = ("purelib", "platlib", "stdlib", "platstdlib", "scripts", "data")
    out = []
    for key in keys:
        try:
            path = sysconfig.get_paths().get(key)
        except Exception:  # noqa: BLE001 - 環境依存の保険
            path = None
        if path:
            out.append(Path(path).resolve())
    return tuple(out)


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def is_installed(path: Path, prefixes: tuple[Path, ...] | None = None) -> bool:
    return any(_under(path, p) for p in (prefixes or _installed_prefixes()))


def trace_first_party(proj: Path, blab_root: Path, entry: Path | None = None) -> dict[str, Path]:
    """``sys.modules`` から first-party なモジュールを拾う。

    first-party = インストールされていないモジュール（site-packages / 標準ライブラリ /
    blab 自身 / blab ルート配下を除外）のうち、project root 配下にあるもの。
    """
    prefixes = _installed_prefixes()
    blab_pkg = Path(__file__).resolve().parent
    found: dict[str, Path] = {}
    for name, module in list(sys.modules.items()):
        # __main__ は entrypoint 本体なので別に扱う（``__mp_main__`` は
        # multiprocessing が張る同じファイルの別名なので、二重に残さない）。
        # blab 自身は実験の構成要素ではない。
        if name in ("__main__", "__mp_main__", "blab") or name.startswith("blab."):
            continue
        path = getattr(module, "__file__", None)
        if not path or not str(path).endswith(".py"):
            continue
        try:
            resolved = Path(path).resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if _under(resolved, blab_pkg) or _under(resolved, Path(blab_root).resolve()):
            continue
        if is_installed(resolved, prefixes):
            continue
        if not _under(resolved, proj):
            continue
        if entry is not None and resolved == entry:
            continue
        found[name] = resolved
    return found


@functools.lru_cache(maxsize=1)
def _distribution_map() -> dict:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - 環境依存
        return {}
    try:
        return metadata.packages_distributions()
    except Exception:  # noqa: BLE001 - 環境依存の保険
        return {}


def installed_packages() -> dict[str, str]:
    """import 済みモジュールに対応するインストール済み配布物の名前と version。

    実体を snapshot しないもの（§3.3 で「自分の pip パッケージへ」と切り出した
    共有ヘルパを含む）について、**何を使ったかだけは残す**ための記録。
    環境全体ではなく、実際に import されたものに限る。
    """
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - 環境依存
        return {}
    mapping = _distribution_map()
    tops = {name.split(".")[0] for name in sys.modules if not name.startswith("_")}
    out: dict[str, str] = {}
    for top in sorted(tops):
        for dist in mapping.get(top, ()):
            if dist in out:
                continue
            try:
                out[dist] = metadata.version(dist)
            except Exception:  # noqa: BLE001
                out[dist] = "?"
    return out


# ------------------------------------------------------------ import の静的走査


def _guarded(node: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` や ``try: import ... except ImportError:`` の中か。"""
    return getattr(node, "_blab_guarded", False)


def _mark_guarded(body: list[ast.stmt]) -> None:
    for stmt in body:
        for child in ast.walk(stmt):
            child._blab_guarded = True  # type: ignore[attr-defined]


def imported_names(source: str) -> tuple[set[str], list[str]]:
    """ソースの import 文から ``(トップレベル名の集合, 動的 import の記述)`` を返す。

    ``if TYPE_CHECKING:`` と ``try/except ImportError`` の中の import は、実行されない
    ことが正常なので数えない（未記録の警告を無意味に鳴らさないため）。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), ["ソースを構文解析できませんでした"]

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "TYPE_CHECKING" in test:
                _mark_guarded(node.body)
        elif isinstance(node, ast.Try):
            if any(
                isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError")
                for h in node.handlers
            ) or any(
                isinstance(h.type, ast.Tuple)
                and any(
                    isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                    for e in h.type.elts
                )
                for h in node.handlers
            ):
                _mark_guarded(node.body)

    names: set[str] = set()
    dynamic: list[str] = []
    for node in ast.walk(tree):
        if _guarded(node):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相対 import。entrypoint 自身のパッケージなので追わない
                continue
            if node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            target = None
            if isinstance(func, ast.Name) and func.id == "__import__":
                target = "__import__"
            elif isinstance(func, ast.Attribute) and func.attr == "import_module":
                target = "importlib.import_module"
            if target is None:
                continue
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(str(arg.value).split(".")[0])
            else:
                dynamic.append(f"{target}(<動的>) at line {node.lineno}")
    return names, dynamic


# ------------------------------------------------------------ snapshot 本体


def _copy_source(src: Path, dest: Path) -> tuple[bool, str | None]:
    try:
        size = src.stat().st_size
    except OSError as e:
        return False, f"{src.name}: 読めません（{e}）"
    if size > MAX_SOURCE_BYTES:
        return False, f"{src.name}: {size} bytes（上限 {MAX_SOURCE_BYTES} を超えるので複製しない）"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
    except OSError as e:
        return False, f"{src.name}: コピーできません（{e}）"
    return True, None


def _entry_dest(code_dir: Path, entry: Path) -> Path:
    """同名の entrypoint が内容違いで来た場合に上書きしない置き場を決める。"""
    dest = code_dir / entry.name
    if not dest.exists() or layout.hash_source(dest) == layout.hash_source(entry):
        return dest
    digest = layout.hash_source(entry)[-8:]
    return code_dir / f"{entry.stem}.{digest}{entry.suffix}"


def snapshot_entrypoint(run_dir: Path, root: Path, *, argv: str) -> dict:
    """entrypoint と first-party import を ``code/`` に snapshot し、記録を返す。

    ``components.json`` の ``entrypoints`` に積む 1 件を組んで返す。
    """
    code_dir = Path(run_dir) / CODE_DIR
    entry = entrypoint_file()
    proj = project_root(root, entry)
    record: dict[str, Any] = {
        "name": None,
        "path": None,
        "hash": None,
        "recorded_at": layout.isoformat(layout.now()),
        "argv": argv,
        "project_root": str(proj) if proj else None,
        "first_party": [],
        "unresolved_imports": [],
        "skipped": [],
    }

    if entry is None:
        # notebook / REPL / -c。捕まえられないので、捕まえられなかったと書く。
        record["name"] = "<interactive>"
        record["unresolved_imports"] = [
            "entrypoint のファイルを特定できません（__main__ に __file__ が無い）"
        ]
        return record

    dest = _entry_dest(code_dir, entry)
    ok, problem = _copy_source(entry, dest)
    record["name"] = entry.name
    record["hash"] = layout.hash_source(entry)
    if ok:
        record["path"] = f"{CODE_DIR}/{dest.name}"
    else:
        record["skipped"].append(problem or f"{entry.name}: コピーできません")

    sources = {entry.name: entry.read_text(encoding="utf-8", errors="replace")}

    if proj is not None:
        modules = trace_first_party(proj, root, entry)
        for name in sorted(modules)[:MAX_FIRST_PARTY_FILES]:
            src = modules[name]
            rel = Path(*name.split("."))
            target = code_dir / FIRST_PARTY_DIR / (
                rel / "__init__.py" if src.name == "__init__.py" else rel.with_suffix(".py")
            )
            ok, problem = _copy_source(src, target)
            if ok:
                record["first_party"].append(
                    {
                        "module": name,
                        "path": f"{CODE_DIR}/{FIRST_PARTY_DIR}/"
                        + str(target.relative_to(code_dir / FIRST_PARTY_DIR)),
                        "hash": layout.hash_source(src),
                        "origin": str(src),
                    }
                )
                sources[name] = src.read_text(encoding="utf-8", errors="replace")
            else:
                record["skipped"].append(problem or f"{name}: コピーできません")
        if len(modules) > MAX_FIRST_PARTY_FILES:
            record["skipped"].append(
                f"first-party モジュールが {len(modules)} 個あり、"
                f"{MAX_FIRST_PARTY_FILES} 個までしか snapshot していません"
            )
    else:
        record["unresolved_imports"].append(
            "project root を決められないので first-party import を追跡していません"
        )

    record["unresolved_imports"].extend(_unresolved(sources))
    return record


def _accounted_for(name: str) -> bool:
    """その名前の所在が説明できるか。

    - この実行で import された → ``sys.modules`` にある
    - 標準ライブラリ → 実体を残す必要がない
    - インストール済み配布物 → 実体は残さないが、何であるかは分かっている

    どれでもない名前だけが「辿れなかった」ものになる。判定に import は使わない
    （記録のための走査で副作用を起こさない）。
    """
    if name in sys.modules:
        return True
    if name in getattr(sys, "stdlib_module_names", ()):  # 3.10+
        return True
    return name in _distribution_map()


def _unresolved(sources: dict[str, str]) -> list[str]:
    """snapshot した各ソースの import のうち、実体を辿れなかったものを列挙する。

    所在が説明できない名前は、動的に差し替えられたか、この環境に無いもの。
    **推測せず、辿れなかったという事実だけを残す。**
    """
    out: list[str] = []
    for where, text in sources.items():
        names, dynamic = imported_names(text)
        for name in sorted(names):
            if _accounted_for(name):
                continue
            out.append(f"{where}: import {name}（この実行では解決できていない）")
        for note in dynamic:
            out.append(f"{where}: {note}")
    return out


def snapshot_dirty_component(run_dir: Path, id: str, source: Path) -> str | None:
    """dirty 実行した component のソースを run に残す（§3.4）。

    version 参照が使えないぶん、内容そのものを残して嘘をつかない。
    """
    dest = Path(run_dir) / CODE_DIR / COMPONENTS_SNAPSHOT_DIR / f"{id}.py"
    ok, problem = _copy_source(Path(source), dest)
    if not ok:
        warn(f"dirty component {id} を snapshot できません: {problem}")
        return None
    return f"{CODE_DIR}/{COMPONENTS_SNAPSHOT_DIR}/{dest.name}"

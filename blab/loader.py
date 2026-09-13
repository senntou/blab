"""凍結ディレクトリを合成パッケージとして import する（layout.md §2.4）。

    blab._c.<id>__<hash の先頭 8 桁>

`submodule_search_locations` を持つパッケージとして `sys.modules` に載せるので、
`__init__.py` が無くても `from .blocks import BasicBlock` が解決する。ハッシュが名前に
入っているため、同一 id の別バージョンを 1 プロセス内で同時にロードしても衝突しない。

**import 元は必ず凍結版であって作業コピーではない。** したがって「記録されたハッシュと
実際に動いたバイト列が違う」は原理的に起こらない。
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .entry import resolve_entry
from .errors import BlabError

#: 合成パッケージの名前空間。実体のあるパッケージではない。
NAMESPACE = "blab._c"

ENTRY_MODULE = "main"


class _MissingComponentFinder(importlib.abc.MetaPathFinder):
    """`blab._c.*` が `sys.modules` に無いときに、理由の分かる例外を出すだけの finder。

    合成モジュール名は**子プロセスから解決できない**。`spawn` で起動した
    `DataLoader(num_workers>0)` が component のオブジェクトを unpickle しようとすると、
    子プロセスには `sys.modules` が引き継がれていないのでここに来る。

    解決そのものは実装しない（Linux 既定の `fork` では `sys.modules` を継承するので
    起きない）。ただし**何が起きたのか分からないまま延々ハマる**のが一番悪いので、
    メッセージだけは出す。
    """

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname != NAMESPACE and not fullname.startswith(NAMESPACE + "."):
            return None
        raise ImportError(
            f"blab の component モジュール {fullname!r} をこのプロセスで解決できません。\n"
            "  これはたいてい、start method が 'spawn' の子プロセス（例: "
            "DataLoader(num_workers>0, multiprocessing_context='spawn')）が、\n"
            "  component の中で定義されたクラスを unpickle しようとしたときに起きます。"
            "component は `blab run` のプロセスの sys.modules にしか載っていません。\n"
            "  対処: start method を 'fork' にする（Linux の既定）、num_workers=0 にする、"
            "または子プロセスに渡すオブジェクトを component の外（標準ライブラリや "
            "pip パッケージ）で定義された型にしてください。"
        )


_finder = _MissingComponentFinder()


def _ensure_namespace() -> None:
    """`blab._c` を、実体を持たないパッケージとして `sys.modules` に置く。"""
    if NAMESPACE not in sys.modules:
        spec = importlib.machinery.ModuleSpec(NAMESPACE, loader=None, is_package=True)
        spec.submodule_search_locations = []
        module = importlib.util.module_from_spec(spec)
        sys.modules[NAMESPACE] = module
        parent = sys.modules.get("blab")
        if parent is not None:
            setattr(parent, "_c", module)
    if not any(isinstance(f, _MissingComponentFinder) for f in sys.meta_path):
        sys.meta_path.append(_finder)


def package_name(id: str, hash: str) -> str:
    return f"{NAMESPACE}.{id}__{hash.split(':', 1)[-1][:8]}"


def load_module(id: str, hash: str, path: Path) -> ModuleType:
    """凍結ディレクトリの `main.py` を import して返す。

    同一ハッシュの再ロードは `sys.modules` のキャッシュを使う。
    """
    _ensure_namespace()
    pkg = package_name(id, hash)
    main = f"{pkg}.{ENTRY_MODULE}"
    if main in sys.modules:
        return sys.modules[main]

    path = Path(path)
    entry_file = path / f"{ENTRY_MODULE}.py"
    if not entry_file.is_file():
        raise BlabError(f"component {id!r} に {ENTRY_MODULE}.py がありません（{path}）")

    if pkg not in sys.modules:
        # `__init__.py` を持たないディレクトリを、探索パス付きのパッケージとして登録する。
        # これで main.py の相対 import がこのディレクトリの中だけで解決する。
        spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
        spec.submodule_search_locations = [str(path)]
        module = importlib.util.module_from_spec(spec)
        sys.modules[pkg] = module
        setattr(sys.modules[NAMESPACE], pkg.rsplit(".", 1)[1], module)

    try:
        return importlib.import_module(main)
    except BaseException:
        # 失敗した import を中途半端に残さない（次の試行が壊れたモジュールを拾う）。
        sys.modules.pop(main, None)
        sys.modules.pop(pkg, None)
        raise


def load_entry(id: str, hash: str, path: Path):
    """`main.py` を import し、`@blab.entry` がちょうど 1 つであることを検査して返す。

    Returns:
        `(entry の名前, entry オブジェクト, モジュール)`。
    """
    module = load_module(id, hash, path)
    name, obj = resolve_entry(module, id)
    return name, obj, module

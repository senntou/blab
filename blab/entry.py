"""``@blab.entry`` — component の入口を 1 つだけ明示するマーカー。

ファイル名からの命名規約ではなく明示マーカーを採る。曖昧さがなく、補助クラスを
同じファイルに置けて、事前検証で「入口がちょうど 1 つあるか」を検査できる。

    import blab

    @blab.entry
    class Resnet18LinearClassifier(nn.Module):
        ...
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from .errors import BlabError

#: マーカーの属性名。クラス / 関数に直接立てる。
ENTRY_FLAG = "__blab_entry__"


def entry(obj: Any) -> Any:
    """component の入口として印を付ける。オブジェクトはそのまま返す。"""
    try:
        setattr(obj, ENTRY_FLAG, True)
    except (AttributeError, TypeError) as e:
        raise BlabError(
            f"@blab.entry を {obj!r} に付けられません（属性を持てないオブジェクト）: {e}"
        ) from e
    return obj


def is_entry(obj: Any) -> bool:
    return getattr(obj, ENTRY_FLAG, False) is True


def find_entries(module: ModuleType) -> list[tuple[str, Any]]:
    """モジュール内の entry を ``(名前, オブジェクト)`` で列挙する。

    **そのモジュールで定義されたもの**だけを見る（``__module__`` で判定）。
    他所から import してきた印付きオブジェクトを入口と誤認しないため。
    """
    found = []
    for name, obj in vars(module).items():
        if name.startswith("__") or not is_entry(obj):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        found.append((name, obj))
    return found


def resolve_entry(module: ModuleType, id: str) -> tuple[str, Any]:
    """entry がちょうど 1 つであることを検査して返す。"""
    found = find_entries(module)
    if not found:
        raise BlabError(
            f"component {id!r} に @blab.entry がありません。"
            "入口のクラスか関数に @blab.entry を付けてください"
        )
    if len(found) > 1:
        names = ", ".join(n for n, _ in found)
        raise BlabError(
            f"component {id!r} の @blab.entry が {len(found)} 個あります（{names}）。"
            "入口はちょうど 1 つにしてください"
        )
    return found[0]

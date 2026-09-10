"""blab の例外と「決して学習を落とさない」ためのガード。"""

from __future__ import annotations

import functools
import os
import warnings


class BlabError(Exception):
    """blab の基底例外。"""


class BlabUsageError(BlabError):
    """API の使い方が誤っている（ネストした init など）。"""


def strict() -> bool:
    """厳格モードか。``BLAB_STRICT=1`` で有効。"""
    return os.environ.get("BLAB_STRICT", "") not in ("", "0", "false", "False")


def warn(msg: str) -> None:
    warnings.warn(f"[blab] {msg}", RuntimeWarning, stacklevel=3)


def guard(func):
    """記録系メソッド用。例外を握って警告に落とす（厳格モードでは送出）。

    ``BlabUsageError`` だけは常に送出する（使い方の誤りは黙って握らない）。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except BlabUsageError:
            raise
        except Exception as e:  # noqa: BLE001 - 学習本体を落とさないための意図的な捕捉
            if strict():
                raise
            warn(f"{func.__name__} failed: {e!r}")
            return None

    return wrapper

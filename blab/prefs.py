"""UI 設定の保存（列の表示・並び・フィルタなど）。

閲覧層は run ディレクトリを読むだけだが、UI 設定だけはルート直下の
``.blab-ui/prefs.json`` に置く。ブラウザの localStorage には持たない
（マシンやブラウザを変えても、ディレクトリを ``rsync`` で持って行けば
設定が付いてくる）。

run / group / experiment のディレクトリには一切書かない。``.blab-ui/`` は
``meta.json`` を持たないので索引からも無視される。消しても設定が既定に
戻るだけで、実験データは何も失われない。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .io import dumps, read_json, write_json_atomic

PREFS_DIR = ".blab-ui"
PREFS_NAME = "prefs.json"
SCHEMA_VERSION = 1

#: 1 キーあたりの値の上限（壊れたクライアントがルートを埋めないように）。
MAX_VALUE_BYTES = 256 * 1024
MAX_KEYS = 500


class PrefsStore:
    """``<root>/.blab-ui/prefs.json`` を読み書きする小さな KV ストア。"""

    def __init__(self, root: Path) -> None:
        self.path = Path(root).resolve() / PREFS_DIR / PREFS_NAME
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cache is None:
            data = read_json(self.path, {})
            self._cache = data.get("prefs", {}) if isinstance(data, dict) else {}
            if not isinstance(self._cache, dict):
                self._cache = {}
        return self._cache

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._load())

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._load().get(key, default)

    def set(self, key: str, value: Any) -> dict[str, Any]:
        """1 キーを更新する。``value`` が None ならそのキーを消す。"""
        with self._lock:
            prefs = self._load()
            if value is None:
                prefs.pop(key, None)
            else:
                if len(prefs) >= MAX_KEYS and key not in prefs:
                    raise ValueError(f"too many preference keys (max {MAX_KEYS})")
                if len(dumps(value).encode("utf-8")) > MAX_VALUE_BYTES:
                    raise ValueError(f"preference value too large (max {MAX_VALUE_BYTES} bytes)")
                prefs[key] = value
            self._write(prefs)
            return dict(prefs)

    def clear(self) -> None:
        with self._lock:
            self._write({})

    def _write(self, prefs: dict[str, Any]) -> None:
        write_json_atomic(self.path, {"schema_version": SCHEMA_VERSION, "prefs": prefs})
        self._cache = prefs

"""原子的な JSON 書き込みと JSONL の追記 / 追従読み。

書き込みの原子性（design.md §2.3）:

- JSON 系は同一ディレクトリに一時ファイルを書いて ``os.replace`` で差し替える。
- JSONL は 1 行を単一 ``write`` で追記して ``flush``。読み手は不完全な最終行を捨てる。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


def _umask() -> int:
    """現在の umask を読む（読むだけの API が無いので一度だけ設定して戻す）。"""
    current = os.umask(0)
    os.umask(current)
    return current


def dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(
        obj, ensure_ascii=False, allow_nan=False, default=_fallback, indent=indent
    )


def _fallback(o: Any) -> Any:
    # numpy スカラや Path など、JSON にならない値の最後の受け皿。
    for attr in ("item", "tolist"):
        fn = getattr(o, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    return str(o)


def write_json_atomic(path: Path, obj: Any, indent: int | None = None) -> None:
    """``path`` に JSON を原子的に書く。

    ``indent`` は人が編集する git 管理下のファイル（``blab.json`` など）に使う。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        # mkstemp は 0600 で作る。これらは git に載る普通のファイルなので、
        # umask を尊重した通常のパーミッションに直す。
        os.chmod(tmp, 0o666 & ~_umask())
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(obj, indent=indent))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """JSON を読む。存在しない / 壊れている場合は ``default``。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


class JsonlWriter:
    """追記オンリーの JSONL ライタ。

    既定では 1 行ごとに flush する（ライブ表示のため）。``flush_interval`` に
    秒数を与えると、その間隔でのみ flush してバッファリングする。
    """

    def __init__(self, path: Path, flush_interval: float = 0.0) -> None:
        self.path = Path(path)
        self.flush_interval = float(flush_interval)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8", buffering=1024 * 64)
        self._last_flush = time.monotonic()

    def write(self, record: dict) -> None:
        # 1 レコード = 1 write。行が混ざらないようにここで改行まで含めて書く。
        self._f.write(dumps(record) + "\n")
        now = time.monotonic()
        if self.flush_interval <= 0 or (now - self._last_flush) >= self.flush_interval:
            self._f.flush()
            self._last_flush = now

    def flush(self) -> None:
        try:
            self._f.flush()
        except ValueError:
            pass

    def close(self) -> None:
        try:
            self._f.flush()
            self._f.close()
        except (ValueError, OSError):
            pass


def read_jsonl_from(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """``offset`` バイト目以降の完全な行だけを読む。

    Returns:
        (レコード列, 次回の開始オフセット)。書き込み途中の最終行は捨て、その
        バイト分はオフセットに含めないので、次回に完全な行として読み直せる。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    if size < offset:
        # ファイルが縮んだ（作り直された）。最初から読み直す。
        offset = 0
    if size == offset:
        return [], offset
    records: list[dict] = []
    consumed = offset
    with open(path, "rb") as f:
        f.seek(offset)
        buf = f.read(size - offset)
    end = buf.rfind(b"\n")
    if end == -1:
        return [], offset
    complete = buf[: end + 1]
    consumed += len(complete)
    for line in complete.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records, consumed


def last_jsonl_record(path: Path, tail_bytes: int = 65536) -> dict | None:
    """JSONL の**最後の完全な行**を返す。無ければ None。

    末尾だけを読むので、巨大な ``metrics.jsonl`` を持つ run を開き直しても安い。
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            buf = f.read()
    except OSError:
        return None
    for line in reversed(buf.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # 書き込み途中の最終行 / 途中で切れた先頭行
        if isinstance(rec, dict):
            return rec
    return None


def iter_jsonl(path: Path) -> Iterable[dict]:
    records, _ = read_jsonl_from(path, 0)
    return records

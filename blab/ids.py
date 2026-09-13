"""識別子・時刻・ディレクトリ名。

ULID とディレクトリ名の規約は layout.md §4.1。日時は UTC 固定で名前に入れる
（TZ の異なるマシン間で ``rsync`` しても名前の時系列順が保たれる）。
"""

from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import BlabError

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ts_ms: int | None = None) -> str:
    """ULID を採番する（48bit ミリ秒 + 80bit 乱数、Crockford Base32 26 文字）。

    標準ライブラリのみで実装する（実行層は依存を増やさない）。
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    value = (ts_ms & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD[(value >> (5 * (25 - i))) & 0x1F] for i in range(26))


def short_id(ulid: str) -> str:
    """ディレクトリ名に使う短 ID = ULID の末尾 4 文字（小文字）。"""
    return ulid[-4:].lower()


def slugify(name: str | None, max_len: int = 60) -> str:
    if not name:
        return ""
    s = re.sub(r"[^0-9A-Za-z._-]+", "-", str(name)).strip("-.")
    s = re.sub(r"-{2,}", "-", s).lower()
    return s[:max_len]


def format_run_dir_name(created: datetime, ulid: str, name: str | None) -> str:
    """run のディレクトリ名 ``{YYYYMMDD-HHMMSS}_{短ID}_{slug}``。日時は UTC 固定。"""
    stamp = created.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parts = [stamp, short_id(ulid)]
    slug = slugify(name)
    if slug:
        parts.append(slug)
    return "_".join(parts)


def now() -> datetime:
    """ローカルタイムゾーン付きの現在時刻（``created_at`` などに使う）。"""
    return datetime.now(timezone.utc).astimezone()


def isoformat(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(timespec="seconds")


def create_run_dir(parent: Path, name: str | None) -> tuple[Path, str, datetime]:
    """親の下に一意な run ディレクトリを作る（layout.md §4.1）。

    ``os.mkdir``（exist_ok なし）で作り、既存で失敗したら ULID ごと採番し直して
    リトライする。同一秒・同名の連投でも衝突しない。
    """
    parent.mkdir(parents=True, exist_ok=True)
    created = now()
    for _ in range(64):
        ulid = new_ulid(int(created.timestamp() * 1000))
        path = parent / format_run_dir_name(created, ulid, name)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        return path, ulid, created
    raise BlabError(f"{parent} の下に一意なディレクトリを作れませんでした")

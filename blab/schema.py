"""meta / params / summary のスキーマ、バリデーション、フラット化。"""

from __future__ import annotations

import math
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import BlabError, strict, warn
from .io import read_json
from .layout import SCHEMA_VERSION, KIND_GROUP, KIND_RUN, ROOT_MARKER, isoformat

STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"
STATUSES = (STATUS_RUNNING, STATUS_FINISHED, STATUS_FAILED, STATUS_KILLED)

RESERVED_METRIC_KEYS = ("_step", "_time", "_epoch")

ON_MISSING_ERROR = "error"
ON_MISSING_WARN = "warn"
ON_MISSING_IGNORE = "ignore"
#: ``blab.json`` のポリシーの既定。何も書いていないルートは何も要求しない。
POLICY_DEFAULTS = {
    "require_components": False,
    "require_tags": [],
    "on_missing": ON_MISSING_WARN,
}


def to_jsonable(value: Any) -> Any:
    """numpy スカラ / Path などを JSON で表せる値に落とす。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # JSON は NaN/Inf を表現できない。欠測として None に落とす。
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return isoformat(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return to_jsonable(item())
        except Exception:  # noqa: BLE001
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return to_jsonable(tolist())
        except Exception:  # noqa: BLE001
            pass
    return str(value)


def normalize_params(params: Any, _path: str = "") -> Any:
    """params を JSON 化し、キーのドットを禁止する。

    ドット区切りのフラット化と衝突するため、キーに ``.`` は使えない。
    ``BLAB_STRICT=1`` ならエラー、既定では警告して ``_`` に置換する。
    """
    if isinstance(params, dict):
        out: dict[str, Any] = {}
        for k, v in params.items():
            key = str(k)
            if "." in key:
                where = f"{_path}.{key}" if _path else key
                if strict():
                    raise BlabError(f"param key must not contain '.': {where!r}")
                warn(f"param key {where!r} contains '.'; replaced with '_'")
                key = key.replace(".", "_")
            out[key] = normalize_params(v, f"{_path}.{key}" if _path else key)
        return out
    return to_jsonable(params)


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """ネストした dict をドット区切りにフラット化する。

    リストなど非スカラの葉は JSON 文字列にして 1 列に収める。
    """
    from .io import dumps

    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(flatten(v, key))
            elif isinstance(v, (list, tuple, dict)):
                out[key] = dumps(to_jsonable(v))
            else:
                out[key] = to_jsonable(v)
    elif prefix:
        out[prefix] = to_jsonable(obj)
    return out


def normalize_metrics(data: dict) -> dict:
    """log() に渡された 1 レコードを検証・正規化する。"""
    if not isinstance(data, dict):
        raise BlabError(f"log() takes a dict, got {type(data).__name__}")
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = str(k)
        if key.startswith("_") and key not in RESERVED_METRIC_KEYS:
            msg = f"metric key {key!r} is reserved ('_' prefix)"
            if strict():
                raise BlabError(msg)
            warn(msg)
            key = key.lstrip("_") or "value"
        out[key] = to_jsonable(v)
    return out


def normalize_summary(data: dict) -> dict:
    if not isinstance(data, dict):
        raise BlabError(f"log_summary() takes a dict, got {type(data).__name__}")
    return {str(k): to_jsonable(v) for k, v in data.items()}


# ------------------------------------------------------------------ meta 生成


def git_info(cwd: Path | None = None) -> dict | None:
    """カレントリポジトリの commit / branch / dirty。git が無ければ None。"""

    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", *args],
                cwd=str(cwd or Path.cwd()),
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = run("rev-parse", "--short", "HEAD")
    if commit is None:
        return None
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
    }


def env_info() -> dict:
    return {
        "python": platform.python_version(),
        "hostname": platform.node(),
        "platform": platform.platform(terse=True),
        "pid": os.getpid(),
    }


def cmdline() -> str:
    return " ".join(sys.argv)


def new_run_meta(*, id: str, name: str | None, created_at: datetime, tags, notes: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_RUN,
        "id": id,
        "name": name,
        "status": STATUS_RUNNING,
        "created_at": isoformat(created_at),
        "finished_at": None,
        "duration_sec": None,
        "heartbeat_at": isoformat(created_at),
        "tags": list(tags or []),
        "notes": notes or "",
        "git": git_info(),
        "env": env_info(),
        "cmd": cmdline(),
    }


def new_group_meta(
    *, id: str, name: str | None, created_at: datetime, tags, notes: str, group_kind: str | None
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_GROUP,
        "id": id,
        "name": name,
        "status": STATUS_RUNNING,
        "created_at": isoformat(created_at),
        "finished_at": None,
        "duration_sec": None,
        "tags": list(tags or []),
        "notes": notes or "",
        "group_kind": group_kind,
    }


# ------------------------------------------------------------------ ポリシー（§3.5）


def read_policy(root: Path) -> dict:
    """``blab.json`` のポリシーを読む。書いていないキーは既定。"""
    marker = read_json(Path(root) / ROOT_MARKER, {}) or {}
    if not isinstance(marker, dict):
        marker = {}
    policy = dict(POLICY_DEFAULTS)
    if isinstance(marker.get("require_components"), bool):
        policy["require_components"] = marker["require_components"]
    tags = marker.get("require_tags")
    if isinstance(tags, list):
        policy["require_tags"] = [str(t) for t in tags]
    mode = marker.get("on_missing")
    if mode in (ON_MISSING_ERROR, ON_MISSING_WARN, ON_MISSING_IGNORE):
        policy["on_missing"] = mode
    return policy


def check_policy(root: Path, policy: dict, bindings: list[dict]) -> list[str]:
    """ポリシー違反を列挙する（判定のみ。どう振る舞うかは呼び出し側）。

    タグは component 側（``component.json``）に付いているので、binding の id から
    レジストリを引いて集める。レジストリを読めない binding は**タグ不明**として
    扱い、「タグが無い」と断定しない（§0）。
    """
    from .registry import read_component

    violations: list[str] = []
    if policy.get("require_components") and not bindings:
        violations.append("require_components: component を 1 つも load していない run です")

    want = [t for t in policy.get("require_tags") or []]
    if not want:
        return violations

    seen: set[str] = set()
    unknown: set[str] = set()
    for binding in bindings:
        id = binding.get("id")
        if not id or id in seen or id in unknown:
            continue
        try:
            meta = read_component(Path(root), str(id))
        except BlabError:
            unknown.add(str(id))
            continue
        seen.add(str(id))
        for tag in meta.get("tags") or []:
            seen.add(f"tag:{tag}")
    for tag in want:
        if f"tag:{tag}" not in seen:
            detail = f"require_tags: {tag!r} を持つ component が無い"
            if unknown:
                detail += f"（タグ不明の component: {', '.join(sorted(unknown))}）"
            violations.append(detail)
    return violations


def new_components_doc(bindings: list[dict], entrypoints: list[dict]) -> dict:
    """``components.json``（§4.1）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "bindings": bindings,
        "entrypoints": entrypoints,
    }

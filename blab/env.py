"""`env.json` — 環境情報（layout.md §5.4）。

**記録するだけ。検証も強制もしない。** 揃えられるかはユーザの責任、というのが
design.md §0 の約束である。

取得に失敗した項目は `null` にせず、**理由を残す**。

    {"cuda": {"$unavailable": "torch を import できない"}}

「沈黙による嘘をしない」の適用。捕まえられない情報があること自体は許容するが、
捕まえられなかったことを黙って隠すのは許容しない。
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

from .project import SCHEMA_VERSION


def _unavailable(reason: str) -> dict:
    return {"$unavailable": reason}


def collect(project_root: Path | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "pid": os.getpid(),
        "argv": list(sys.argv),
        **_packages(project_root),
        "git": _git(project_root),
        "cuda": _cuda(),
    }


def _packages(project_root: Path | None) -> dict:
    """`uv.lock` があればそのハッシュ、無ければ import 済みの版を集める。"""
    out: dict = {}
    lock = (Path(project_root) / "uv.lock") if project_root else None
    if lock is not None and lock.is_file():
        out["packages_hash"] = "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    else:
        out["packages_hash"] = _unavailable("uv.lock がない")

    try:
        from importlib import metadata

        out["packages"] = {
            dist.metadata["Name"]: dist.version
            for dist in metadata.distributions()
            if dist.metadata.get("Name")
        }
    except Exception as e:  # noqa: BLE001 - 環境情報の取得で学習を落とさない
        out["packages"] = _unavailable(f"importlib.metadata が読めない: {e}")
    return out


def _git(project_root: Path | None) -> dict:
    if project_root is None:
        return _unavailable("プロジェクトルートが分からない")
    root = str(project_root)

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return _unavailable("git リポジトリではない、または git が無い")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def _cuda() -> dict:
    if "torch" not in sys.modules:
        try:
            import torch  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return _unavailable(f"torch を import できない: {type(e).__name__}")
    torch = sys.modules["torch"]
    try:
        if not torch.cuda.is_available():
            return _unavailable("CUDA が利用できない")
        return {
            "torch_cuda": torch.version.cuda,
            "cudnn": str(getattr(torch.backends.cudnn, "version", lambda: None)()),
            "gpu": torch.cuda.get_device_name(0),
            "n_gpu": torch.cuda.device_count(),
        }
    except Exception as e:  # noqa: BLE001
        return _unavailable(f"CUDA 情報の取得に失敗: {type(e).__name__}: {e}")

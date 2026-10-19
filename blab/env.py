"""`env.json` — 環境情報（layout.md §5.4）。

**記録するだけ。検証も強制もしない。** 揃えられるかはユーザの責任とする。

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
    """CUDA 情報。**親プロセスで CUDA を初期化しない**（driver の `cuInit` も含めて）。

    親が一度でも CUDA に触ると、fork で GPU ごとに子を立てるコンポーネントが
    `Cannot re-initialize CUDA in forked subprocess` で落ちる。`get_device_name` は
    `_lazy_init()` で、`is_available()` は（`PYTORCH_NVML_BASED_CUDA_CHECK` が無ければ）
    `cudaGetDeviceCount` で、`device_count()` も NVML に失敗すると同じ経路で CUDA に触る。
    `torch.backends.cudnn.version()` も初回の `_init()` で `is_available()` を呼ぶ。
    だから初期化済みのときだけ torch に聞き、それ以外は GPU の有無も名前も nvidia-smi に聞く。
    """
    if "torch" not in sys.modules:
        try:
            import torch  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return _unavailable(f"torch を import できない: {type(e).__name__}")
    torch = sys.modules["torch"]
    try:
        if torch.version.cuda is None:
            return _unavailable("torch が CUDA 無しでビルドされている")
        if torch.cuda.is_initialized():
            gpus: list[str] | dict = [
                torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
            ]
        else:
            gpus = _visible_gpus()
        if isinstance(gpus, dict):
            return gpus
        if not gpus:
            return _unavailable("CUDA が利用できない")
        return {
            "torch_cuda": torch.version.cuda,
            "cudnn": _cudnn_version(torch),
            "gpu": gpus[0],
            "n_gpu": len(gpus),
        }
    except Exception as e:  # noqa: BLE001
        return _unavailable(f"CUDA 情報の取得に失敗: {type(e).__name__}: {e}")


def _cudnn_version(torch) -> str | dict:
    """cuDNN の版。`torch.backends.cudnn.version()` は fork を汚すので、ライブラリに直接聞く。"""
    if torch.cuda.is_initialized():
        return str(torch.backends.cudnn.version())
    cudnn = getattr(torch._C, "_cudnn", None)
    if cudnn is None:
        return _unavailable("torch が cuDNN 無しでビルドされている")
    return str(cudnn.getVersionInt())


def _visible_gpus() -> list[str] | dict:
    """torch から見える GPU の名前を、torch の device 番号順に nvidia-smi で得る。

    nvidia-smi は `CUDA_VISIBLE_DEVICES` を見ないので、ここで index / UUID を突き合わせる。
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return _unavailable(f"nvidia-smi を実行できない: {type(e).__name__}")
    if out.returncode != 0:
        return _unavailable("CUDA が利用できない（nvidia-smi が失敗した）")
    rows = [
        [c.strip() for c in line.split(",", 2)]
        for line in out.stdout.splitlines()
        if line.count(",") >= 2
    ]
    if "CUDA_VISIBLE_DEVICES" not in os.environ:
        return [name for _, _, name in rows]
    gpus = []
    for want in os.environ["CUDA_VISIBLE_DEVICES"].split(","):
        want = want.strip()
        hit = [n for idx, uuid, n in rows if want and (want == idx or uuid.startswith(want))]
        if len(hit) != 1:
            break  # CUDA も不正な（見つからない）エントリ以降は無視する
        gpus.append(hit[0])
    return gpus

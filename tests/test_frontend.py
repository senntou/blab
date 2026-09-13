"""Phase 9 — 描画モジュールのテスト（node で動かす）。

DOM の最小シムの上で config-tree.js と table.js を描き、設計上の約束
（引数の出どころを区別する / 記録できなかったものを省略しない）が実際に
出ているかを見る。node が無い環境では skip する。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent / "js" / "render.test.mjs"


@pytest.mark.skipif(NODE is None, reason="node が無い")
def test_render_modules():
    result = subprocess.run(
        [NODE, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(NODE is None, reason="node が無い")
@pytest.mark.parametrize(
    "path",
    sorted(
        p.relative_to(Path(__file__).parent.parent).as_posix()
        for p in (Path(__file__).parent.parent / "blab" / "static" / "js").rglob("*.js")
    ),
)
def test_javascript_parses(path):
    result = subprocess.run(
        [NODE, "--check", path],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

"""stdout / stderr の捕捉（layout.md §5.5）。

捕捉は `sys.stdout` の差し替えではなく **fd レベルの複製**で行う。`os.dup2` で
パイプに向け、読んだものをファイルと元の fd の両方へ流す（tee）。torch や CUDA が
**C レベルで書く出力**を取りこぼさないためである。

fd の差し替えはデバッガや進捗バーと相性が悪いので、`blab run --no-capture` で無効に
できる。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


class _Tee:
    """1 本の fd をパイプへ向け、読んだものをファイルと元の fd へ流す。"""

    def __init__(self, stream, path: Path) -> None:
        self._stream = stream
        self._fd = stream.fileno()
        self._path = Path(path)
        self._saved = os.dup(self._fd)
        self._read, write = os.pipe()
        os.dup2(write, self._fd)
        os.close(write)

        # fd がパイプになると Python は print をブロックバッファリングに切り替える。
        # すると C レベルの書き込みとの前後関係が崩れ、ログの行順が実際の順序と
        # ずれる。端末で見るのと同じ順序を保つため、行バッファリングに戻す。
        self._line_buffered = None
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                self._line_buffered = stream.line_buffering
                reconfigure(line_buffering=True)
            except (ValueError, OSError):
                self._line_buffered = None

        self._file = open(self._path, "ab", buffering=0)
        self._thread = threading.Thread(target=self._pump, daemon=True, name="blab-tee")
        self._thread.start()

    def _pump(self) -> None:
        while True:
            try:
                chunk = os.read(self._read, 65536)
            except OSError:
                break
            if not chunk:
                break
            try:
                os.write(self._saved, chunk)  # 端末には元どおり出す
            except OSError:
                pass
            try:
                self._file.write(chunk)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._stream.flush()
        except (ValueError, OSError):
            pass
        if self._line_buffered is not None:
            try:
                self._stream.reconfigure(line_buffering=self._line_buffered)
            except (ValueError, OSError):
                pass
        # パイプの書き手を閉じると pump が EOF で抜ける。
        os.dup2(self._saved, self._fd)
        os.close(self._saved)
        self._thread.join(timeout=5)
        try:
            os.close(self._read)
        except OSError:
            pass
        try:
            self._file.close()
        except OSError:
            pass


class Capture:
    """`logs/stdout.log` と `logs/stderr.log` に tee する context manager。"""

    def __init__(self, logs_dir: Path, enabled: bool = True) -> None:
        self.logs_dir = Path(logs_dir)
        self.enabled = enabled
        self._tees: list[_Tee] = []

    def __enter__(self) -> "Capture":
        if not self.enabled:
            return self
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        for stream, name in ((sys.stdout, "stdout.log"), (sys.stderr, "stderr.log")):
            try:
                stream.fileno()
            except (AttributeError, ValueError, OSError):
                # pytest の capture 下など、fd を持たないストリーム。捕捉しない。
                continue
            try:
                self._tees.append(_Tee(stream, self.logs_dir / name))
            except OSError:
                continue
        return self

    def __exit__(self, *exc) -> None:
        for tee in reversed(self._tees):
            try:
                tee.close()
            except Exception:  # noqa: BLE001 - 後始末で学習結果を落とさない
                pass
        self._tees.clear()

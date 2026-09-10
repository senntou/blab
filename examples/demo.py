"""UI を試すためのサンプルデータ生成。

    python examples/demo.py --dir ./.blab

component を 3 つ登録し、それを使った単独 run を数本と CV group（5 fold）を作る。
実行中の見え方を確認したい場合は ``--live`` を付けると 1 本を running のまま
数十秒かけてログし続ける。標準ライブラリだけで動く（学習は疑似データ）。

より実際の実験に近いデモ（two moons + PyTorch の MLP、dirty 実行や整合性ゲートの
確認まで含む）は ``demo/``（git 管理外）にある。
"""

from __future__ import annotations

import argparse
import math
import random
import tempfile
import time
from pathlib import Path

import blab
from blab import layout, registry


# ------------------------------------------------------------ component の登録

BACKBONE = '''"""疑似 CNN の特徴抽出部。デモなので数を返すだけ。"""

import blab


@blab.entry
class CnnBackbone:
    def __init__(self, depth: int = 18):
        self.depth = depth
        self.out_features = 64 * depth
'''

CLASSIFIER = '''"""backbone を合成した分類器。

依存は __init__ の中で blab.load する（継承ではなく合成なので、使った backbone が
binding として記録に出る）。
"""

import blab


@blab.entry
class CnnClassifier:
    def __init__(self, k: int = 10, depth: int = 18):
        self.backbone = blab.load("cnn_backbone", depth=depth)
        self.k = k

    @property
    def n_params(self):
        return self.backbone.out_features * self.k
'''

CLASSIFIER_V2 = CLASSIFIER.replace(
    "        self.k = k",
    "        self.k = k\n        self.dropout = 0.1   # v2: ヘッド手前に dropout",
)

AUGMENT = '''"""データ拡張の指定。文字列のリストを返すだけのファクトリ。"""

import blab


@blab.entry
def augment(policy: str = "flip-crop"):
    return policy.split("-")
'''

COMPONENTS = [
    ("cnn_backbone", [("v1", "初版", "backbone", BACKBONE)]),
    (
        "cnn_classifier",
        [
            ("v1", "初版。backbone を合成する", "model", CLASSIFIER),
            ("v2", "dropout を追加", "model", CLASSIFIER_V2),
        ],
    ),
    ("augment", [("v1", "初版", "augmentation", AUGMENT)]),
]


def register_components(root) -> None:
    """デモ用の component をレジストリに用意する。

    実際の運用では ``blab new`` で雛形を作って ``current/`` を手で編集し、
    ``blab register`` で凍結する。ここでは再現可能にするためソースを埋め込んでいる。
    """
    for id, versions in COMPONENTS:
        current = layout.current_source_path(root, id)
        if not current.exists():
            registry.create_component(root, id)
        for version, note, tags, source in versions:
            if layout.version_source_path(root, id, version).exists():
                continue
            current.write_text(source, encoding="utf-8")
            registry.register(root, id, version, note=note, tags=[tags])
            print(f"[demo] registered {id}@{version}")


def train(run, *, lr: float, epochs: int = 20, steps: int = 40, delay: float = 0.0) -> float:
    rng = random.Random(hash(run.name) & 0xFFFF)
    loss = 2.3
    acc = 0.1
    for epoch in range(epochs):
        for _ in range(steps):
            loss = max(0.02, loss - lr * 40 * rng.uniform(0.5, 1.5) + rng.gauss(0, 0.01))
            run.log({"train/loss": loss}, epoch=epoch)
            if delay:
                time.sleep(delay)
        acc = min(0.99, 1 - math.exp(-0.15 * (epoch + 1)) + rng.gauss(0, 0.01))
        run.log({"val/acc": acc, "val/loss": loss * 1.1}, epoch=epoch)
    return acc


def make_png(path: Path, seed: int) -> Path:
    """依存なしで小さな PNG を書く（アーティファクト表示の確認用）。"""
    import struct
    import zlib

    w = h = 64
    rng = random.Random(seed)
    base = (rng.randrange(60, 200), rng.randrange(60, 200), rng.randrange(60, 200))
    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            k = (x * y + seed) % 255
            raw += bytes(((base[0] + k) % 256, (base[1] + k // 2) % 256, base[2]))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="blab ルート")
    ap.add_argument("--live", action="store_true", help="running な run を 1 本残す")
    args = ap.parse_args()

    root = layout.resolve_root(args.dir, create=True)
    register_components(root)

    # 生成した PNG は blab ルートの外に置く（ルート配下は run ディレクトリだけにする）。
    tmp = Path(tempfile.mkdtemp(prefix="blab-demo-"))

    # --- 単独 run を 3 本
    for i, lr in enumerate([3e-4, 1e-3, 3e-3]):
        cfg = {"lr": lr, "bs": 128, "optim": {"name": "adam", "wd": 1e-4}, "aug": ["flip", "crop"]}
        with blab.init(
            experiment="mnist-cnn", name=f"baseline-lr{lr:g}", params=cfg, dir=root,
            tags=["cnn", "baseline"],
        ) as run:
            # 何を使ったかは blab.load() から観測される（宣言しない）
            model = blab.load("cnn_classifier@v2" if i else "cnn_classifier@v1", role="model", k=10)
            blab.load("augment@v1", role="aug", policy="flip-crop")
            acc = train(run, lr=lr)
            run.log_summary({"test/acc": acc, "test/loss": 0.2 + i * 0.01, "params/total": model.n_params})
            run.log_artifact(make_png(tmp / f"cm{i}.png", i), name="confusion_matrix.png")
            (run.dir / "logs" / "stdout.log").write_text(
                f"training with lr={lr}\nfinished acc={acc:.4f}\n", encoding="utf-8"
            )

    # --- 失敗する run を 1 本
    try:
        with blab.init(experiment="mnist-cnn", name="oom", params={"lr": 1e-2, "bs": 4096}, dir=root) as run:
            blab.load("cnn_classifier@v2", role="model", k=10, depth=152)
            train(run, lr=1e-2, epochs=3)
            raise RuntimeError("CUDA out of memory")
    except RuntimeError:
        pass

    # --- CV group
    with blab.group(experiment="mnist-cnn", name="cv5", group_kind="cv", dir=root) as g:
        for fold in range(5):
            with g.run(name=f"fold{fold}", params={"lr": 3e-4, "bs": 128, "fold": fold}) as run:
                blab.load("cnn_classifier@v2", role="model", k=10)
                blab.load("augment@v1", role="aug", policy=f"flip-crop-fold{fold}")
                acc = train(run, lr=3e-4, epochs=15)
                run.log_summary({"test/acc": acc, "test/loss": 0.22 - fold * 0.003})
                run.log_artifact(make_png(tmp / f"fold{fold}.png", 10 + fold), name="confusion_matrix.png")

    # --- 別 experiment
    with blab.init(experiment="cifar-resnet", name="r18", params={"lr": 0.1, "depth": 18}, dir=root) as run:
        blab.load("cnn_classifier@v2", role="model", k=100, depth=18)
        acc = train(run, lr=1e-3, epochs=10)
        run.log_summary({"test/acc": acc})

    if args.live:
        print("[demo] live run: Ctrl-C で終了")
        with blab.init(experiment="mnist-cnn", name="live", params={"lr": 3e-4}, dir=root) as run:
            blab.load("cnn_classifier@v2", role="model", k=10)
            train(run, lr=3e-4, epochs=60, steps=20, delay=0.05)

    print("[demo] done. `blab ui --open` で閲覧できます")


if __name__ == "__main__":
    main()

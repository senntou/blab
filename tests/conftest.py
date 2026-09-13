from __future__ import annotations

import sys
from pathlib import Path

import pytest

from blab.project import Project, init

#: 素の component。`main.py` に @blab.entry を 1 つだけ置く。
TRAINER = '''
import blab

from .loops import step


@blab.entry
class StandardTrainer:
    def __init__(self, dataset, model, epochs, lr=0.001, seed=0):
        self.dataset, self.model = dataset, model
        self.epochs, self.lr, self.seed = epochs, lr, seed

    def execute(self, run):
        train = self.dataset.build(split="train")
        val = self.dataset.build(split="val")
        model = self.model.build(k=train.n_classes)
        for epoch in range(self.epochs):
            run.log({"loss": 1.0 / (step(epoch) + 1)}, epoch=epoch)
        run.log_summary({"acc": 0.5 + 0.001 * model.k, "n_val": len(val.split)})
'''

LOOPS = '''
def step(n):
    return n
'''

DATASET = '''
import blab


@blab.entry
class Cifar100:
    def __init__(self, split, augment=None, root=None):
        self.split, self.augment, self.root = split, augment, root
        self.n_classes = 100
'''

MODEL = '''
import blab


@blab.entry
class Resnet18:
    def __init__(self, k, pretrained=False):
        self.k, self.pretrained = k, pretrained
'''


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """グローバル索引を本物の `~/.blab` から切り離す。"""
    monkeypatch.setenv("BLAB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BLAB_RUNS", raising=False)
    yield


@pytest.fixture(autouse=True)
def clean_modules():
    """テスト間で合成モジュールを持ち越さない（同じ id・別ハッシュを何度も作るため）。"""
    yield
    for name in [n for n in sys.modules if n.startswith("blab._c")]:
        del sys.modules[name]


def write_component(project: Project, id: str, main: str, **extra: str) -> Path:
    path = project.components_dir / id
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.py").write_text(main, encoding="utf-8")
    for name, text in extra.items():
        (path / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path) -> Project:
    """trainer / dataset / model が入った、検証を通るプロジェクト。"""
    proj, _ = init(tmp_path / "proj", name="testproj")
    write_component(proj, "standard_trainer", TRAINER, loops__py=LOOPS)
    write_component(proj, "cifar100", DATASET)
    write_component(proj, "resnet18", MODEL)
    return proj


def write_experiment(project: Project, name: str, text: str) -> Path:
    path = project.experiments_dir / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


BASELINE = """
experiment: cifar100
name: baseline

run:
  use: standard_trainer
  epochs: 10
  dataset: {use: cifar100, augment: randaug}
  model: {use: resnet18, pretrained: false}
"""

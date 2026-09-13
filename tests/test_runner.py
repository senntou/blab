"""Phase 5-6 — `blab run`: 実行・記録・run ディレクトリ・焼き込み・env / data。"""

from __future__ import annotations

import json

import pytest
import yaml

from blab.components import hash_dir
from blab.resolved import RESOLVED_NAME, iter_components
from blab.run import STATUS_FAILED, STATUS_FINISHED, STATUS_KILLED
from blab.runner import execute
from blab.spec import parse_document
from conftest import BASELINE, write_component


def go(project, text: str = BASELINE, **kw):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False, **kw)


def test_run_produces_a_self_contained_directory(project):
    outcome = go(project)
    path = outcome.run.path
    assert outcome.status == STATUS_FINISHED
    for name in ("meta.json", RESOLVED_NAME, "env.json", "summary.json", "metrics.jsonl"):
        assert (path / name).is_file(), name
    assert (path / "components").is_dir()


def test_baked_components_match_the_recorded_hashes(project):
    """**run のディレクトリさえあればコードは完全に復元できる**（layout.md §5.3）。

    凍結ディレクトリは import された時点で `__pycache__` が書かれるので、焼き込みは
    ハッシュ対象と同じフィルタを通さないとこの不変条件が壊れる。
    """
    path = go(project).run.path
    doc = yaml.safe_load((path / RESOLVED_NAME).read_text(encoding="utf-8"))
    seen = 0
    for _, component in iter_components(doc):
        baked = path / "components" / component["use"]
        assert hash_dir(baked) == component["hash"], component["use"]
        seen += 1
    assert seen == 3


def test_experiment_directory_is_created(project):
    path = go(project).run.path
    experiment = path.parent
    assert json.loads((experiment / "meta.json").read_text())["kind"] == "experiment"


def test_metrics_are_appended(project):
    path = go(project).run.path
    lines = [json.loads(l) for l in (path / "metrics.jsonl").read_text().splitlines()]
    assert len(lines) == 10
    assert [l["_epoch"] for l in lines] == list(range(10))
    assert all("_step" in l and "_time" in l for l in lines)


def test_summary_is_written(project):
    outcome = go(project)
    assert outcome.run.summary["acc"] == pytest.approx(0.6)
    assert json.loads((outcome.run.path / "summary.json").read_text())["acc"] == pytest.approx(0.6)


def test_params_json_is_gone(project):
    """ハイパラは component の引数であり、`resolved.yaml` に全部入っている。"""
    assert not (go(project).run.path / "params.json").exists()


class TestStatus:
    def test_failure_is_recorded_with_a_traceback(self, project):
        write_component(
            project,
            "boom",
            "import blab\n\n@blab.entry\nclass Boom:\n"
            "    def __init__(self):\n        pass\n"
            "    def execute(self, run):\n        raise ValueError('ばーん')\n",
        )
        outcome = go(project, "experiment: x\nrun: {use: boom}\n")
        assert outcome.status == STATUS_FAILED
        meta = json.loads((outcome.run.path / "meta.json").read_text())
        assert meta["status"] == STATUS_FAILED
        assert meta["exit"]["type"] == "ValueError"
        assert "ばーん" in meta["exit"]["message"]
        assert "Traceback" in meta["exit"]["traceback"]

    def test_partial_observation_survives_a_failure(self, project):
        """例外で終わっても、それまでの `builds` は残す。"""
        write_component(
            project,
            "halfway",
            "import blab\n\n@blab.entry\nclass Halfway:\n"
            "    def __init__(self, dataset):\n        self.dataset = dataset\n"
            "    def execute(self, run):\n"
            "        self.dataset.build(split='train')\n"
            "        raise RuntimeError('stop')\n",
        )
        outcome = go(project, "experiment: x\nrun: {use: halfway, dataset: {use: cifar100}}\n")
        doc = yaml.safe_load((outcome.run.path / RESOLVED_NAME).read_text())
        assert doc["run"]["children"]["dataset"]["builds"][0]["args"]["split"] == "train"

    def test_keyboard_interrupt_is_killed_not_failed(self, project):
        write_component(
            project,
            "stopme",
            "import blab\n\n@blab.entry\nclass StopMe:\n"
            "    def __init__(self):\n        pass\n"
            "    def execute(self, run):\n        raise KeyboardInterrupt\n",
        )
        assert go(project, "experiment: x\nrun: {use: stopme}\n").status == STATUS_KILLED

    def test_duration_is_recorded(self, project):
        meta = json.loads((go(project).run.path / "meta.json").read_text())
        assert meta["duration_sec"] is not None and meta["finished_at"] is not None


class TestGroup:
    def test_declaring_a_group_creates_the_directory(self, project):
        outcome = go(project, BASELINE + "\ngroup: cv5\n")
        group = outcome.run.path.parent
        assert group.name == "cv5"
        assert json.loads((group / "meta.json").read_text())["kind"] == "group"

    def test_runs_declaring_the_same_name_land_together(self, project):
        """**グループは実行が作るのではなく、宣言が作る。**"""
        a = go(project, BASELINE + "\ngroup: cv5\n").run.path
        b = go(project, BASELINE + "\ngroup: cv5\n").run.path
        assert a.parent == b.parent
        assert a != b

    def test_group_has_no_status(self, project):
        """group を所有するプロセスが存在しないので、終わったかを誰も知らない。"""
        group = go(project, BASELINE + "\ngroup: cv5\n").run.path.parent
        meta = json.loads((group / "meta.json").read_text())
        assert "status" not in meta and "heartbeat_at" not in meta


class TestUnusedWarning:
    def test_unused_child_is_reported(self, project):
        write_component(
            project,
            "lazy",
            "import blab\n\n@blab.entry\nclass Lazy:\n"
            "    def __init__(self, dataset, model):\n"
            "        self.dataset, self.model = dataset, model\n"
            "    def execute(self, run):\n        self.dataset.build(split='train')\n",
        )
        text = "experiment: x\nrun: {use: lazy, dataset: {use: cifar100}, model: {use: resnet18}}\n"
        assert go(project, text).unused == ["model"]


class TestEnv:
    def test_records_what_it_can(self, project):
        env = json.loads((go(project).run.path / "env.json").read_text())
        assert env["python"] and env["hostname"] and env["platform"]

    def test_missing_things_say_why(self, project):
        """取得に失敗した項目は `null` にせず、理由を残す。"""
        env = json.loads((go(project).run.path / "env.json").read_text())
        assert "$unavailable" in env["cuda"]  # このテスト環境に CUDA は無い


class TestData:
    def test_small_files_are_copied_into_the_run(self, project, tmp_path):
        csv = tmp_path / "folds.csv"
        csv.write_text("a,b\n1,2\n", encoding="utf-8")
        project.local["data"] = {"FOLDS": str(csv)}
        text = BASELINE.replace("augment: randaug", "root: {data: FOLDS}")
        path = go(project, text).run.path

        doc = json.loads((path / "data.json").read_text())
        assert doc["FOLDS"]["copied_to"] == "data/folds.csv"
        assert (path / "data" / "folds.csv").read_text() == "a,b\n1,2\n"
        assert doc["FOLDS"]["hash"].startswith("sha256:")

    def test_component_receives_the_original_path(self, project, tmp_path):
        csv = tmp_path / "folds.csv"
        csv.write_text("a\n", encoding="utf-8")
        project.local["data"] = {"FOLDS": str(csv)}
        text = BASELINE.replace("augment: randaug", "root: {data: FOLDS}")
        path = go(project, text).run.path

        doc = yaml.safe_load((path / RESOLVED_NAME).read_text())
        # 記録には実パスではなく論理名が残る。
        recorded = doc["run"]["children"]["dataset"]["builds"][0]["args"]["root"]
        assert recorded == {"$data": "FOLDS"}

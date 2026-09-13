"""Phase 4 — Builder の引数合体と観測、`resolved.yaml` の生成。"""

from __future__ import annotations

import pytest
import yaml

from blab.builder import Builder, Recorder, instantiate_root
from blab.errors import BlabError
from blab.preflight import preflight
from blab.resolved import build_document, unused_children
from blab.spec import parse_document
from conftest import BASELINE, write_component


def prepared(project, text: str = BASELINE):
    experiment = parse_document(yaml.safe_load(text))
    report = preflight(project, experiment)
    assert report.ok, [f.message for f in report.findings]
    return experiment, report


def recorder_for(project, text: str = BASELINE, **kw):
    experiment, report = prepared(project, text)
    return experiment, report, Recorder(report.root, **kw)


#: `$ref` / `$unrecorded` を見るための、dataset と holder だけを持つ root。
PAIR = """
experiment: x
run:
  use: pair
  dataset: {use: cifar100}
  holder: {use: holder}
"""


def _pair_root(project) -> None:
    write_component(
        project,
        "holder",
        "import blab\n\n@blab.entry\nclass Holder:\n"
        "    def __init__(self, thing):\n        self.thing = thing\n",
    )
    write_component(
        project,
        "pair",
        "import blab\n\n@blab.entry\nclass Pair:\n"
        "    def __init__(self, dataset, holder):\n"
        "        self.dataset, self.holder = dataset, holder\n"
        "    def execute(self, run):\n        pass\n",
    )


class TestMerge:
    def test_children_arrive_as_builders(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        assert isinstance(root.dataset, Builder)
        assert isinstance(root.model, Builder)

    def test_runtime_wins_over_yaml(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        dataset = root.dataset.build(split="train", augment="none")
        assert dataset.augment == "none"  # YAML は randaug だがコード側が勝つ

    def test_one_builder_makes_many_instances(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        train = root.dataset.build(split="train")
        val = root.dataset.build(split="val")
        assert train is not val
        assert (train.split, val.split) == ("train", "val")

    def test_missing_runtime_argument_fails_at_build(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        with pytest.raises(BlabError, match="split"):
            root.dataset.build()

    def test_unknown_runtime_argument_fails(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        with pytest.raises(BlabError, match="nope"):
            root.dataset.build(split="train", nope=1)

    def test_defaults_are_filled_in(self, project):
        _, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        assert root.lr == 0.001 and root.seed == 0


class TestObservation:
    def test_records_the_merged_arguments(self, project):
        experiment, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        train = root.dataset.build(split="train")
        root.model.build(k=train.n_classes)

        doc = build_document(experiment, recorder, project_uid=project.uid)
        model = doc["run"]["children"]["model"]["builds"][0]
        # `k` は YAML のどこにも書かれていない。実際にそう渡されたから記録されている。
        assert model["args"]["k"] == 100
        assert model["args_from"]["k"] == "runtime"
        assert model["args_from"]["pretrained"] == "yaml"

    def test_records_defaults_with_their_values(self, project):
        experiment, _, recorder = recorder_for(project)
        instantiate_root(recorder)
        doc = build_document(experiment, recorder, project_uid=project.uid)
        assert doc["run"]["args"]["lr"] == 0.001
        assert doc["run"]["args_from"]["lr"] == "default"

    def test_marks_overridden_arguments(self, project):
        experiment, report = prepared(project)
        experiment.overrides = {"run.epochs": 3}
        recorder = Recorder(report.root, overrides=experiment.overrides)
        instantiate_root(recorder)
        doc = build_document(experiment, recorder, project_uid=project.uid)
        assert doc["run"]["args_from"]["epochs"] == "override"

    def test_one_entry_per_distinct_argument_set(self, project):
        experiment, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        root.dataset.build(split="train")
        root.dataset.build(split="train")  # 同一引数は畳む
        root.dataset.build(split="val")
        doc = build_document(experiment, recorder, project_uid=project.uid)
        splits = [b["args"]["split"] for b in doc["run"]["children"]["dataset"]["builds"]]
        assert splits == ["train", "val"]

    def test_detects_objects_returned_by_build(self, project):
        """`.build()` が返したオブジェクトは `$ref` になる。推測しない。"""
        _pair_root(project)
        experiment, _, recorder = recorder_for(project, PAIR)
        root = instantiate_root(recorder)
        train = root.dataset.build(split="train")
        root.holder.build(thing=train)

        doc = build_document(experiment, recorder, project_uid=project.uid)
        assert doc["run"]["children"]["holder"]["builds"][0]["args"]["thing"] == {
            "$ref": "dataset#0"
        }

    def test_unrecordable_objects_say_so(self, project):
        _pair_root(project)
        experiment, _, recorder = recorder_for(project, PAIR)
        root = instantiate_root(recorder)
        root.holder.build(thing=object())
        doc = build_document(experiment, recorder, project_uid=project.uid)
        recorded = doc["run"]["children"]["holder"]["builds"][0]["args"]["thing"]
        assert recorded == {"$unrecorded": "object"}


class TestUnused:
    def test_declared_but_never_built_is_kept(self, project):
        """**エラーにはしないが、黙って消さない**（B-6 の決定）。"""
        experiment, _, recorder = recorder_for(project)
        root = instantiate_root(recorder)
        root.dataset.build(split="train")
        # model は build しない。

        doc = build_document(experiment, recorder, project_uid=project.uid)
        assert doc["run"]["children"]["model"]["builds"] == []
        assert unused_children(recorder.root) == ["model"]

"""Phase 3 — 事前検証。**学習を 1 秒でも始める前に全部落とす**（layout.md §3.2）。"""

from __future__ import annotations

import pytest
import yaml

from blab.preflight import preflight
from blab.spec import parse_document
from conftest import BASELINE, MODEL, write_component


def check(project, text: str):
    return preflight(project, parse_document(yaml.safe_load(text)))


def messages(report) -> str:
    return "\n".join(f.message for f in report.findings)


def test_valid_configuration_passes(project):
    report = check(project, BASELINE)
    assert report.ok, messages(report)
    assert report.root.resolved.id == "standard_trainer"


class TestExistence:
    def test_missing_component(self, project):
        report = check(project, BASELINE.replace("use: resnet18", "use: resnet50"))
        assert not report.ok
        assert "resnet50" in messages(report)

    def test_missing_main_py(self, project):
        (project.components_dir / "empty").mkdir()
        report = check(project, BASELINE.replace("use: resnet18", "use: empty"))
        assert "main.py" in messages(report)

    def test_reports_every_problem_not_just_the_first(self, project):
        report = check(
            project,
            BASELINE.replace("use: resnet18", "use: nope1").replace(
                "use: cifar100", "use: nope2"
            ),
        )
        assert len(report.errors) == 2


class TestImport:
    def test_syntax_error_points_at_the_working_copy(self, project):
        write_component(project, "broken", "import blab\n\n@blab.entry\nclass A\n    pass\n")
        report = check(project, BASELINE.replace("use: resnet18", "use: broken"))
        assert "SyntaxError" in messages(report)
        # 凍結パスではなく、人が直せるパス表記で出す。
        assert "broken/main.py" in messages(report)
        assert ".blab/frozen" not in messages(report)

    def test_import_error(self, project):
        write_component(project, "bad", "import blab\nimport no_such_package_xyz\n")
        report = check(project, BASELINE.replace("use: resnet18", "use: bad"))
        assert "no_such_package_xyz" in messages(report)

    def test_two_entries(self, project):
        write_component(
            project, "two", "import blab\n\n@blab.entry\nclass A: pass\n\n@blab.entry\nclass B: pass\n"
        )
        report = check(project, BASELINE.replace("use: resnet18", "use: two"))
        assert "2 個あります" in messages(report)

    def test_no_entry(self, project):
        write_component(project, "none", "class Plain: pass\n")
        report = check(project, BASELINE.replace("use: resnet18", "use: none"))
        assert "@blab.entry がありません" in messages(report)


class TestRoot:
    def test_root_must_have_execute(self, project):
        report = check(project, "experiment: x\nrun: {use: resnet18, k: 10}\n")
        assert "execute" in messages(report)

    def test_root_required_args_must_be_in_yaml(self, project):
        """root は誰も `.build()` しないので、実行時に渡される経路が無い。"""
        report = check(project, BASELINE.replace("  epochs: 10\n", ""))
        assert "epochs" in messages(report)
        assert not report.ok

    def test_factory_function_root_is_allowed(self, project):
        write_component(
            project,
            "factory",
            "import blab\n\n@blab.entry\ndef make():\n    return object()\n",
        )
        report = check(project, "experiment: x\nrun: {use: factory}\n")
        # 戻り値を静的に見られないので通す（実行時に確かめる）。
        assert report.ok, messages(report)


class TestSignature:
    def test_unknown_argument_suggests_the_right_one(self, project):
        report = check(project, BASELINE.replace("epochs: 10", "epocs: 10"))
        assert "もしかして 'epochs'" in messages(report)

    def test_lists_what_is_accepted(self, project):
        report = check(project, BASELINE + "  nope: 1\n")
        assert "受け取れるのは" in messages(report)

    def test_child_required_args_are_deferred_to_build(self, project):
        report = check(project, BASELINE)
        dataset = dict(report.root.children())["run.dataset"]
        assert dataset.runtime_required == ["split"]
        assert report.ok

    def test_defaults_are_recorded_with_their_values(self, project):
        """シグネチャのデフォルトも値を埋めて記録する（B-4 の決定）。"""
        report = check(project, BASELINE)
        assert report.root.defaults == {"lr": 0.001, "seed": 0}

    def test_var_keyword_accepts_anything(self, project):
        write_component(
            project,
            "flexible",
            "import blab\n\n@blab.entry\nclass F:\n"
            "    def __init__(self, **kw):\n        self.kw = kw\n"
            "    def execute(self, run):\n        pass\n",
        )
        report = check(project, "experiment: x\nrun: {use: flexible, anything: 1}\n")
        assert report.ok, messages(report)

    def test_positional_only_is_rejected(self, project):
        write_component(
            project,
            "posonly",
            "import blab\n\n@blab.entry\nclass P:\n"
            "    def __init__(self, a, /):\n        self.a = a\n"
            "    def execute(self, run):\n        pass\n",
        )
        report = check(project, "experiment: x\nrun: {use: posonly, a: 1}\n")
        assert "位置専用" in messages(report)


class TestData:
    def test_unknown_logical_name(self, project):
        report = check(project, BASELINE.replace("augment: randaug", "root: {data: FOLDS}"))
        assert "blab.local.json" in messages(report)

    def test_missing_path(self, project):
        project.local["data"] = {"FOLDS": "/nowhere/folds.csv"}
        report = check(project, BASELINE.replace("augment: randaug", "root: {data: FOLDS}"))
        assert "このマシンにありません" in messages(report)

    def test_resolves_inside_a_child_component(self, project, tmp_path):
        """子 component の引数に書かれた `{data:}` も辿る。"""
        csv = tmp_path / "folds.csv"
        csv.write_text("a,b\n", encoding="utf-8")
        project.local["data"] = {"FOLDS": str(csv)}
        report = check(project, BASELINE.replace("augment: randaug", "root: {data: FOLDS}"))
        assert report.ok, messages(report)
        assert report.data == {"FOLDS": csv}

    def test_relative_path_is_from_the_project_root(self, project):
        (project.root / "splits").mkdir()
        (project.root / "splits" / "fold5.csv").write_text("a\n", encoding="utf-8")
        project.local["data"] = {"FOLDS": "splits/fold5.csv"}
        report = check(project, BASELINE.replace("augment: randaug", "root: {data: FOLDS}"))
        assert report.ok, messages(report)
        assert report.data["FOLDS"] == project.root / "splits" / "fold5.csv"


class TestRequireTags:
    def _tag(self, project, id: str, *tags: str):
        from blab.components import Meta

        meta = Meta.load(project, id)
        meta.tags = list(tags)
        meta.save(project)

    def test_satisfied_anywhere_in_the_tree(self, project):
        """root が持つのではなく、**木の中に 1 つ以上**（B-10 の決定）。"""
        self._tag(project, "resnet18", "model")
        project.config["require_tags"] = ["model"]
        report = check(project, BASELINE)
        assert report.ok, messages(report)

    def test_missing_tag_is_an_error(self, project):
        project.config["require_tags"] = ["model"]
        report = check(project, BASELINE)
        assert "require_tags" in messages(report)

    def test_root_tag_alone_does_not_satisfy_a_child_requirement(self, project):
        self._tag(project, "standard_trainer", "trainer")
        project.config["require_tags"] = ["model"]
        assert not check(project, BASELINE).ok


def test_raise_if_failed_lists_everything(project):
    from blab.errors import BlabError

    report = check(project, BASELINE.replace("use: resnet18", "use: nope"))
    with pytest.raises(BlabError, match="事前検証に失敗"):
        report.raise_if_failed()

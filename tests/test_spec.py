"""Phase 3 — 実験 YAML の文法と `--set`。"""

from __future__ import annotations

import pytest
import yaml

from blab.errors import BlabError
from blab.spec import (
    DataRef,
    Node,
    apply_overrides,
    iter_nodes,
    load_experiment,
    parse_document,
    parse_override,
    parse_value,
)
from conftest import BASELINE, write_experiment


def parse(text: str):
    return parse_document(yaml.safe_load(text))


class TestGrammar:
    def test_use_mapping_is_a_reference(self):
        experiment = parse(BASELINE)
        assert experiment.run.id == "standard_trainer"
        assert experiment.run.args["epochs"] == 10
        assert isinstance(experiment.run.args["dataset"], Node)
        assert experiment.run.args["dataset"].id == "cifar100"

    def test_string_shorthand_is_not_a_reference(self):
        """`metric: top1_accuracy` は普通の文字列引数（design.md §5.1 / A-3 の決定）。

        参照とみなすと、component を新規作成した瞬間に既存 YAML の意味が黙って変わる。
        """
        experiment = parse(BASELINE + "  metric: top1_accuracy\n")
        assert experiment.run.args["metric"] == "top1_accuracy"

    def test_references_nest_at_any_depth(self):
        experiment = parse(
            BASELINE
            + """
  transforms:
    - {use: flip, p: 0.5}
    - {use: randaug}
  metrics:
    acc: {use: top1}
"""
        )
        args = experiment.run.args
        assert [n.id for n in args["transforms"]] == ["flip", "randaug"]
        assert args["transforms"][0].path == "run.transforms[0]"
        assert args["metrics"]["acc"].path == "run.metrics.acc"

    def test_data_reference(self):
        experiment = parse(BASELINE + "  folds: {data: FOLD_SPLIT_CSV}\n")
        folds = experiment.run.args["folds"]
        assert isinstance(folds, DataRef) and folds.name == "FOLD_SPLIT_CSV"

    def test_raw_escapes_reserved_keys(self):
        experiment = parse(BASELINE + "  cfg: {$raw: {use: hello, data: world}}\n")
        # 中身は走査されず、そのままの辞書として渡る。
        assert experiment.run.args["cfg"] == {"use": "hello", "data": "world"}

    def test_dollar_keys_are_reserved(self):
        with pytest.raises(BlabError, match="予約"):
            parse(BASELINE + "  cfg: {$ref: 1}\n")

    def test_raw_must_be_alone(self):
        with pytest.raises(BlabError, match=r"\$raw は単独"):
            parse(BASELINE + "  cfg: {$raw: {a: 1}, b: 2}\n")

    def test_use_must_be_a_string(self):
        with pytest.raises(BlabError, match="文字列でなければ"):
            parse(BASELINE + "  cfg: {use: {a: 1}}\n")

    def test_data_must_be_alone(self):
        with pytest.raises(BlabError, match="論理名の文字列 1 つ"):
            parse(BASELINE + "  cfg: {data: X, extra: 1}\n")

    def test_use_and_data_together_warns(self):
        warnings: list[str] = []
        value = parse_value({"use": "flip", "data": 3}, "run.x", warnings)
        assert isinstance(value, Node) and value.id == "flip"
        assert warnings and "$raw" in warnings[0]

    def test_iter_nodes_walks_the_whole_tree(self):
        experiment = parse(BASELINE + "  transforms: [{use: flip}]\n")
        assert sorted(n.id for n in iter_nodes(experiment.run)) == [
            "cifar100",
            "flip",
            "resnet18",
            "standard_trainer",
        ]


class TestDocument:
    def test_requires_experiment(self):
        with pytest.raises(BlabError, match="experiment"):
            parse("run: {use: a}\n")

    def test_requires_run(self):
        with pytest.raises(BlabError, match="run"):
            parse("experiment: x\n")

    def test_run_must_be_a_reference(self):
        with pytest.raises(BlabError, match="root component"):
            parse("experiment: x\nrun: 3\n")

    def test_unknown_top_level_key_is_a_typo(self):
        with pytest.raises(BlabError, match="experimnt"):
            parse("experimnt: x\nexperiment: y\nrun: {use: a}\n")

    def test_rejects_other_schema_version(self):
        with pytest.raises(BlabError, match="schema_version"):
            parse("schema_version: 1\nexperiment: x\nrun: {use: a}\n")

    def test_group_and_name(self):
        experiment = parse("experiment: x\ngroup: cv5\nname: fold0\nrun: {use: a}\n")
        assert (experiment.group, experiment.name) == ("cv5", "fold0")


class TestOverrides:
    def test_parses_yaml_scalars(self):
        assert parse_override("run.epochs=3")[1] == 3
        assert parse_override("run.flag=true")[1] is True
        assert parse_override("run.x=null")[1] is None
        assert parse_override("run.xs=[1, 2]")[1] == [1, 2]

    def test_scientific_notation_is_a_number(self):
        """YAML 1.1 は `1e-5` を float と認めないが、書いた人は数のつもり。"""
        assert parse_override("run.lr=1e-5")[1] == pytest.approx(1e-5)

    def test_quoting_keeps_a_string(self):
        assert parse_override('run.tag="1e-5"')[1] == "1e-5"

    def test_applies_to_the_document(self):
        doc = yaml.safe_load(BASELINE)
        applied = apply_overrides(doc, ["run.epochs=3", "run.dataset.augment=none"])
        assert doc["run"]["epochs"] == 3
        assert doc["run"]["dataset"]["augment"] == "none"
        assert applied == {"run.epochs": 3, "run.dataset.augment": "none"}

    def test_indexes_into_lists(self):
        doc = yaml.safe_load(BASELINE + "  transforms: [{use: flip, p: 0.5}]\n")
        apply_overrides(doc, ["run.transforms[0].p=0.9"])
        assert doc["run"]["transforms"][0]["p"] == 0.9

    def test_can_add_a_new_argument(self):
        """YAML に無い引数も渡せる。タイポは事前検証がシグネチャと照合して捕まえる。"""
        doc = yaml.safe_load(BASELINE)
        apply_overrides(doc, ["run.seed=7"])
        assert doc["run"]["seed"] == 7

    def test_cannot_swap_a_component(self):
        """値の上書きだけを許す（B-9 の決定）。"""
        doc = yaml.safe_load(BASELINE)
        with pytest.raises(BlabError, match="差し替え"):
            apply_overrides(doc, ["run.model.use=resnet50"])

    def test_missing_intermediate_path_is_an_error(self):
        doc = yaml.safe_load(BASELINE)
        with pytest.raises(BlabError, match="YAML にありません"):
            apply_overrides(doc, ["run.nope.deep=1"])

    def test_out_of_range_index_is_an_error(self):
        doc = yaml.safe_load(BASELINE + "  transforms: [{use: flip}]\n")
        with pytest.raises(BlabError, match="範囲外"):
            apply_overrides(doc, ["run.transforms[3].p=0.9"])

    def test_requires_equals(self):
        with pytest.raises(BlabError, match="<パス>=<値>"):
            parse_override("run.lr")


class TestLoadExperiment:
    def test_records_what_was_overridden(self, project):
        path = write_experiment(project, "baseline", BASELINE)
        experiment = load_experiment(path, overrides=["run.epochs=3"])
        assert experiment.run.args["epochs"] == 3
        assert experiment.overrides == {"run.epochs": 3}

    def test_cli_name_and_group_win(self, project):
        path = write_experiment(project, "baseline", BASELINE)
        experiment = load_experiment(path, name="fold0", group="cv5")
        assert (experiment.name, experiment.group) == ("fold0", "cv5")

    def test_syntax_error_points_at_the_file(self, project):
        path = write_experiment(project, "bad", "experiment: x\nrun: {use: a\n")
        with pytest.raises(BlabError, match="構文エラー"):
            load_experiment(path)

    def test_missing_file(self, project):
        with pytest.raises(BlabError, match="読めません"):
            load_experiment(project.experiments_dir / "nope.yaml")

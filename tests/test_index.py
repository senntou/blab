"""Phase 7 — group の集計は保存せず、読むときに導出する（layout.md §6.2）。"""

from __future__ import annotations

import json

import pytest
import yaml

from blab.aggregate import aggregate
from blab.index import find, is_trashed, runs_using, scan, walk
from blab.run import STATUS_FINISHED
from blab.runner import execute
from blab.spec import parse_document
from conftest import BASELINE


def go(project, text: str = BASELINE):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False)


class TestAggregate:
    def test_mean_and_sample_std(self):
        stats = aggregate([{"acc": 1.0}, {"acc": 2.0}, {"acc": 3.0}])
        assert stats["acc"].mean == pytest.approx(2.0)
        assert stats["acc"].std == pytest.approx(1.0)  # n-1
        assert stats["acc"].n == 3

    def test_single_run_has_no_std(self):
        """n=1 で std を 0 と書くと「ばらつきが無い」という嘘になる。"""
        assert aggregate([{"acc": 1.0}])["acc"].std is None

    def test_non_numeric_keys_are_dropped(self):
        assert "note" not in aggregate([{"acc": 1.0, "note": "hi"}])

    def test_mixed_types_are_dropped(self):
        assert "acc" not in aggregate([{"acc": 1.0}, {"acc": "bad"}])

    def test_booleans_are_not_numbers(self):
        assert "flag" not in aggregate([{"flag": True}, {"flag": False}])

    def test_empty(self):
        assert aggregate([]) == {}


class TestScan:
    def test_finds_experiments_groups_and_runs(self, project):
        go(project, BASELINE + "\ngroup: cv5\n")
        go(project)
        nodes = scan(project.runs_dir())
        kinds = sorted(n.kind for n in walk(nodes))
        assert kinds == ["experiment", "group", "run", "run"]

    def test_group_aggregation_is_derived(self, project):
        for _ in range(3):
            go(project, BASELINE + "\ngroup: cv5\n")
        group = next(n for n in walk(scan(project.runs_dir())) if n.kind == "group")
        stats = group.aggregate()
        assert stats["acc"].n == 3
        # 集計はファイルに書かない。
        assert not (group.path / "summary.json").exists()

    def test_aggregation_stays_consistent_when_a_run_is_removed(self, project):
        import shutil

        for _ in range(3):
            go(project, BASELINE + "\ngroup: cv5\n")
        group = next(n for n in walk(scan(project.runs_dir())) if n.kind == "group")
        shutil.rmtree(group.leaf_runs()[0].path)
        group = next(n for n in walk(scan(project.runs_dir())) if n.kind == "group")
        assert group.aggregate()["acc"].n == 2

    def test_only_finished_runs_are_aggregated(self, project):
        go(project, BASELINE + "\ngroup: cv5\n")
        group = next(n for n in walk(scan(project.runs_dir())) if n.kind == "group")
        run = group.leaf_runs()[0]
        meta = json.loads((run.path / "meta.json").read_text())
        meta["status"] = "failed"
        (run.path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        group = next(n for n in walk(scan(project.runs_dir())) if n.kind == "group")
        assert group.aggregate() == {}

    def test_directories_without_meta_are_not_nodes(self, project):
        go(project)
        (project.runs_dir() / "stray").mkdir()
        assert all(n.path.name != "stray" for n in walk(scan(project.runs_dir())))

    def test_stale_running_run(self, project):
        outcome = go(project)
        meta = json.loads((outcome.run.path / "meta.json").read_text())
        meta["status"] = "running"
        meta["heartbeat_at"] = "2020-01-01T00:00:00+09:00"
        (outcome.run.path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        node = find(project.runs_dir(), outcome.run.path.name)
        assert node.status == "stale"


class TestFind:
    def test_by_short_id(self, project):
        outcome = go(project)
        assert find(project.runs_dir(), outcome.run.id[-4:].lower()).id == outcome.run.id

    def test_by_ulid(self, project):
        outcome = go(project)
        assert find(project.runs_dir(), outcome.run.id).id == outcome.run.id

    def test_missing(self, project):
        assert find(project.runs_dir(), "nope") is None


class TestIsTrashed:
    def test_run_directly_under_trash(self, project):
        root = project.runs_dir()
        assert is_trashed(root, root / "cifar100" / "_trash" / "20260101-000000_abcd")

    def test_the_trash_group_itself(self, project):
        root = project.runs_dir()
        assert is_trashed(root, root / "cifar100" / "_trash")

    def test_ordinary_run_is_not_trashed(self, project):
        root = project.runs_dir()
        assert not is_trashed(root, root / "cifar100" / "20260101-000000_abcd")

    def test_ordinary_group_is_not_trashed(self, project):
        root = project.runs_dir()
        assert not is_trashed(root, root / "cifar100" / "cv5" / "20260101-000000_abcd")


def test_reverse_lookup_by_component_hash(project):
    """この component を使った全 run を引く（layout.md §9）。"""
    a = go(project)
    b = go(project)
    doc = yaml.safe_load((a.run.path / "resolved.yaml").read_text())
    hash = doc["run"]["children"]["model"]["hash"]

    using = runs_using(project.runs_dir())
    names = {node.path.name for _, node in using[hash]}
    assert names == {a.run.path.name, b.run.path.name}

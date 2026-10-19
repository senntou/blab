"""Phase 7 — group の集計は保存せず、読むときに導出する（layout.md §6.2）。"""

from __future__ import annotations

import json

import pytest
import yaml

from blab.aggregate import aggregate
from blab.index import FileCache, ScanCache, find, is_trashed, row, runs_using, scan, walk
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


def _touch_forward(path, seconds: int = 10) -> None:
    """mtime を確実に進める（同じ時刻の書き込みを「変わっていない」と見なさないため）。"""
    import os

    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))


class TestFileCache:
    """UI の再走査で、変わっていないファイルを解析し直さない（layout.md §9）。"""

    def test_reuses_value_until_file_changes(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text("1")
        calls = []

        def derive(p):
            calls.append(p)
            return p.read_text()

        cache = FileCache()
        assert cache.get(path, "t", derive) == "1"
        assert cache.get(path, "t", derive) == "1"
        assert len(calls) == 1

        path.write_text("22")
        _touch_forward(path)
        assert cache.get(path, "t", derive) == "22"
        assert len(calls) == 2

    def test_missing_file_gives_default(self, tmp_path):
        cache = FileCache()
        assert cache.get(tmp_path / "nope", "t", lambda p: 1, default="d") == "d"
        assert len(cache) == 0

    def test_retain_drops_entries_of_vanished_nodes(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        for name in ("a", "b"):
            (tmp_path / name / "x").write_text("x")
        cache = FileCache()
        for name in ("a", "b"):
            cache.get(tmp_path / name / "x", "t", lambda p: 1)
        cache.retain({tmp_path / "a"})
        assert len(cache) == 1


class TestScanCache:
    """変わっていないディレクトリのファイルは開かない（layout.md §9）。"""

    def test_rows_and_reverse_lookup_match_uncached(self, project, tmp_path):
        go(project, BASELINE + "\ngroup: cv5\n")
        go(project)
        root = project.runs_dir()
        plain = [row(n, root) for n in walk(scan(root)) if n.kind in ("run", "group")]
        using = {h: [(u, n.path) for u, n in v] for h, v in runs_using(root).items()}
        cache = ScanCache(tmp_path / "cache.json")
        for _ in range(2):  # 2 回目はキャッシュから
            nodes = scan(root, cache=cache)
            cached = [row(n, root) for n in walk(nodes) if n.kind in ("run", "group")]
            assert cached == plain
            got = runs_using(root, nodes)
            assert {h: [(u, n.path) for u, n in v] for h, v in got.items()} == using
        # 保存したものを読み直しても同じ（起動し直した UI）。
        cache.save()
        reloaded = ScanCache(tmp_path / "cache.json")
        assert len(reloaded) == len(cache) > 0
        nodes = scan(root, cache=reloaded)
        assert [row(n, root) for n in walk(nodes) if n.kind in ("run", "group")] == plain

    def test_unchanged_run_files_are_not_opened(self, project, monkeypatch):
        go(project)
        root = project.runs_dir()
        cache = ScanCache()
        rows = [row(n, root) for n in walk(scan(root, cache=cache)) if n.kind == "run"]

        import blab.index as index_mod

        opened = []
        real = index_mod.read_json
        monkeypatch.setattr(
            index_mod, "read_json", lambda p, **kw: opened.append(p) or real(p, **kw)
        )
        again = [row(n, root) for n in walk(scan(root, cache=cache)) if n.kind == "run"]
        assert again == rows
        assert opened == []

    def test_rewritten_summary_is_read_again(self, project):
        """blab の書き込みは rename なので、ディレクトリの mtime が変わる。"""
        from blab.io import write_json_atomic

        outcome = go(project)
        root = project.runs_dir()
        cache = ScanCache()
        run = next(n for n in walk(scan(root, cache=cache)) if n.kind == "run")
        assert run.summary["acc"] != 0.5
        write_json_atomic(outcome.run.path / "summary.json", {"acc": 0.5})
        _touch_forward(outcome.run.path)
        run = next(n for n in walk(scan(root, cache=cache)) if n.kind == "run")
        assert run.summary == {"acc": 0.5}

    def test_added_doc_and_note_are_seen(self, project):
        from blab.docs import attach
        from blab.io import write_json_atomic

        outcome = go(project)
        root = project.runs_dir()
        cache = ScanCache()
        run = next(n for n in walk(scan(root, cache=cache)) if n.kind == "run")
        assert row(run, root)["docs"] == []
        attach(outcome.run.path, text="# hi", name="README.md")
        meta = json.loads((outcome.run.path / "meta.json").read_text())
        write_json_atomic(outcome.run.path / "meta.json", {**meta, "notes": "見た"})
        _touch_forward(outcome.run.path)
        got = row(next(n for n in walk(scan(root, cache=cache)) if n.kind == "run"), root)
        assert got["docs"] == ["README.md"]
        assert got["notes"] == "見た"

    def test_vanished_runs_are_dropped(self, project):
        import shutil

        outcome = go(project)
        root = project.runs_dir()
        cache = ScanCache()
        scan(root, cache=cache)
        before = len(cache)
        shutil.rmtree(outcome.run.path)
        scan(root, cache=cache)
        assert len(cache) == before - 1

    def test_broken_cache_file_is_ignored(self, project, tmp_path):
        go(project)
        path = tmp_path / "cache.json"
        path.write_text("{not json")
        cache = ScanCache(path)
        assert [n.kind for n in walk(scan(project.runs_dir(), cache=cache))] == [
            "experiment",
            "run",
        ]

    def test_summary_of_running_run_is_not_cached(self, project):
        """NFS では stat が古いことがあるので、書き換わる途中の summary は mtime に頼らない。"""
        import os
        from datetime import datetime, timezone

        outcome = go(project)
        meta_path = outcome.run.path / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["status"] = "running"
        meta["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        root = project.runs_dir()
        cache = ScanCache()
        run = next(n for n in walk(scan(root, cache=cache)) if n.kind == "run")
        before = run.summary
        # 同じ mtime・同じ大きさで中身だけ変わった（stat が古いのと同じ状況）。
        path = outcome.run.path / "summary.json"
        st = os.stat(path)
        text = json.dumps({"acc": 0.5})
        path.write_text(text.ljust(st.st_size) if len(text) < st.st_size else text)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        run = next(n for n in walk(scan(root, cache=cache)) if n.kind == "run")
        assert run.summary == {"acc": 0.5} != before

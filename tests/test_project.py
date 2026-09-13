"""Phase 1 — プロジェクト / グローバル索引 / ログルートの決まり方。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blab.errors import BlabError
from blab.project import GlobalIndex, Project, find_root, init


def test_init_writes_project_and_dirs(tmp_path):
    project, created = init(tmp_path / "proj", name="cifar-research")
    assert created
    assert project.name == "cifar-research"
    assert len(project.uid) == 26  # ULID
    assert project.components_dir.is_dir()
    assert project.experiments_dir.is_dir()

    config = json.loads((project.root / "blab.json").read_text(encoding="utf-8"))
    # 既定値も明示して書く（挙動を隠さない）。
    assert config["components_dir"] == "components"
    assert config["experiments_dir"] == "experiments"


def test_init_is_idempotent_and_keeps_uid(tmp_path):
    first, created = init(tmp_path / "proj")
    assert created
    second, created_again = init(tmp_path / "proj")
    assert not created_again
    assert second.uid == first.uid


def test_init_writes_gitignore_without_duplicating(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".gitignore").write_text("*.pyc\n/runs/\n", encoding="utf-8")
    init(root)
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count("/runs/") == 1
    assert "/.blab/" in lines
    assert "/blab.local.json" in lines


def test_project_uid_survives_directory_rename(tmp_path):
    """ディレクトリ名を同一性の根拠にしない（design.md §3.2）。"""
    project, _ = init(tmp_path / "before")
    uid = project.uid
    (tmp_path / "before").rename(tmp_path / "after")
    assert Project.at(tmp_path / "after").uid == uid


def test_find_root_walks_up(project):
    deep = project.components_dir / "standard_trainer"
    assert find_root(deep) == project.root


def test_load_without_project_explains(tmp_path):
    with pytest.raises(BlabError, match="blab.json が見つかりません"):
        Project.load(tmp_path)


def test_rejects_other_schema_version(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "blab.json").write_text(
        json.dumps({"schema_version": 1, "project_uid": "X", "project": "old"}),
        encoding="utf-8",
    )
    with pytest.raises(BlabError, match="schema_version"):
        Project.at(root)


class TestRunsDir:
    """ログルートの優先順位（layout.md §1.1）。"""

    def test_default_is_runs_under_project(self, project):
        assert project.runs_dir() == project.root / "runs"

    def test_blab_json_wins_over_default(self, project):
        project.config["runs_dir"] = "logs"
        assert project.runs_dir() == (project.root / "logs").resolve()

    def test_local_wins_over_blab_json(self, project, tmp_path):
        project.config["runs_dir"] = "logs"
        project.local["runs_dir"] = str(tmp_path / "big-disk")
        assert project.runs_dir() == tmp_path / "big-disk"

    def test_cli_wins_over_local(self, project, tmp_path):
        project.local["runs_dir"] = str(tmp_path / "big-disk")
        assert project.runs_dir(tmp_path / "scratch") == tmp_path / "scratch"

    def test_env_wins_over_everything(self, project, tmp_path, monkeypatch):
        monkeypatch.setenv("BLAB_RUNS", str(tmp_path / "cluster"))
        project.local["runs_dir"] = str(tmp_path / "big-disk")
        assert project.runs_dir(tmp_path / "scratch") == tmp_path / "cluster"


class TestGlobalIndex:
    def test_init_registers(self, tmp_path):
        project, _ = init(tmp_path / "proj", name="seg-baseline")
        assert GlobalIndex.load().by_name("seg-baseline") == project.root

    def test_missing_name_is_none(self, tmp_path):
        init(tmp_path / "proj", name="a")
        assert GlobalIndex.load().by_name("nope") is None

    def test_duplicate_names_are_an_error(self, tmp_path):
        init(tmp_path / "one", name="same")
        init(tmp_path / "two", name="same")
        with pytest.raises(BlabError, match="2 件あります"):
            GlobalIndex.load().by_name("same")

    def test_is_only_a_cache(self, tmp_path):
        """壊れても再登録で直る（真実は各 blab.json）。"""
        project, _ = init(tmp_path / "proj", name="x")
        index_path = GlobalIndex.load().path
        index_path.write_text("{ broken", encoding="utf-8")
        assert GlobalIndex.load().projects == {}
        GlobalIndex.load().register(project.uid, project.name, project.root)
        assert GlobalIndex.load().by_name("x") == project.root

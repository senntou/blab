"""Phase 8 / 10 — `blab tag` と、run からの再実行。"""

from __future__ import annotations

import shutil

import pytest
import yaml

from blab.components import Meta, Resolver, hash_dir, parse_ref
from blab.errors import BlabError
from blab.replay import prepare
from blab.run import STATUS_FINISHED
from blab.runner import execute
from blab.spec import parse_document
from blab.tag import tag
from conftest import BASELINE, MODEL, write_component


def go(project, text: str = BASELINE, **kw):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False, **kw)


class TestTag:
    def test_promotes_into_git(self, project):
        hash, dest = tag(project, "resnet18", "v1", note="初版")
        assert dest == project.labeled_dir / "resnet18" / "v1"
        assert hash_dir(dest) == hash
        assert Meta.load(project, "resnet18").label_hash("v1") == hash

    def test_does_not_change_the_working_copy_hash(self, project):
        """ラベルを本体の外に置いた理由そのもの。"""
        before = hash_dir(project.components_dir / "resnet18")
        tag(project, "resnet18", "v1")
        assert hash_dir(project.components_dir / "resnet18") == before

    def test_labels_are_immutable(self, project):
        tag(project, "resnet18", "v1")
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        with pytest.raises(BlabError, match="ラベルは不変"):
            tag(project, "resnet18", "v1")

    def test_from_run_restores_a_past_version(self, project):
        """キャッシュを消しても、run に実体が焼き込まれているので復元できる。"""
        outcome = go(project)
        recorded = yaml.safe_load(
            (outcome.run.path / "resolved.yaml").read_text()
        )["run"]["children"]["model"]["hash"]

        # 作業コピーを書き換え、キャッシュも消す。
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        shutil.rmtree(project.cache_dir)

        hash, dest = tag(project, "resnet18", "good", from_run=outcome.run.path)
        assert hash == recorded
        assert hash_dir(dest) == recorded

    def test_from_run_rejects_a_tampered_run(self, project):
        outcome = go(project)
        (outcome.run.path / "components" / "resnet18" / "main.py").write_text(
            "import blab\n", encoding="utf-8"
        )
        with pytest.raises(BlabError, match="食い違"):
            tag(project, "resnet18", "v1", from_run=outcome.run.path)

    def test_from_run_says_what_the_run_actually_used(self, project):
        outcome = go(project)
        with pytest.raises(BlabError, match="使ったのは"):
            tag(project, "nothing_here", "v1", from_run=outcome.run.path)

    def test_tagged_version_resolves_by_label(self, project):
        tag(project, "resnet18", "v1")
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        shutil.rmtree(project.cache_dir, ignore_errors=True)
        # 実体は components/.frozen/ にしか残っていない。
        resolved = Resolver(project).resolve(parse_ref("resnet18@v1"))
        assert resolved.label == "v1"


class TestReplay:
    def test_uses_the_baked_code_not_the_working_copy(self, project):
        """component を編集したあとでも、過去 run は当時のコードで再実行される。"""
        first = go(project)
        recorded = yaml.safe_load(
            (first.run.path / "resolved.yaml").read_text()
        )["run"]["children"]["model"]["hash"]

        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )

        replay = prepare(first.run.path)
        second = execute(
            project, replay.experiment, capture=False, report=replay.report,
            recorded=replay.recorded,
        )
        again = yaml.safe_load(
            (second.run.path / "resolved.yaml").read_text()
        )["run"]["children"]["model"]["hash"]
        assert again == recorded
        assert second.status == STATUS_FINISHED

    def test_makes_a_new_run_and_leaves_the_original(self, project):
        first = go(project)
        before = (first.run.path / "summary.json").read_text()
        replay = prepare(first.run.path)
        second = execute(project, replay.experiment, capture=False, report=replay.report)
        assert second.run.path != first.run.path
        assert (first.run.path / "summary.json").read_text() == before

    def test_records_replay_of(self, project):
        import json

        first = go(project)
        replay = prepare(first.run.path)
        second = execute(
            project, replay.experiment, capture=False, report=replay.report,
            replay_of={"id": replay.source_id, "path": str(replay.source_path)},
        )
        meta = json.loads((second.run.path / "meta.json").read_text())
        assert meta["replay_of"]["id"] == first.run.id

    def test_works_without_the_project_registry(self, project, tmp_path):
        """プロジェクトを消したあとでも、run のディレクトリさえあれば再実行できる。"""
        first = go(project)
        portable = tmp_path / "received"
        shutil.copytree(first.run.path, portable)
        shutil.rmtree(project.components_dir)

        replay = prepare(portable)
        assert replay.report.ok, [f.message for f in replay.report.findings]
        outcome = execute(project, replay.experiment, capture=False, report=replay.report)
        assert outcome.status == STATUS_FINISHED

    def test_rejects_a_tampered_run(self, project):
        first = go(project)
        (first.run.path / "components" / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        replay = prepare(first.run.path)
        assert not replay.report.ok
        assert "改変" in "\n".join(f.message for f in replay.report.errors)

    def test_missing_components_directory(self, project):
        first = go(project)
        shutil.rmtree(first.run.path / "components")
        with pytest.raises(BlabError, match="自己完結"):
            prepare(first.run.path)

    def test_detects_runtime_argument_drift(self, project):
        """記録を再生するのではなく、コードを動かして一致を確かめる。"""
        first = go(project)
        replay = prepare(first.run.path)
        # 焼き込んだ dataset の n_classes を変える（データが変わった状況の模擬）。
        dataset = replay.report.root.args["dataset"]
        dataset.entry_obj.n_classes = 7
        original = dataset.entry_obj.__init__

        def patched(self, split, augment=None, root=None):
            original(self, split, augment, root)
            self.n_classes = 7

        dataset.entry_obj.__init__ = patched
        second = execute(
            project, replay.experiment, capture=False, report=replay.report,
            recorded=replay.recorded,
        )
        assert any("k" in m for m in second.mismatches), second.mismatches

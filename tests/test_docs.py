"""ノードに添付するドキュメント（layout.md §5.7）。

置き方（YAML の docs: / blab doc add / run.log_doc / 直接置く）がどれも同じ「ノード直下の
*.md」になり、UI の API から読めることを見る。
"""

from __future__ import annotations

import pytest
import yaml

from blab import docs
from blab.cli import main
from blab.errors import BlabError
from blab.preflight import preflight
from blab.runner import execute
from blab.spec import load_experiment, parse_document
from conftest import BASELINE, TRAINER, write_component, write_experiment


def go(project, text: str = BASELINE):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False)


class TestListDocs:
    def test_readme_comes_first_then_by_name(self, tmp_path):
        for name in ("b.md", "README.md", "a.md", "notes.txt", ".hidden.md"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        (tmp_path / "dir.md").mkdir()
        assert [d["name"] for d in docs.list_docs(tmp_path)] == ["README.md", "a.md", "b.md"]

    def test_missing_directory_is_empty(self, tmp_path):
        assert docs.list_docs(tmp_path / "nope") == []


class TestAttach:
    def test_copies_and_refuses_overwrite_without_force(self, tmp_path):
        node = tmp_path / "node"
        node.mkdir()
        src = tmp_path / "intent.md"
        src.write_text("# 目的", encoding="utf-8")
        assert docs.attach(node, src) == node / "intent.md"
        with pytest.raises(BlabError, match="既にあります"):
            docs.attach(node, src)
        src.write_text("# 目的 v2", encoding="utf-8")
        docs.attach(node, src, force=True)
        assert (node / "intent.md").read_text(encoding="utf-8") == "# 目的 v2"

    def test_text_defaults_to_readme(self, tmp_path):
        assert docs.attach(tmp_path, text="hi").name == "README.md"

    @pytest.mark.parametrize("name", ["../x.md", "a/b.md", ".x.md", "meta.json", "run.py"])
    def test_refuses_names_that_could_clobber_the_record(self, tmp_path, name):
        with pytest.raises(BlabError):
            docs.attach(tmp_path, text="x", name=name)

    def test_images_can_sit_next_to_the_docs(self, tmp_path):
        assert docs.attach(tmp_path, text="png", name="fig.png").name == "fig.png"


class TestExperimentYaml:
    def test_docs_are_copied_into_the_run(self, project):
        (project.experiments_dir / "intent.md").parent.mkdir(parents=True, exist_ok=True)
        (project.experiments_dir / "intent.md").write_text("# なぜ\n$x^2$", encoding="utf-8")
        path = write_experiment(project, "e", BASELINE + "docs: [intent.md]\n")
        outcome = execute(project, load_experiment(path), capture=False)
        assert (outcome.run.path / "intent.md").read_text(encoding="utf-8").startswith("# なぜ")

    def test_a_single_string_is_accepted(self, project):
        exp = parse_document(yaml.safe_load(BASELINE + "docs: intent.md\n"))
        assert exp.docs == ["intent.md"]

    def test_wrong_type_is_rejected(self):
        with pytest.raises(BlabError, match="docs"):
            parse_document(yaml.safe_load(BASELINE + "docs: {a: 1}\n"))

    def test_missing_doc_fails_preflight(self, project):
        path = write_experiment(project, "e", BASELINE + "docs: [missing.md]\n")
        report = preflight(project, load_experiment(path))
        assert not report.ok
        assert any("missing.md" in f.message for f in report.errors)

    def test_colliding_names_fail_preflight(self, project):
        for sub in ("a", "b"):
            (project.experiments_dir / sub).mkdir(parents=True, exist_ok=True)
            (project.experiments_dir / sub / "README.md").write_text("x", encoding="utf-8")
        path = write_experiment(project, "e", BASELINE + "docs: [a/README.md, b/README.md]\n")
        report = preflight(project, load_experiment(path))
        assert any("同じ名前" in f.message for f in report.errors)


class TestRunApi:
    def test_component_can_write_a_report(self, project):
        write_component(
            project,
            "standard_trainer",
            TRAINER.replace(
                'run.log_summary({"acc"',
                'run.log_doc("report.md", "# 結果\\n精度は $0.6$")\n        run.log_summary({"acc"',
            ),
            loops__py="def step(n):\n    return n\n",
        )
        outcome = go(project)
        assert outcome.status == "finished"
        assert "精度" in (outcome.run.path / "report.md").read_text(encoding="utf-8")


class TestCli:
    def test_add_ls_show_rm(self, project, tmp_path, capsys, monkeypatch):
        run = go(project).run.path
        experiment = run.parent
        src = tmp_path / "plan.md"
        src.write_text("# 計画", encoding="utf-8")

        main(["--dir", str(project.root), "doc", "add", str(experiment), str(src)])
        assert (experiment / "plan.md").is_file()

        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("# 標準入力から"))
        main(["--dir", str(project.root), "doc", "add", str(run), "-"])
        assert (run / "README.md").read_text(encoding="utf-8") == "# 標準入力から"
        capsys.readouterr()

        # 短 ID でも引ける
        main(["--dir", str(project.root), "doc", "ls", run.name.split("_")[1]])
        assert "README.md" in capsys.readouterr().out

        main(["--dir", str(project.root), "doc", "show", str(run)])
        assert "標準入力から" in capsys.readouterr().out

        main(["--dir", str(project.root), "show", str(run)])
        assert "README.md" in capsys.readouterr().out

        main(["--dir", str(project.root), "doc", "rm", str(experiment), "plan.md"])
        assert not (experiment / "plan.md").exists()

    def test_add_refuses_overwrite(self, project, tmp_path):
        run = go(project).run.path
        (run / "README.md").write_text("old", encoding="utf-8")
        src = tmp_path / "README.md"
        src.write_text("new", encoding="utf-8")
        with pytest.raises(SystemExit):
            main(["--dir", str(project.root), "doc", "add", str(run), str(src)])
        main(["--dir", str(project.root), "doc", "add", str(run), str(src), "-f"])
        assert (run / "README.md").read_text(encoding="utf-8") == "new"


class TestServer:
    @pytest.fixture
    def client(self, project):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from blab.server import create_app

        self.run = go(project, BASELINE + "group: cv5\n").run.path
        (self.run / "README.md").write_text("# run の意図", encoding="utf-8")
        (self.run.parent / "group.md").write_text("# group", encoding="utf-8")
        (self.run.parent.parent / "README.md").write_text("# experiment", encoding="utf-8")
        return TestClient(create_app(project))

    def test_detail_lists_docs(self, client):
        detail = client.get("/api/nodes/cifar100/cv5/" + self.run.name + "/detail").json()
        assert [d["name"] for d in detail["docs"]] == ["README.md"]
        group = client.get("/api/nodes/cifar100/cv5/detail").json()
        assert [d["name"] for d in group["docs"]] == ["group.md"]

    def test_rows_and_experiments_mark_docs(self, client):
        rows = client.get("/api/nodes").json()["rows"]
        assert {r["kind"]: r["docs"] for r in rows} == {"run": ["README.md"], "group": ["group.md"]}
        assert client.get("/api/experiments").json()["experiments"][0]["docs"] == ["README.md"]

    def test_docs_endpoint_for_the_experiment_page(self, client):
        doc = client.get("/api/nodes/cifar100/docs").json()
        assert doc["kind"] == "experiment"
        assert [d["name"] for d in doc["docs"]] == ["README.md"]

    def test_reads_a_doc(self, client):
        res = client.get("/api/nodes/cifar100/doc", params={"name": "README.md"})
        assert res.json()["text"] == "# experiment"

    @pytest.mark.parametrize("name", ["meta.json", "cv5/group.md", "../x.md"])
    def test_only_top_level_markdown_is_readable(self, client, name):
        res = client.get("/api/nodes/cifar100/doc", params={"name": name})
        assert res.status_code == 400

    def test_katex_is_bundled(self, client):
        assert client.get("/vendor/katex/katex.min.js").status_code == 200
        assert client.get("/vendor/katex/katex.min.css").status_code == 200
        assert client.get("/vendor/katex/fonts/KaTeX_Main-Regular.woff2").status_code == 200

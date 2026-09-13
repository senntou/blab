"""CLI 全体。"""

from __future__ import annotations

from pathlib import Path

import pytest

from blab.cli import main
from conftest import BASELINE, write_experiment


def run(argv: list[str]) -> None:
    main(argv)


class TestInit:
    def test_creates_a_project(self, tmp_path, capsys):
        run(["init", str(tmp_path / "proj"), "--name", "cifar-research"])
        assert (tmp_path / "proj" / "blab.json").is_file()
        assert "cifar-research" in capsys.readouterr().out

    def test_second_time_says_so(self, tmp_path, capsys):
        run(["init", str(tmp_path / "proj")])
        capsys.readouterr()
        run(["init", str(tmp_path / "proj")])
        assert "既にプロジェクト" in capsys.readouterr().out


class TestLink:
    def test_registers(self, tmp_path, capsys):
        run(["init", str(tmp_path / "other"), "--name", "seg"])
        capsys.readouterr()
        run(["link", str(tmp_path / "other")])
        assert "索引に登録" in capsys.readouterr().out

    def test_non_project_fails(self, tmp_path):
        with pytest.raises(SystemExit):
            run(["link", str(tmp_path)])


class TestNew:
    def test_scaffolds_a_component(self, project, capsys):
        run(["--dir", str(project.root), "new", "my_model"])
        path = project.components_dir / "my_model"
        assert (path / "main.py").is_file()
        assert (path / "README.ja.md").is_file()
        assert "@blab.entry" in (path / "main.py").read_text(encoding="utf-8")

    def test_scaffold_passes_check(self, project, capsys):
        run(["--dir", str(project.root), "new", "my_thing"])
        text = (project.components_dir / "my_thing" / "main.py").read_text(encoding="utf-8")
        assert "class MyThing" in text

    def test_refuses_to_overwrite(self, project):
        with pytest.raises(SystemExit):
            run(["--dir", str(project.root), "new", "resnet18"])

    def test_rejects_a_bad_id(self, project):
        with pytest.raises(SystemExit):
            run(["--dir", str(project.root), "new", "my-model"])


class TestComponents:
    def test_lists(self, project, capsys):
        run(["--dir", str(project.root), "components"])
        out = capsys.readouterr().out
        assert "resnet18" in out and "standard_trainer" in out

    def test_filters_by_tag(self, project, capsys):
        from blab.components import Meta

        meta = Meta.load(project, "resnet18")
        meta.tags = ["model"]
        meta.save(project)
        run(["--dir", str(project.root), "components", "--tags", "model"])
        out = capsys.readouterr().out
        assert "resnet18" in out and "standard_trainer" not in out


class TestCheck:
    def test_passing_configuration(self, project, capsys):
        path = write_experiment(project, "baseline", BASELINE)
        run(["--dir", str(project.root), "check", str(path)])
        out = capsys.readouterr().out
        assert "事前検証を通りました" in out
        # 構成の木を出す。
        assert "standard_trainer" in out and "dataset: cifar100" in out

    def test_failing_configuration_exits_nonzero(self, project, capsys):
        path = write_experiment(project, "bad", BASELINE.replace("epochs", "epocs"))
        with pytest.raises(SystemExit) as exc:
            run(["--dir", str(project.root), "check", str(path)])
        assert exc.value.code != 0

    def test_json_output(self, project, capsys):
        import json

        path = write_experiment(project, "baseline", BASELINE)
        with pytest.raises(SystemExit) as exc:
            run(["--dir", str(project.root), "check", str(path), "--json"])
        assert exc.value.code == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["ok"] and doc["experiment"] == "cifar100"

    def test_set_is_applied_and_recorded(self, project, capsys):
        import json

        path = write_experiment(project, "baseline", BASELINE)
        with pytest.raises(SystemExit):
            run([
                "--dir", str(project.root), "check", str(path),
                "--set", "run.epochs=3", "--json",
            ])
        doc = json.loads(capsys.readouterr().out)
        assert doc["overrides"] == {"run.epochs": 3}

    def test_shows_which_components_were_frozen(self, project, capsys):
        path = write_experiment(project, "baseline", BASELINE)
        run(["--dir", str(project.root), "check", str(path)])
        assert "今回凍結" in capsys.readouterr().out
        run(["--dir", str(project.root), "check", str(path)])
        assert "今回凍結" not in capsys.readouterr().out


class TestRunAndLs:
    def test_run_then_ls(self, project, capsys):
        path = write_experiment(project, "baseline", BASELINE)
        run(["--dir", str(project.root), "run", str(path), "--no-capture"])
        assert "finished" in capsys.readouterr().out

        run(["--dir", str(project.root), "ls"])
        out = capsys.readouterr().out
        assert "cifar100" in out and "finished" in out and "acc=" in out

    def test_group_aggregate_in_ls(self, project, capsys):
        path = write_experiment(project, "baseline", BASELINE)
        for _ in range(3):
            run(["--dir", str(project.root), "run", str(path), "--group", "cv5",
                 "--no-capture"])
        capsys.readouterr()
        run(["--dir", str(project.root), "ls"])
        out = capsys.readouterr().out
        assert "cv5/" in out and "(3 runs)" in out and "±" in out

    def test_show_run(self, project, capsys):
        path = write_experiment(project, "baseline", BASELINE)
        run(["--dir", str(project.root), "run", str(path), "--no-capture"])
        run_path = capsys.readouterr().out.split("finished  ")[1].splitlines()[0]
        run(["--dir", str(project.root), "show", run_path])
        out = capsys.readouterr().out
        assert "summary" in out and "resolved.yaml" in out

    def test_show_component(self, project, capsys):
        run(["--dir", str(project.root), "tag", "resnet18", "v1", "--note", "初版"])
        capsys.readouterr()
        run(["--dir", str(project.root), "show", "resnet18"])
        out = capsys.readouterr().out
        assert "v1" in out and "初版" in out

    def test_failing_run_exits_nonzero(self, project, capsys):
        from conftest import write_component

        write_component(
            project,
            "boom",
            "import blab\n\n@blab.entry\nclass Boom:\n"
            "    def __init__(self):\n        pass\n"
            "    def execute(self, run):\n        raise ValueError('x')\n",
        )
        path = write_experiment(project, "boom", "experiment: x\nrun: {use: boom}\n")
        with pytest.raises(SystemExit) as exc:
            run(["--dir", str(project.root), "run", str(path), "--no-capture"])
        assert exc.value.code != 0


def test_mv_moves_a_run_and_records_it(project, capsys):
    import json

    path = write_experiment(project, "baseline", BASELINE)
    run(["--dir", str(project.root), "run", str(path), "--no-capture"])
    run_path = capsys.readouterr().out.split("finished  ")[1].splitlines()[0]

    run(["--dir", str(project.root), "mv", run_path, "--group", "cv5"])
    moved = project.runs_dir() / "cifar100" / "cv5" / Path(run_path).name
    assert moved.is_dir()
    # **黙って履歴を書き換えない。**
    assert json.loads((moved / "meta.json").read_text())["moved_from"]["path"]


def test_ui_builds_an_app_without_serving(project):
    """`blab ui` は uvicorn を起動して戻らないので、CLI ではなく app 生成を見る。"""
    fastapi = pytest.importorskip("fastapi")
    from blab.server import create_app

    app = create_app(project)
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/api/status" in routes and "/api/nodes" in routes

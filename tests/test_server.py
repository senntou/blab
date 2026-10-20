"""Phase 9 — 閲覧層の API。**ディレクトリを読むだけ**（layout.md §9）。"""

from __future__ import annotations

import pytest
import yaml

from blab.runner import execute
from blab.spec import parse_document
from conftest import BASELINE, Scoped

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from blab.server import create_app  # noqa: E402


def go(project, text: str = BASELINE):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False)


@pytest.fixture
def client(project):
    go(project)
    return Scoped(TestClient(create_app(project)), project.uid)


@pytest.fixture
def rich(project):
    """run / group / ラベル付き component が揃った状態。"""
    go(project)
    for _ in range(3):
        go(project, BASELINE + "\ngroup: cv5\n")
    from blab.tag import tag

    tag(project, "resnet18", "v1", note="初版")
    return Scoped(TestClient(create_app(project)), project.uid)


class TestStatus:
    def test_reports_the_project(self, client, project):
        doc = client.get("/api/status").json()
        assert doc["project"] == project.name
        assert doc["project_uid"] == project.uid
        assert doc["n_runs"] == 1

    def test_experiments(self, client):
        doc = client.get("/api/experiments").json()
        assert [e["name"] for e in doc["experiments"]] == ["cifar100"]
        assert doc["experiments"][0]["n_runs"] == 1


class TestNodes:
    def test_columns_come_from_resolved_yaml(self, client):
        row = next(r for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        # component の版そのものが列になる（「どの版を使った run か」で並べたい）。
        assert row["config"]["run"].startswith("standard_trainer@")
        assert row["config"]["model"].startswith("resnet18@")
        assert row["config"]["run.epochs"] == 10
        # 実行時に決まった値も列になる。
        assert row["config"]["model.k"] == 100
        assert row["summary"]["acc"] == pytest.approx(0.6)

    def test_group_rows_carry_derived_aggregate(self, rich):
        rows = rich.get("/api/nodes").json()["rows"]
        group = next(r for r in rows if r["kind"] == "group")
        assert group["n_runs"] == 3
        assert group["aggregate"]["acc"]["n"] == 3

    def test_filter_by_experiment(self, client):
        assert client.get("/api/nodes", params={"experiment": "cifar100"}).json()["rows"]
        assert not client.get("/api/nodes", params={"experiment": "nope"}).json()["rows"]

    def test_filter_by_kind(self, rich):
        rows = rich.get("/api/nodes", params={"kind": "group"}).json()["rows"]
        assert rows and all(r["kind"] == "group" for r in rows)


class TestRunDetail:
    def test_includes_everything_the_view_needs(self, client):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        doc = client.get(f"/api/nodes/{path}/detail").json()
        assert doc["resolved"]["run"]["use"] == "standard_trainer"
        assert doc["env"]["python"]
        assert {c["id"] for c in doc["components"]} == {"standard_trainer", "cifar100", "resnet18"}
        assert doc["logs"] == [] or all("name" in l for l in doc["logs"])

    def test_baked_hash_is_the_recorded_one(self, client, project):
        """run を開くたびに焼き込みのソースを読んでハッシュし直さない。"""
        from blab.components import hash_dir

        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        doc = client.get(f"/api/nodes/{path}/detail").json()
        run_dir = project.runs_dir() / path
        for component in doc["components"]:
            assert component["hash"] == hash_dir(run_dir / "components" / component["id"])

    def test_component_readme_alone(self, client, project):
        (project.components_dir / "resnet18" / "README.md").write_text("# ResNet", encoding="utf-8")
        assert client.get("/api/components/resnet18/readme").json()["readme"] == "# ResNet"
        assert client.get("/api/components/cifar100/readme").json()["readme"] is None

    def test_metrics_are_columnar(self, client):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        doc = client.get(f"/api/nodes/{path}/metrics").json()
        assert doc["keys"] == ["loss"]
        series = doc["series"]["loss"]
        assert len(series["value"]) == 10
        assert series["epoch"] == list(range(10))

    def test_baked_source_is_readable(self, client):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        doc = client.get(f"/api/nodes/{path}/source", params={"file": "resnet18/main.py"}).json()
        assert "@blab.entry" in doc["text"]

    def test_path_escape_is_refused(self, client):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        res = client.get(f"/api/nodes/{path}/source", params={"file": "../../../etc/passwd"})
        assert res.status_code == 400

    def test_unknown_node_is_404(self, client):
        assert client.get("/api/nodes/nope/detail").status_code == 404


class TestComponents:
    def test_list_includes_usage_counts(self, rich):
        components = {c["id"]: c for c in rich.get("/api/components").json()["components"]}
        assert components["resnet18"]["n_runs"] == 4
        assert components["resnet18"]["label"] == "v1"

    def test_detail_has_readme_labels_and_reverse_lookup(self, rich):
        doc = rich.get("/api/components/resnet18").json()
        assert [l["name"] for l in doc["labels"]] == ["v1"]
        # 逆引き: この component を使った run。
        assert sum(len(runs) for runs in doc["by_version"].values()) == 4

    def test_files_and_source(self, client):
        doc = client.get("/api/components/standard_trainer/files").json()
        assert sorted(f["name"] for f in doc["files"]) == ["loops.py", "main.py"]
        source = client.get(
            "/api/components/standard_trainer/source", params={"file": "main.py"}
        ).json()
        assert "StandardTrainer" in source["text"]

    def test_diff_between_versions(self, client, project):
        from blab.components import hash_dir
        from conftest import MODEL

        before = hash_dir(project.components_dir / "resnet18")
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        go(project)  # 新しい版を凍結させる
        after = hash_dir(project.components_dir / "resnet18")

        doc = client.get(
            "/api/components/resnet18/diff", params={"a": before, "b": after}
        ).json()
        assert doc["files"][0]["file"] == "main.py"
        assert "pretrained=True" in doc["files"][0]["diff"]

    def test_version_only_present_in_a_run_is_still_readable(self, client, project):
        """ラベルも凍結も無い版は、それを使った run に焼き込まれている。"""
        from blab.components import hash_dir
        from conftest import MODEL
        import shutil

        outcome_hash = hash_dir(project.components_dir / "resnet18")
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        shutil.rmtree(project.cache_dir)  # キャッシュを捨てる

        doc = client.get(
            "/api/components/resnet18/source",
            params={"file": "main.py", "hash": outcome_hash},
        ).json()
        assert "pretrained=False" in doc["text"]


class TestWrites:
    def test_moving_a_run_is_the_only_write(self, client, project):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        res = client.post(f"/api/nodes/{path}/group", json={"group": "cv5"})
        assert res.status_code == 200
        assert res.json()["path"].startswith("cifar100/cv5/")

        # 黙って履歴を書き換えない。
        moved = client.get(f"/api/nodes/{res.json()['path']}/detail").json()
        assert moved["meta"]["moved_from"]["path"]

    def test_moving_back_to_the_experiment(self, client):
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        client.post(f"/api/nodes/{path}/group", json={"group": "cv5"})
        rows = client.get("/api/nodes", params={"kind": "run"}).json()["rows"]
        moved = rows[0]["path"]
        res = client.post(f"/api/nodes/{moved}/group", json={"group": None})
        assert "/cv5/" not in res.json()["path"]

    def test_deleting_is_moving_to_the_reserved_trash_group(self, client):
        """UI の「削除」も新しい書き込み経路を増やさず、_trash への group 付け替えでしかない。"""
        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        res = client.post(f"/api/nodes/{path}/group", json={"group": "_trash"})
        assert res.status_code == 200
        trashed = res.json()["path"]
        assert trashed.startswith("cifar100/_trash/")

        # 既定の一覧からは消える。
        rows = client.get("/api/nodes").json()["rows"]
        assert not any(r["path"] == trashed or r["path"].startswith("cifar100/_trash") for r in rows)
        assert not any(r["kind"] == "run" for r in rows)

        # experiment の集計からも外れる。
        exp = client.get("/api/experiments").json()["experiments"][0]
        assert exp["n_runs"] == 0

        # include_trash=1 なら見える。実体は消えていないので復元できる。
        rows = client.get("/api/nodes", params={"include_trash": "1"}).json()["rows"]
        assert any(r["path"] == trashed for r in rows)

        restore = client.post(f"/api/nodes/{trashed}/group", json={"group": None})
        assert restore.status_code == 200
        assert "_trash" not in restore.json()["path"]

    def test_a_running_run_cannot_be_moved(self, client, project):
        """実行中に移すと、生きているプロセスが元の場所を作り直して run が 2 つに分かれる。"""
        from blab.ids import isoformat, now
        from blab.io import write_json_atomic
        from blab.run import read_meta

        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        run_dir = project.runs_dir() / path
        meta = read_meta(run_dir)
        meta.update(status="running", heartbeat_at=isoformat(now()))
        write_json_atomic(run_dir / "meta.json", meta)

        res = client.post(f"/api/nodes/{path}/group", json={"group": "_trash"})
        assert res.status_code == 400
        assert "実行中" in res.json()["detail"]
        assert run_dir.is_dir()

    def test_a_name_already_in_the_trash_gets_a_suffix(self, client, project):
        """同じ名前の run が 2 つあっても、どちらもゴミ箱へ移せる。"""
        import shutil

        path = next(r["path"] for r in client.get("/api/nodes").json()["rows"] if r["kind"] == "run")
        source = project.runs_dir() / path
        (source.parent / "_trash").mkdir()
        shutil.copytree(source, source.parent / "_trash" / source.name)

        res = client.post(f"/api/nodes/{path}/group", json={"group": "_trash"})
        assert res.status_code == 200
        assert res.json()["path"] == f"cifar100/_trash/{source.name}_dup2"


def test_files_endpoint_refuses_escape(client):
    assert client.get("/files/../../etc/passwd").status_code in (400, 404)
    assert client.get("/files/%2e%2e/%2e%2e/etc/passwd").status_code in (400, 404)


class TestProjects:
    """UI から開けるのはグローバル索引に載ったプロジェクトと、起動したプロジェクトだけ。"""

    @pytest.fixture
    def other(self, tmp_path):
        from conftest import DATASET, MODEL, TRAINER, LOOPS, write_component
        from blab.project import init

        proj, _ = init(tmp_path / "other", name="other")  # init が索引に登録する
        write_component(proj, "standard_trainer", TRAINER, loops__py=LOOPS)
        write_component(proj, "cifar100", DATASET)
        write_component(proj, "resnet18", MODEL)
        go(proj)
        go(proj)
        return proj

    def test_lists_the_started_project_first_then_the_index(self, project, other):
        doc = TestClient(create_app(project)).get("/api/projects").json()
        assert doc["default"] == project.uid
        uids = [p["uid"] for p in doc["projects"]]
        assert uids[0] == project.uid
        assert other.uid in uids and len(uids) == len(set(uids))
        assert all(p["available"] for p in doc["projects"])

    def test_opens_another_project_by_uid(self, project, other):
        go(project)
        client = TestClient(create_app(project))
        mine = client.get(f"/api/p/{project.uid}/status").json()
        theirs = client.get(f"/api/p/{other.uid}/status").json()
        assert (mine["project"], mine["n_runs"]) == (project.name, 1)
        assert (theirs["project"], theirs["n_runs"]) == ("other", 2)

    def test_files_are_served_per_project(self, project, other):
        client = TestClient(create_app(project))
        run = next(
            r["path"]
            for r in client.get(f"/api/p/{other.uid}/nodes").json()["rows"]
            if r["kind"] == "run"
        )
        assert client.get(f"/files/{other.uid}/{run}/meta.json").status_code == 200
        assert client.get(f"/files/{project.uid}/{run}/meta.json").status_code == 404

    def test_unknown_uid_is_404(self, project):
        client = TestClient(create_app(project))
        assert client.get("/api/p/nope/status").status_code == 404
        assert client.get("/files/nope/x").status_code == 404

    def test_vanished_project_is_listed_but_not_opened(self, project, other):
        import shutil

        shutil.rmtree(other.root)
        client = TestClient(create_app(project))
        entry = next(
            p for p in client.get("/api/projects").json()["projects"] if p["uid"] == other.uid
        )
        assert entry["available"] is False
        assert client.get(f"/api/p/{other.uid}/status").status_code == 404

    def test_stale_index_pointing_at_a_different_project_is_refused(self, project, other):
        from blab.project import GlobalIndex

        GlobalIndex.load().register("someone-else", "ghost", other.root)
        client = TestClient(create_app(project))
        assert client.get("/api/p/someone-else/status").status_code == 404

    def test_runs_dir_override_only_applies_to_the_started_project(self, project, other, tmp_path):
        logs = tmp_path / "elsewhere"
        logs.mkdir()
        client = TestClient(create_app(project, runs_dir=logs))
        assert client.get(f"/api/p/{project.uid}/status").json()["runs_dir"] == str(logs)
        assert client.get(f"/api/p/{other.uid}/status").json()["runs_dir"] == str(other.runs_dir())

    def test_starts_without_a_project(self, other):
        client = TestClient(create_app(None))
        doc = client.get("/api/projects").json()
        assert doc["default"] is None
        assert [p["uid"] for p in doc["projects"]] == [other.uid]
        assert client.get(f"/api/p/{other.uid}/status").json()["n_runs"] == 2


class TestStatic:
    def test_serves_the_app_shell(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "/js/app.js" in res.text

    @pytest.mark.parametrize(
        "path",
        [
            "/js/app.js", "/js/api.js", "/js/config-tree.js", "/js/table.js",
            "/js/stats.js", "/js/prefs.js", "/js/util.js", "/js/chart.js",
            "/js/views/run.js", "/js/views/runs.js", "/js/views/group.js",
            "/js/views/compare.js", "/js/views/component.js",
            "/js/views/components.js", "/js/views/experiments.js",
            "/js/markdown.js", "/js/docs.js", "/js/views/projects.js",
            "/style.css",
        ],
    )
    def test_every_asset_the_shell_needs_is_served(self, client, path):
        assert client.get(path).status_code == 200


class TestServeWarnings:
    """libyaml が無いと resolved.yaml の読み込みが 10 倍遅いので、起動時に知らせる。"""

    @pytest.fixture
    def no_uvicorn(self, monkeypatch):
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    def test_warns_without_libyaml(self, project, monkeypatch, capsys, no_uvicorn):
        from blab.server import app as appmod

        monkeypatch.setattr(appmod, "FAST_YAML", False)
        appmod.serve(project)
        assert "libyaml" in capsys.readouterr().err

    def test_silent_with_libyaml(self, project, monkeypatch, capsys, no_uvicorn):
        from blab.server import app as appmod

        monkeypatch.setattr(appmod, "FAST_YAML", True)
        appmod.serve(project)
        assert "libyaml" not in capsys.readouterr().err

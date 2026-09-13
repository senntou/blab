"""Phase 9 — 閲覧層の API。**ディレクトリを読むだけ**（layout.md §9）。"""

from __future__ import annotations

import pytest
import yaml

from blab.runner import execute
from blab.spec import parse_document
from conftest import BASELINE

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from blab.server import create_app  # noqa: E402


def go(project, text: str = BASELINE):
    return execute(project, parse_document(yaml.safe_load(text)), capture=False)


@pytest.fixture
def client(project):
    go(project)
    return TestClient(create_app(project))


@pytest.fixture
def rich(project):
    """run / group / ラベル付き component が揃った状態。"""
    go(project)
    for _ in range(3):
        go(project, BASELINE + "\ngroup: cv5\n")
    from blab.tag import tag

    tag(project, "resnet18", "v1", note="初版")
    return TestClient(create_app(project))


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


def test_files_endpoint_refuses_escape(client):
    assert client.get("/files/../../etc/passwd").status_code in (400, 404)


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
            "/style.css",
        ],
    )
    def test_every_asset_the_shell_needs_is_served(self, client, path):
        assert client.get(path).status_code == 200

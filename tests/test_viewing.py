"""閲覧層（索引・サーバ・原子性）のテスト。"""

from __future__ import annotations

import json

import pytest

import blab
from blab import layout
from blab.errors import BlabError
from blab.index import Index
from blab.io import read_jsonl_from, write_json_atomic

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from blab.server.app import create_app  # noqa: E402


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = tmp_path / "blab"
    with blab.init(experiment="exp", name="a", params={"lr": 1e-3, "optim": {"name": "adam"}}, dir=r) as run:
        run.log({"loss": 1.0}, step=0)
        run.log({"loss": 0.5, "acc": 0.7}, step=1)
        run.log_summary({"test/acc": 0.9})
        art = tmp_path / "img.png"
        art.write_bytes(b"\x89PNG\r\n\x1a\n")
        run.log_artifact(art)
    with blab.group(experiment="exp", name="cv", group_kind="cv", dir=r) as g:
        for fold in range(2):
            with g.run(name=f"fold{fold}", params={"fold": fold}) as run:
                run.log({"loss": 1.0 - fold * 0.1})
                run.log_summary({"test/acc": 0.8 + fold * 0.1})
    return r


@pytest.fixture()
def client(root):
    return TestClient(create_app(root))


def test_index_builds_tree(root):
    index = Index(root)
    index.refresh()
    tree = index.tree()
    assert [e["name"] for e in tree] == ["exp"]
    assert tree[0]["counts"] == {"run": 3, "group": 1, "running": 0}
    rows = index.rows()
    assert {r["kind"] for r in rows} == {"run", "group"}
    run_row = next(r for r in rows if r["name"] == "a")
    assert run_row["params"]["optim.name"] == "adam"
    assert run_row["summary"]["test/acc"] == 0.9
    group_row = next(r for r in rows if r["kind"] == "group")
    assert group_row["summary"]["test/acc"] == pytest.approx(0.85)  # 集計の mean


def test_group_row_mixes_own_values_and_aggregate(root):
    """group 行には自分の値と配下の平均が並び、どちらか区別できる。"""
    index = Index(root)
    index.refresh()
    group_path = next(r["path"] for r in index.rows() if r["kind"] == "group")

    blab.open(group_path, dir=root).log_summary({"oof/acc": 0.83, "test/acc": 0.99})
    index.refresh()
    row = next(r for r in index.rows() if r["kind"] == "group")
    # 同じキーがあれば group 自身の値が勝つ（fold 平均では作れない値だから）
    assert row["summary"]["test/acc"] == 0.99
    assert row["summary"]["oof/acc"] == 0.83
    assert row["summary_own"] == ["oof/acc", "test/acc"]
    assert "test/acc" not in row["summary_stats"]  # 自前の値には ±std を付けない

    detail = index.detail(group_path)
    assert detail["summary_raw"]["values"]["oof/acc"] == 0.83
    assert detail["artifacts"] == []  # group も artifacts 欄を持つ


def test_index_picks_up_appended_metrics_and_deletions(root):
    index = Index(root)
    index.refresh()
    run_path = next(r["path"] for r in index.rows(kind="run") if r["name"] == "a")
    assert index.metrics(run_path)["n_rows"] == 2

    run_dir = root / run_path
    with open(run_dir / layout.METRICS_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps({"_step": 2, "_time": 1.0, "loss": 0.1}) + "\n")
    index.refresh()
    metrics = index.metrics(run_path)
    assert metrics["n_rows"] == 3
    assert metrics["series"]["loss"]["value"][-1] == 0.1

    import shutil

    shutil.rmtree(run_dir)
    index.refresh()
    assert index.get(run_path) is None


def test_incomplete_last_jsonl_line_is_ignored(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"_step": 0, "a": 1}\n{"_step": 1, "a"', encoding="utf-8")
    records, offset = read_jsonl_from(path, 0)
    assert [r["_step"] for r in records] == [0]

    # 続きが書かれたら、次回は途中だった行を頭から読み直せる
    with open(path, "a", encoding="utf-8") as f:
        f.write(': 2}\n')
    more, _ = read_jsonl_from(path, offset)
    assert [r["_step"] for r in more] == [1]


def test_atomic_write_leaves_no_partial_json(tmp_path):
    path = tmp_path / "meta.json"
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"a": 2, "b": [1, 2]})
    assert json.loads(path.read_text()) == {"a": 2, "b": [1, 2]}
    assert list(tmp_path.iterdir()) == [path]  # 一時ファイルが残らない


def test_metrics_downsampling(root):
    index = Index(root)
    index.refresh()
    run_path = next(r["path"] for r in index.rows(kind="run") if r["name"] == "a")
    run_dir = root / run_path
    with open(run_dir / layout.METRICS_NAME, "a", encoding="utf-8") as f:
        for i in range(2, 500):
            f.write(json.dumps({"_step": i, "_time": float(i), "loss": 1.0 / (i + 1)}) + "\n")
    index.refresh()
    series = index.metrics(run_path, max_points=50)["series"]["loss"]
    assert series["n"] == 500
    assert len(series["value"]) <= 50
    assert series["stride"] > 1


def test_api_endpoints(client, root):
    assert client.get("/api/status").json()["root"] == str(root.resolve())
    tree = client.get("/api/tree").json()
    assert tree["experiments"][0]["name"] == "exp"

    rows = client.get("/api/nodes").json()["rows"]
    run_path = next(r["path"] for r in rows if r["name"] == "a")
    group_path = next(r["path"] for r in rows if r["kind"] == "group")

    detail = client.get(f"/api/runs/{run_path}").json()
    assert detail["metric_keys"] == ["acc", "loss"]
    assert detail["artifacts"][0]["name"] == "img.png"
    assert detail["meta"]["kind"] == "run"

    metrics = client.get(f"/api/runs/{run_path}/metrics", params={"keys": "loss"}).json()
    assert list(metrics["series"]) == ["loss"]
    assert metrics["series"]["loss"]["value"] == [1.0, 0.5]

    agg = client.get(f"/api/groups/{group_path}/aggregate").json()
    assert agg["n_runs"] == 2 and agg["n_excluded"] == 0

    assert client.get(f"/files/{run_path}/artifacts/img.png").status_code == 200
    assert client.get("/api/runs/does/not/exist").status_code == 404
    # group を run として要求したら 404
    assert client.get(f"/api/runs/{group_path}/metrics").status_code == 404


def test_api_rejects_path_escape(client, root):
    outside = root.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    assert client.get("/files/exp/../../secret.txt").status_code in (400, 404)
    with pytest.raises(BlabError):
        layout.safe_join(root, "../secret.txt")
    with pytest.raises(BlabError):
        layout.safe_join(root, "/etc/passwd")


def test_api_sees_live_run(client, root, monkeypatch):
    # 索引の再走査スロットリング（既定 1 秒）を切って、追記が即見えることを確かめる。
    monkeypatch.setattr("blab.server.app.RESCAN_INTERVAL_SEC", 0.0)
    run = blab.init(experiment="exp", name="live", dir=root)
    try:
        run.log({"loss": 2.0})
        rows = client.get("/api/nodes", params={"kind": "run"}).json()["rows"]
        live = next(r for r in rows if r["name"] == "live")
        assert live["status"] == "running" and live["stale"] is False
        metrics = client.get(f"/api/runs/{live['path']}/metrics").json()
        assert metrics["series"]["loss"]["value"] == [2.0]
        run.log({"loss": 1.0})
        metrics = client.get(f"/api/runs/{live['path']}/metrics").json()
        assert metrics["series"]["loss"]["value"] == [2.0, 1.0]
    finally:
        run.finish()


# ------------------------------------------------------------ component（★ blab）


WIDGET = '''
import blab


@blab.entry
class Widget:
    def __init__(self, k: int = 1):
        self.k = k
'''


@pytest.fixture()
def with_components(tmp_path, monkeypatch):
    """component を使った run が 2 本ある root。"""
    from blab import registry

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BLAB_DEV", raising=False)
    r = layout.ensure_root(tmp_path / ".blab")
    monkeypatch.setenv("BLAB_DIR", str(r))
    registry.create_component(r, "widget")
    current = layout.current_source_path(r, "widget")
    current.write_text(WIDGET, encoding="utf-8")
    registry.register(r, "widget", "v1", note="初版", tags=["model"])
    current.write_text(WIDGET.replace("self.k = k", "self.k = k * 2"), encoding="utf-8")
    registry.register(r, "widget", "v2", note="2 倍にする")

    for version, acc in (("v1", 0.7), ("v2", 0.9)):
        with blab.init(experiment="exp", name=f"run_{version}", dir=r) as run:
            blab.load(f"widget@{version}", role="model", k=3)
            run.log_summary({"test/acc": acc})
    yield r
    import sys

    for name in [n for n in sys.modules if n.startswith(registry.MODULE_PREFIX)]:
        sys.modules.pop(name, None)


def test_row_has_binding_columns(with_components):
    index = Index(with_components)
    index.refresh()
    rows = {r["name"]: r for r in index.rows(kind="run")}
    assert rows["run_v2"]["bindings"] == {"model": "widget@v2", "model.k": 3}
    assert rows["run_v2"]["components"]["n_bindings"] == 1
    assert rows["run_v2"]["components"]["dirty"] is False


def test_component_reverse_lookup(with_components):
    index = Index(with_components)
    index.refresh()
    listed = {c["id"]: c for c in index.components()}
    assert listed["widget"]["n_runs"] == 2
    assert listed["widget"]["latest"] == "v2"
    detail = index.component("widget")
    assert sorted(detail["by_version"]) == ["v1", "v2"]
    assert len(detail["by_version"]["v1"]) == 1
    # 逆引きの行に summary が付く（「この version は結局効いたのか」を見るため）
    v2 = [r for r in detail["runs"] if r["version"] == "v2"][0]
    assert v2["summary"]["test/acc"] == 0.9


def test_component_tag_filter(with_components):
    index = Index(with_components)
    index.refresh()
    assert [c["id"] for c in index.components(["model"])] == ["widget"]
    assert index.components(["nonexistent"]) == []


def test_component_api(with_components):
    client = TestClient(create_app(with_components))
    listed = client.get("/api/components").json()["components"]
    assert [c["id"] for c in listed] == ["widget"]

    detail = client.get("/api/components/widget").json()
    assert detail["entry"] == "Widget"
    assert detail["n_runs"] == 2

    source = client.get("/api/components/widget/source?version=v1").json()
    assert "class Widget" in source["source"]
    assert client.get("/api/components/widget/source?version=v9").status_code == 404

    diff = client.get("/api/components/widget/diff?a=v1&b=v2").json()["diff"]
    assert "+        self.k = k * 2" in diff

    runs = client.get("/api/components/widget/runs").json()
    assert len(runs["runs"]) == 2
    assert client.get("/api/components/nope").status_code == 404


def test_run_detail_exposes_components_and_code(with_components):
    client = TestClient(create_app(with_components))
    path = [r["path"] for r in client.get("/api/nodes").json()["rows"] if r["name"] == "run_v2"][0]
    detail = client.get(f"/api/runs/{path}").json()
    assert detail["components_raw"]["bindings"][0]["id"] == "widget"
    assert detail["components_raw"]["entrypoints"]
    # pytest 経由なので entrypoint は pytest 自身。ファイル一覧が引けることを見る。
    names = [f["name"] for f in detail["code"]]
    entry = detail["components_raw"]["entrypoints"][0]
    if entry.get("path"):
        assert entry["path"].removeprefix("code/") in names
        fetched = client.get(f"/api/runs/{path}/code/{names[0]}").json()
        assert fetched["source"]
        assert fetched["hash"].startswith("sha256:")


def test_code_endpoint_rejects_escape(with_components):
    client = TestClient(create_app(with_components))
    path = [r["path"] for r in client.get("/api/nodes").json()["rows"] if r["name"] == "run_v2"][0]
    assert client.get(f"/api/runs/{path}/code/%2e%2e%2f%2e%2e%2fblab.json").status_code in (400, 404)


def test_registry_is_not_scanned_as_a_node(with_components):
    """components/ は run/group ではない。索引のツリーに現れてはいけない。"""
    index = Index(with_components)
    index.refresh()
    assert all(not p.startswith("components") for p in index.nodes)
    assert index.stats()["n_components"] == 1

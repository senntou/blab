"""記録層のテスト。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

import blab
from blab import layout
from blab.aggregate import aggregate_group, reindex
from blab.errors import BlabError, BlabUsageError
from blab.io import read_json, read_jsonl_from
from blab.schema import flatten, normalize_params


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("BLAB_STRICT", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path / "blab"


def read_metrics(run):
    records, _ = read_jsonl_from(run.dir / layout.METRICS_NAME, 0)
    return records


def test_run_writes_layout(root):
    with blab.init(experiment="exp", name="baseline", params={"lr": 3e-4}, dir=root) as run:
        run.log({"loss": 1.0})
        run.log({"loss": 0.5})
        run.log_summary({"test/acc": 0.9})
        run_dir = run.dir

    assert (root / layout.ROOT_MARKER).is_file()
    meta = read_json(run_dir / layout.META_NAME)
    assert meta["kind"] == "run"
    assert meta["status"] == "finished"
    assert meta["finished_at"] and meta["duration_sec"] is not None
    assert read_json(run_dir / layout.PARAMS_NAME) == {"lr": 3e-4}
    assert read_json(run_dir / layout.SUMMARY_NAME) == {"test/acc": 0.9}
    assert run_dir.parent.name == "exp"
    assert run_dir.name.endswith("_baseline")
    # ディレクトリ名の日時は UTC 固定
    stamp, short, _slug = run_dir.name.split("_", 2)
    created = datetime.fromisoformat(meta["created_at"]).astimezone(timezone.utc)
    assert stamp == created.strftime("%Y%m%d-%H%M%S")
    assert short == meta["id"][-4:].lower()


def test_step_autonumbering_and_reserved_keys(root):
    with blab.init(experiment="exp", dir=root) as run:
        run.log({"a": 1})
        run.log({"a": 2})
        run.log({"a": 3}, step=10)
        run.log({"a": 4})
        run.log({"a": 5}, epoch=2)
        records = read_metrics(run)
    assert [r["_step"] for r in records] == [0, 1, 10, 11, 12]
    assert all(isinstance(r["_time"], float) for r in records)
    assert records[-1]["_epoch"] == 2
    assert "_epoch" not in records[0]


def test_summary_is_shallow_merged(root):
    with blab.init(experiment="exp", dir=root) as run:
        run.log_summary({"a": 1, "b": 2})
        run.log_summary({"b": 3, "c": 4})
        assert read_json(run.dir / layout.SUMMARY_NAME) == {"a": 1, "b": 3, "c": 4}


def test_failed_and_killed_status(root):
    with pytest.raises(RuntimeError):
        with blab.init(experiment="exp", name="boom", dir=root) as run:
            failed_dir = run.dir
            raise RuntimeError("boom")
    assert read_json(failed_dir / layout.META_NAME)["status"] == "failed"

    with pytest.raises(KeyboardInterrupt):
        with blab.init(experiment="exp", name="ctrlc", dir=root) as run:
            killed_dir = run.dir
            raise KeyboardInterrupt
    assert read_json(killed_dir / layout.META_NAME)["status"] == "killed"


def test_dotted_param_keys(root, monkeypatch):
    with pytest.warns(RuntimeWarning):
        assert normalize_params({"a.b": 1}) == {"a_b": 1}
    monkeypatch.setenv("BLAB_STRICT", "1")
    with pytest.raises(BlabError):
        normalize_params({"a.b": 1})


def test_flatten_nested_and_nonscalar():
    flat = flatten({"optim": {"lr": 1e-3}, "aug": ["flip", "crop"], "n": 3})
    assert flat["optim.lr"] == 1e-3
    assert flat["n"] == 3
    assert json.loads(flat["aug"]) == ["flip", "crop"]


def test_logging_never_raises_but_strict_does(root, monkeypatch):
    with blab.init(experiment="exp", dir=root) as run:
        with pytest.warns(RuntimeWarning):
            run.log_artifact("/nonexistent/file.png")  # 握って警告
        monkeypatch.setenv("BLAB_STRICT", "1")
        with pytest.raises(BlabError):
            run.log_artifact("/nonexistent/file.png")


def test_nested_init_is_rejected(root):
    with blab.init(experiment="exp", dir=root):
        with pytest.raises(BlabUsageError):
            blab.init(experiment="exp", dir=root)
    # 終了後は再び作れる
    blab.init(experiment="exp", dir=root).finish()


def test_artifacts_copy_and_move(root, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    moved = tmp_path / "b.txt"
    moved.write_text("bye", encoding="utf-8")
    with blab.init(experiment="exp", dir=root) as run:
        run.log_artifact(src)
        run.log_artifact(moved, name="sub/renamed.txt", mode="move")
        assert (run.dir / "artifacts" / "a.txt").read_text() == "hello"
        assert (run.dir / "artifacts" / "sub" / "renamed.txt").read_text() == "bye"
        assert not moved.exists()
        assert src.exists()  # copy は元を残す


def test_artifact_cannot_escape_run_dir(root):
    with blab.init(experiment="exp", dir=root) as run:
        with pytest.warns(RuntimeWarning):
            assert run.log_artifact(__file__, name="../escaped.py") is None


def test_heartbeat_thread_runs(root, monkeypatch):
    monkeypatch.setattr(layout, "HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr("blab.run.HEARTBEAT_INTERVAL_SEC", 0.05)
    run = blab.init(experiment="exp", dir=root)
    first = read_json(run.dir / layout.META_NAME)["heartbeat_at"]
    threading.Event().wait(0.2)
    second = read_json(run.dir / layout.META_NAME)["heartbeat_at"]
    run.finish()
    assert second >= first
    assert not run._heartbeat.is_alive()


def test_stale_detection():
    old = (datetime.now(timezone.utc) - timedelta(seconds=layout.STALE_AFTER_SEC + 5)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    assert layout.is_stale({"status": "running", "heartbeat_at": old})
    assert not layout.is_stale({"status": "running", "heartbeat_at": fresh})
    assert not layout.is_stale({"status": "finished", "heartbeat_at": old})


def test_group_aggregation(root):
    with blab.group(experiment="exp", name="cv", group_kind="cv", dir=root) as g:
        for fold in range(3):
            with g.run(name=f"fold{fold}", params={"fold": fold}) as run:
                run.log_summary({"test/acc": 0.8 + fold * 0.1, "note": "x"})
        # finished でない run は集計から外れる
        failed = g.run(name="fold3")
        failed.log_summary({"test/acc": 0.0})
        failed.finish("failed")
        group_dir = g.dir

    summary = read_json(group_dir / layout.SUMMARY_NAME)
    assert summary["n_runs"] == 4
    assert summary["n_excluded"] == 1
    acc = summary["metrics"]["test/acc"]
    assert acc["count"] == 3
    assert acc["mean"] == pytest.approx(0.9)
    assert acc["min"] == pytest.approx(0.8) and acc["max"] == pytest.approx(1.0)
    assert "note" not in summary["metrics"]  # 数値でないキーは集計しない
    assert read_json(group_dir / layout.META_NAME)["status"] == "finished"


def test_nested_group_counts_only_leaf_runs(root):
    with blab.group(experiment="exp", name="outer", dir=root) as outer:
        with outer.run(name="direct") as run:
            run.log_summary({"acc": 1.0})
        with outer.group(name="inner") as inner:
            for i in range(2):
                with inner.run(name=f"r{i}") as run:
                    run.log_summary({"acc": 0.5})
        outer_dir = outer.dir

    agg = aggregate_group(outer_dir)
    # 葉の run 3 本だけを見る（inner の summary.json は二重集計しない）
    assert agg["metrics"]["acc"]["count"] == 3
    assert agg["metrics"]["acc"]["mean"] == pytest.approx(2.0 / 3)


def test_group_failure_still_aggregates(root):
    with pytest.raises(RuntimeError):
        with blab.group(experiment="exp", name="cv", dir=root) as g:
            with g.run(name="fold0") as run:
                run.log_summary({"acc": 0.5})
            group_dir = g.dir
            raise RuntimeError("fold1 crashed")
    meta = read_json(group_dir / layout.META_NAME)
    assert meta["status"] == "failed"
    assert read_json(group_dir / layout.SUMMARY_NAME)["metrics"]["acc"]["count"] == 1


def test_group_records_its_own_values(root):
    """group 自身の値は集計とは別枠で、集計しても消えない。"""
    with blab.group(experiment="exp", name="cv", group_kind="cv", dir=root) as g:
        for fold in range(2):
            with g.run(name=f"fold{fold}") as run:
                run.log_summary({"acc": 0.8 + fold * 0.1})
        g.log_summary({"oof/acc": 0.87, "wilcoxon_p": 0.03})
        g.log_summary({"wilcoxon_p": 0.02})  # shallow merge（後勝ち）
        group_dir = g.dir

    summary = read_json(group_dir / layout.SUMMARY_NAME)
    assert summary["values"] == {"oof/acc": 0.87, "wilcoxon_p": 0.02}
    assert summary["metrics"]["acc"]["count"] == 2  # 集計は集計で生きている
    # 集計をやり直しても group 自身の値は残る（キャッシュに巻き込まれない）
    reindex(root)
    assert read_json(group_dir / layout.SUMMARY_NAME)["values"]["oof/acc"] == 0.87
    # 配下 run の集計には混ざらない（group の値は葉ではない）
    assert "oof/acc" not in aggregate_group(group_dir)["metrics"]


def test_group_artifact(root, tmp_path):
    src = tmp_path / "per_fold.csv"
    src.write_text("fold,acc\n0,0.8\n")
    with blab.group(experiment="exp", name="cv", dir=root) as g:
        g.log_artifact(src)
        group_dir = g.dir
    assert (group_dir / layout.ARTIFACTS_DIR / "per_fold.csv").read_text().startswith("fold,acc")


def test_open_run_appends_without_touching_the_record(root):
    """終わった run を開き直して後付け記録する（評価が学習の後になるため）。"""
    with blab.init(experiment="exp", name="baseline", params={"lr": 1e-3}, dir=root) as run:
        run.log({"loss": 1.0})
        run.log_summary({"val/r2": 0.5})
        run_dir = run.dir
    before = read_json(run_dir / layout.META_NAME)

    opened = blab.open("baseline", dir=root)
    opened.log_summary({"probe/macro_f1": 0.71})
    opened.log({"loss": 0.4})
    opened.finish()

    assert read_json(run_dir / layout.SUMMARY_NAME) == {"val/r2": 0.5, "probe/macro_f1": 0.71}
    assert read_json(run_dir / layout.PARAMS_NAME) == {"lr": 1e-3}  # 上書きされない
    after = read_json(run_dir / layout.META_NAME)
    assert (after["status"], after["finished_at"]) == (before["status"], before["finished_at"])
    records, _ = read_jsonl_from(run_dir / layout.METRICS_NAME, 0)
    assert [r["_step"] for r in records] == [0, 1]  # step は続きから


def test_open_group_and_id_lookup(root):
    with blab.group(experiment="exp", name="cv", dir=root) as g:
        with g.run(name="fold0") as run:
            run.log_summary({"acc": 0.5})
        group_dir, group_id = g.dir, g.id

    blab.open(group_id, dir=root).log_summary({"oof/acc": 0.6})
    assert read_json(group_dir / layout.SUMMARY_NAME)["values"] == {"oof/acc": 0.6}
    # 短 ID（末尾4文字）でも、絶対パスそのものでも引ける
    assert blab.open(layout.short_id(group_id), dir=root).dir == group_dir
    assert blab.open(group_dir.resolve(), dir=root).dir == group_dir
    with pytest.raises(BlabError):
        blab.open("no-such-run", dir=root)


def test_open_with_resume_reopens_the_run(root):
    with blab.init(experiment="exp", name="crashed", dir=root) as run:
        run_dir = run.dir
        run.finish("killed")

    resumed = blab.open("crashed", dir=root, resume=True)
    assert read_json(run_dir / layout.META_NAME)["status"] == "running"
    resumed.log({"loss": 0.1})
    resumed.finish()
    assert read_json(run_dir / layout.META_NAME)["status"] == "finished"


def test_open_ambiguous_target_is_rejected(root):
    """同名 run が複数あるときに黙って片方を掴まない（取り違え防止）。"""
    for _ in range(2):
        with blab.init(experiment="exp", name="fold0", dir=root):
            pass
    with pytest.raises(BlabError, match="2 件"):
        blab.open("fold0", dir=root)


def test_reindex_regenerates_group_summary(root):
    with blab.group(experiment="exp", name="cv", dir=root) as g:
        with g.run(name="fold0") as run:
            run.log_summary({"acc": 0.5})
        group_dir = g.dir
    (group_dir / layout.SUMMARY_NAME).unlink()
    assert reindex(root) == [group_dir]
    assert (group_dir / layout.SUMMARY_NAME).is_file()


def test_root_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("BLAB_DIR", raising=False)
    project = tmp_path / "project"
    (project / "src" / "deep").mkdir(parents=True)
    layout.ensure_root(project / layout.DEFAULT_ROOT_NAME)
    monkeypatch.chdir(project / "src" / "deep")
    assert layout.resolve_root() == (project / layout.DEFAULT_ROOT_NAME).resolve()

    explicit = tmp_path / "elsewhere"
    monkeypatch.setenv("BLAB_DIR", str(explicit))
    assert layout.resolve_root() == explicit.resolve()
    assert layout.resolve_root(tmp_path / "arg") == (tmp_path / "arg").resolve()


def test_ulid_is_sortable_and_unique():
    ids = [layout.new_ulid() for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(len(i) == 26 for i in ids)
    assert sorted(ids[:1]) == ids[:1]
    early = layout.new_ulid(1_000_000_000_000)
    late = layout.new_ulid(2_000_000_000_000)
    assert early < late


def test_same_second_same_name_runs_do_not_collide(root):
    dirs = set()
    for _ in range(5):
        run = blab.init(experiment="exp", name="same", dir=root)
        dirs.add(run.dir)
        run.finish()
    assert len(dirs) == 5

"""component システムのテスト（design.md §3 / §4.1 / §4.2）。"""

from __future__ import annotations

import json
import sys

import pytest

import blab
from blab import layout, registry, schema, snapshot
from blab.errors import BlabError, BlabUsageError
from blab.io import read_json

WIDGET_V1 = '''
import blab


@blab.entry
class Widget:
    def __init__(self, k: int = 1, extra=None):
        self.k = k
        self.extra = extra
'''

WIDGET_V2 = WIDGET_V1.replace("self.k = k", "self.k = k * 2")

HOLDER_V1 = '''
import blab


@blab.entry
class Holder:
    def __init__(self, k: int = 3):
        # 依存は __init__ の中で load する（loaded_by として観測される）
        self.inner = blab.load("widget", k=k)
'''


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("BLAB_STRICT", raising=False)
    monkeypatch.delenv("BLAB_DEV", raising=False)
    monkeypatch.chdir(tmp_path)
    root = layout.ensure_root(tmp_path / ".blab")
    monkeypatch.setenv("BLAB_DIR", str(root))
    yield root
    # 合成モジュールはプロセスに残るので、テスト間で持ち越さない
    for name in [n for n in sys.modules if n.startswith(registry.MODULE_PREFIX)]:
        sys.modules.pop(name, None)


def write_component(root, id: str, source: str, version: str, **kw) -> None:
    current = layout.current_source_path(root, id)
    if not current.exists():
        registry.create_component(root, id)
    current.write_text(source, encoding="utf-8")
    registry.register(root, id, version, **kw)


# ------------------------------------------------------------ 登録（§3.7）


def test_register_records_entry_and_freezes_source(root):
    write_component(root, "widget", WIDGET_V1, "v1", note="初版", tags=["model"])
    meta = read_json(layout.component_meta_path(root, "widget"))
    assert meta["entry"] == "Widget"
    assert meta["tags"] == ["model"]
    assert [v["id"] for v in meta["versions"]] == ["v1"]
    frozen = layout.version_source_path(root, "widget", "v1")
    assert frozen.read_text() == WIDGET_V1
    assert meta["versions"][0]["hash"] == layout.hash_source(frozen)


def test_register_rejects_duplicate_version_and_identical_content(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with pytest.raises(BlabError, match="既に登録"):
        registry.register(root, "widget", "v1")
    with pytest.raises(BlabError, match="同一内容"):
        registry.register(root, "widget", "v2")


def test_register_requires_exactly_one_entry(root):
    registry.create_component(root, "broken")
    current = layout.current_source_path(root, "broken")
    current.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(BlabError, match="@blab.entry がありません"):
        registry.register(root, "broken", "v1")
    current.write_text(
        "import blab\n\n@blab.entry\nclass A:\n    pass\n\n@blab.entry\nclass B:\n    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(BlabError, match="2 個あります"):
        registry.register(root, "broken", "v1")


def test_ids_and_versions_are_validated(root):
    with pytest.raises(BlabError):
        registry.create_component(root, "not-an-identifier")
    write_component(root, "widget", WIDGET_V1, "v1")
    with pytest.raises(BlabError, match="予約語"):
        registry.register(root, "widget", "latest")


# ------------------------------------------------------------ load（§3.6）


def test_load_returns_instance_and_resolves_latest(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    write_component(root, "widget", WIDGET_V2, "v2")
    assert blab.load("widget").k == 2  # latest = versions の末尾
    assert blab.load("widget@v1").k == 1
    assert blab.load("widget@latest").k == 2


def test_load_requires_registration(root):
    with pytest.raises(BlabError, match="レジストリにありません"):
        blab.load("widget")
    registry.create_component(root, "widget")
    layout.current_source_path(root, "widget").write_text(WIDGET_V1, encoding="utf-8")
    with pytest.raises(BlabError, match="登録済み version を持ちません"):
        blab.load("widget")


def test_unknown_version_is_rejected(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with pytest.raises(BlabError, match="version 'v9' はありません"):
        blab.load("widget@v9")


def test_multiple_versions_load_side_by_side(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    write_component(root, "widget", WIDGET_V2, "v2")
    a, b = blab.load("widget@v1"), blab.load("widget@v2")
    assert (a.k, b.k) == (1, 2)
    assert type(a) is not type(b)  # 合成モジュール名が別なので別クラス


# ------------------------------------------------------------ 整合性ゲート（§3.4）


def test_gate_stops_implicit_load_when_current_differs(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    layout.current_source_path(root, "widget").write_text(WIDGET_V2, encoding="utf-8")
    with pytest.raises(BlabError, match="current/widget.py は v1 と異なります"):
        blab.load("widget")
    # version を明示した load は「その版を使う」表明なので止めない
    assert blab.load("widget@v1").k == 1


def test_gate_skipped_when_current_is_absent(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    layout.current_source_path(root, "widget").unlink()
    assert blab.load("widget").k == 1  # clone / rsync 直後


def test_gate_is_not_swallowed_by_guard(root):
    """記録系は握るが、ゲートは握らない（§5 の例外）。"""
    write_component(root, "widget", WIDGET_V1, "v1")
    layout.current_source_path(root, "widget").write_text(WIDGET_V2, encoding="utf-8")
    with blab.init(experiment="exp") as run:
        with pytest.raises(BlabError):
            blab.load("widget")
        run.log({"loss": 1.0})  # 記録は続けられる


# ------------------------------------------------------------ dev 実行（§3.4）


def test_dev_loads_current_and_marks_dirty(root, monkeypatch):
    write_component(root, "widget", WIDGET_V1, "v1")
    layout.current_source_path(root, "widget").write_text(WIDGET_V2, encoding="utf-8")
    with blab.init(experiment="exp") as run:
        assert blab.load("widget", dev=True).k == 2
        binding = run.components[0]
    assert binding["version"] is None
    assert binding["dirty"] is True
    assert binding["snapshot"] == "code/_components/widget.py"
    assert (run.dir / "code" / "_components" / "widget.py").read_text() == WIDGET_V2


def test_env_dev_leaves_pinned_loads_alone(root, monkeypatch):
    write_component(root, "widget", WIDGET_V1, "v1")
    write_component(root, "widget", WIDGET_V2, "v2")
    layout.current_source_path(root, "widget").write_text(
        WIDGET_V1.replace("self.k = k", "self.k = k * 99"), encoding="utf-8"
    )
    monkeypatch.setenv("BLAB_DEV", "1")
    assert blab.load("widget").k == 99  # pin していない → current
    assert blab.load("widget@v1").k == 1  # pin してある → version が勝つ
    monkeypatch.setenv("BLAB_DEV", "other_component")
    with pytest.raises(BlabError, match="異なります"):
        blab.load("widget")  # 名指しされていないので dev にならず、ゲートが効く


def test_explicit_dev_with_version_is_a_usage_error(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with pytest.raises(BlabUsageError, match="両立しません"):
        blab.load("widget@v1", dev=True)


# ------------------------------------------------------------ binding の観測（§4.1）


def test_bindings_record_order_refs_and_nesting(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    write_component(root, "holder", HOLDER_V1, "v1")
    with blab.init(experiment="exp") as run:
        holder = blab.load("holder", role="outer", k=5)
        blab.load("widget", role="passed", extra=holder)
        bindings = run.components

    assert [(b["index"], b["id"], b["loaded_by"]) for b in bindings] == [
        (0, "holder", None),  # index は load 開始順
        (1, "widget", 0),  # __init__ の中の load は loaded_by で親を指す
        (2, "widget", None),
    ]
    # loaded_by は必ず自分より小さい index を指す（UI が木を描くための不変条件）
    assert all(b["loaded_by"] is None or b["loaded_by"] < b["index"] for b in bindings)
    # 引数として渡したオブジェクトは $ref になる（データフロー）
    assert bindings[2]["args"]["extra"] == {"$ref": 0}
    assert bindings[1]["args"] == {"k": 5}


def test_identical_loads_are_folded_but_different_args_are_not(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp") as run:
        blab.load("widget", role="a", k=1)
        blab.load("widget", role="a", k=1)  # 完全一致 → 畳む
        blab.load("widget", role="a", k=2)  # args 違い → 別 binding
        blab.load("widget", role="b", k=1)  # role 違い → 別 binding
        assert len(run.components) == 3


def test_unrecordable_args_are_marked_not_guessed(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp") as run:
        blab.load("widget", extra=object())
        args = run.components[0]["args"]
    assert args["extra"] == {"$unrecorded": "object"}


def test_failed_load_is_still_recorded(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp") as run:
        with pytest.raises(TypeError):
            blab.load("widget", nonexistent_arg=1)
        binding = run.components[0]
    assert binding["failed"] is True
    assert binding["id"] == "widget"


def test_load_outside_a_run_is_not_recorded(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    assert blab.load("widget").k == 1  # 例外にならず、単に記録されない
    assert registry.current_recorder() is None


# ------------------------------------------------------------ entrypoint（§4.2）


def test_components_json_records_entrypoint(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp") as run:
        blab.load("widget")
        run_dir = run.dir
    doc = read_json(run_dir / layout.COMPONENTS_NAME)
    assert doc["schema_version"] == layout.SCHEMA_VERSION
    assert len(doc["entrypoints"]) == 1
    entry = doc["entrypoints"][0]
    # pytest 経由なので __main__ は pytest 自身。特定できたことと argv が残ることだけ見る。
    assert entry["argv"]
    assert "unresolved_imports" in entry


def test_reopened_run_appends_entrypoint_and_continues_indices(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp", name="r") as run:
        blab.load("widget", role="first")
        path = run.path
        run_dir = run.dir
    doc = read_json(run_dir / layout.COMPONENTS_NAME)
    doc["entrypoints"][0]["name"] = "train.py"  # 別スクリプトから開いた状況を作る
    doc["entrypoints"][0]["hash"] = "sha256:dummy"
    (run_dir / layout.COMPONENTS_NAME).write_text(json.dumps(doc), encoding="utf-8")

    with blab.open(path) as run:
        blab.load("widget", role="second")
    doc = read_json(run_dir / layout.COMPONENTS_NAME)
    assert [b["index"] for b in doc["bindings"]] == [0, 1]
    assert [b["role"] for b in doc["bindings"]] == ["first", "second"]
    assert [b["entrypoint"] for b in doc["bindings"]] == [0, 1]
    assert len(doc["entrypoints"]) == 2


def test_repeated_same_entrypoint_is_folded(root):
    write_component(root, "widget", WIDGET_V1, "v1")
    with blab.init(experiment="exp", name="r") as run:
        path, run_dir = run.path, run.dir
    for _ in range(3):
        blab.open(path).finish(None)
    doc = read_json(run_dir / layout.COMPONENTS_NAME)
    assert len(doc["entrypoints"]) == 1
    assert doc["entrypoints"][0]["n_recorded"] == 4


def test_unresolved_imports_are_reported(tmp_path):
    names, dynamic = snapshot.imported_names(
        "import os\n"
        "import definitely_not_installed_xyz\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import torch_not_real\n"
        "try:\n    import optional_dep\nexcept ImportError:\n    optional_dep = None\n"
        "import importlib\n"
        "m = importlib.import_module(name)\n"
    )
    assert "definitely_not_installed_xyz" in names
    assert "torch_not_real" not in names  # TYPE_CHECKING の中は数えない
    assert "optional_dep" not in names  # ImportError で守られた import も数えない
    assert dynamic and "動的" in dynamic[0]


def test_accounted_for_filters_known_names():
    """所在が説明できる名前は「未記録の参照」に出さない（警告の意味を保つため）。"""
    assert snapshot._accounted_for("json")  # 標準ライブラリ
    assert snapshot._accounted_for("blab")  # この実行で import 済み
    assert not snapshot._accounted_for("definitely_not_installed_xyz")


# ------------------------------------------------------------ ポリシー（§3.5）


def set_policy(root, **policy) -> None:
    marker = read_json(root / layout.ROOT_MARKER, {})
    marker.update(policy)
    (root / layout.ROOT_MARKER).write_text(json.dumps(marker), encoding="utf-8")


def test_policy_warns_and_records_violations(root):
    set_policy(root, require_components=True, require_tags=["model"], on_missing="warn")
    with pytest.warns(RuntimeWarning, match="ポリシー違反"):
        with blab.init(experiment="exp") as run:
            run_dir = run.dir
    meta = read_json(run_dir / layout.META_NAME)
    assert meta["status"] == "finished"  # 規律の問題であって実験の失敗ではない
    assert len(meta["policy_violations"]) == 2


def test_policy_error_writes_everything_then_raises(root):
    set_policy(root, require_components=True, on_missing="error")
    with pytest.raises(BlabError, match="ポリシー違反"):
        with blab.init(experiment="exp", params={"lr": 1.0}) as run:
            run_dir = run.dir
            run.log({"loss": 0.5})
            run.log_summary({"acc": 0.9})
    meta = read_json(run_dir / layout.META_NAME)
    assert meta["status"] == "finished"
    assert meta["finished_at"] and meta["duration_sec"] is not None
    assert meta["policy_violations"]
    assert read_json(run_dir / layout.SUMMARY_NAME) == {"acc": 0.9}
    assert (run_dir / layout.METRICS_NAME).read_text().strip()


def test_policy_satisfied_by_tagged_component(root):
    write_component(root, "widget", WIDGET_V1, "v1", tags=["model"])
    set_policy(root, require_components=True, require_tags=["model"], on_missing="error")
    with blab.init(experiment="exp") as run:
        blab.load("widget")
        run_dir = run.dir
    assert "policy_violations" not in read_json(run_dir / layout.META_NAME)


def test_policy_ignore_disables_the_check(root):
    set_policy(root, require_components=True, require_tags=["model"], on_missing="ignore")
    with blab.init(experiment="exp") as run:
        run_dir = run.dir
    assert "policy_violations" not in read_json(run_dir / layout.META_NAME)


def test_body_exception_wins_over_policy_error(root):
    set_policy(root, require_components=True, on_missing="error")
    with pytest.raises(RuntimeError, match="boom"):
        with blab.init(experiment="exp") as run:
            run_dir = run.dir
            raise RuntimeError("boom")
    meta = read_json(run_dir / layout.META_NAME)
    assert meta["status"] == "failed"  # 原因を隠さない
    assert meta["policy_violations"]


# ------------------------------------------------------------ 閲覧の補助


def test_describe_and_diff(root):
    write_component(root, "widget", WIDGET_V1, "v1", note="初版", tags=["model"])
    write_component(root, "widget", WIDGET_V2, "v2", note="k を 2 倍に")
    info = registry.describe(root, "widget")
    assert info["latest"] == "v2"
    assert info["current_matches_latest"] is True
    assert [v["note"] for v in info["versions"]] == ["初版", "k を 2 倍に"]
    diff = registry.diff_versions(root, "widget", "v1", "v2")
    assert "-        self.k = k" in diff and "+        self.k = k * 2" in diff


def test_readme_staleness_needs_a_real_gap(root, monkeypatch):
    write_component(root, "widget", WIDGET_V1, "v1")
    # 登録直前に README を書くのが普通の順序なので、直後は陳腐化とみなさない
    assert registry.describe(root, "widget")["readme_stale"] is False
    readme = layout.component_readme_path(root, "widget")
    import os
    import time

    old = time.time() - layout.README_STALE_GRACE_SEC - 3600
    os.utime(readme, (old, old))
    assert registry.describe(root, "widget")["readme_stale"] is True


def test_policy_defaults_are_written_into_blab_json(root):
    marker = read_json(root / layout.ROOT_MARKER)
    assert marker["require_components"] is False
    assert marker["on_missing"] == schema.ON_MISSING_WARN


def test_load_uses_the_active_run_root(tmp_path, monkeypatch):
    """``blab.init(dir=...)`` で別ルートを指定した実行は、そのルートのレジストリを引く。

    cwd からの探索に落ちると、無関係なプロジェクトの component を読んでしまう。
    """
    monkeypatch.delenv("BLAB_DEV", raising=False)
    here = layout.ensure_root(tmp_path / "here" / ".blab")
    there = layout.ensure_root(tmp_path / "there" / ".blab")
    monkeypatch.chdir(tmp_path / "here")
    monkeypatch.setenv("BLAB_DIR", str(here))
    write_component(here, "widget", WIDGET_V1, "v1")
    write_component(there, "widget", WIDGET_V2, "v1")

    assert blab.load("widget").k == 1  # run の外は BLAB_DIR / 探索どおり
    with blab.init(experiment="exp", dir=there) as run:
        assert blab.load("widget").k == 2  # run の中は run のルート
        assert run.components[0]["hash"] == layout.hash_source(
            layout.version_source_path(there, "widget", "v1")
        )

"""Phase 2 — ハッシュ・自動凍結・ラベル・参照の解決・合成パッケージの import。"""

from __future__ import annotations

import sys

import pytest

from blab.components import (
    Meta,
    Resolver,
    freeze,
    hash_dir,
    list_ids,
    parse_ref,
    validate_id,
)
from blab.errors import BlabError
from blab.loader import load_entry, load_module
from conftest import MODEL, write_component


class TestId:
    def test_accepts_python_identifiers(self):
        assert validate_id("resnet18") == "resnet18"
        assert validate_id("standard_trainer") == "standard_trainer"

    @pytest.mark.parametrize("bad", ["res-net", "18resnet", "class", "a b", ""])
    def test_rejects_the_rest(self, bad):
        # 合成モジュール名の一部になるため。
        with pytest.raises(BlabError):
            validate_id(bad)

    def test_dot_entries_are_not_components(self, project):
        (project.components_dir / ".meta").mkdir(exist_ok=True)
        (project.components_dir / ".frozen").mkdir(exist_ok=True)
        assert list_ids(project) == ["cifar100", "resnet18", "standard_trainer"]


class TestHash:
    def test_covers_every_file_including_readme(self, project):
        path = project.components_dir / "resnet18"
        before = hash_dir(path)
        (path / "README.ja.md").write_text("せつめい", encoding="utf-8")
        assert hash_dir(path) != before

    def test_is_not_normalised(self, project):
        path = project.components_dir / "resnet18"
        before = hash_dir(path)
        (path / "main.py").write_text(MODEL + "\n", encoding="utf-8")
        assert hash_dir(path) != before

    def test_ignores_pycache_and_dot_entries(self, project):
        path = project.components_dir / "resnet18"
        before = hash_dir(path)
        (path / "__pycache__").mkdir()
        (path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
        (path / ".ipynb_checkpoints").mkdir()
        (path / ".ipynb_checkpoints" / "main.py").write_text("x", encoding="utf-8")
        assert hash_dir(path) == before

    def test_depends_on_the_path_not_only_the_bytes(self, project):
        a = write_component(project, "a_one", "x = 1\n")
        b = write_component(project, "a_two", "")
        (b / "main.py").unlink()
        (b / "other.py").write_text("x = 1\n", encoding="utf-8")
        assert hash_dir(a) != hash_dir(b)

    def test_tagging_does_not_change_the_hash(self, project):
        """ラベルは本体の外（`.meta/`）に置く（design.md §4.1・A-1 の決定）。"""
        path = project.components_dir / "resnet18"
        before = hash_dir(path)
        meta = Meta.load(project, "resnet18")
        meta.tags = ["model"]
        meta.add_label("v1", before)
        meta.save(project)
        assert hash_dir(path) == before

    def test_promoting_a_label_does_not_change_the_hash(self, project):
        path = project.components_dir / "resnet18"
        before = hash_dir(path)
        (project.labeled_dir / "resnet18" / "v1").mkdir(parents=True)
        (project.labeled_dir / "resnet18" / "v1" / "main.py").write_text("x", encoding="utf-8")
        assert hash_dir(path) == before


class TestFreeze:
    def test_frozen_contents_match_the_hash(self, project):
        """凍結ディレクトリの中身とハッシュが 1:1（layout.md §2.1）。"""
        source = project.components_dir / "standard_trainer"
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        hash = hash_dir(source)
        frozen = freeze(project, "standard_trainer", source, hash)
        assert hash_dir(frozen) == hash
        assert not (frozen / "__pycache__").exists()

    def test_is_idempotent(self, project):
        source = project.components_dir / "resnet18"
        hash = hash_dir(source)
        first = freeze(project, "resnet18", source, hash)
        second = freeze(project, "resnet18", source, hash)
        assert first == second

    def test_happens_automatically_on_resolve(self, project):
        resolver = Resolver(project)
        resolved = resolver.resolve(parse_ref("resnet18"))
        assert resolved.froze_now
        assert resolved.path.is_relative_to(project.frozen_dir)
        # 二度目は凍結済み。
        assert not Resolver(project).resolve(parse_ref("resnet18")).froze_now

    def test_editing_produces_a_new_version(self, project):
        first = Resolver(project).resolve(parse_ref("resnet18"))
        path = project.components_dir / "resnet18" / "main.py"
        path.write_text(MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8")
        second = Resolver(project).resolve(parse_ref("resnet18"))
        assert first.hash != second.hash
        # 古い版も残っている（過去 run が参照しているため）。
        assert first.path.is_dir()

    def test_cache_can_always_be_deleted(self, project):
        import shutil

        before = Resolver(project).resolve(parse_ref("resnet18"))
        shutil.rmtree(project.cache_dir)
        after = Resolver(project).resolve(parse_ref("resnet18"))
        assert after.hash == before.hash  # 作業コピーから再構築できる


class TestParseRef:
    def test_plain(self):
        ref = parse_ref("resnet18")
        assert (ref.id, ref.project, ref.label, ref.hash) == ("resnet18", None, None, None)

    def test_label(self):
        assert parse_ref("resnet18@v3").label == "v3"

    def test_hash(self):
        assert parse_ref("resnet18@sha256:3f9a1c").hash == "sha256:3f9a1c"

    def test_other_project(self):
        ref = parse_ref("seg-baseline/unet@v2")
        assert (ref.project, ref.id, ref.label) == ("seg-baseline", "unet", "v2")

    @pytest.mark.parametrize("bad", ["a@b@c", "a@", "a/b/c", "/unet", "resnet18@sha256:zz"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(BlabError):
            parse_ref(bad)


class TestLabels:
    def test_missing_meta_means_no_labels(self, project):
        meta = Meta.load(project, "resnet18")
        assert not meta.exists
        assert meta.tags == [] and meta.labels == []

    def test_labels_are_immutable(self, project):
        meta = Meta.load(project, "resnet18")
        meta.add_label("v1", "sha256:aaa")
        meta.add_label("v1", "sha256:aaa")  # 同じハッシュなら何も起きない
        with pytest.raises(BlabError, match="ラベルは不変"):
            meta.add_label("v1", "sha256:bbb")

    def test_latest_is_the_last_entry(self, project):
        meta = Meta.load(project, "resnet18")
        meta.add_label("v1", "sha256:aaa")
        meta.add_label("v3", "sha256:ccc")
        assert meta.label_hash("latest") == "sha256:ccc"

    def test_latest_cannot_be_used_as_a_name(self, project):
        with pytest.raises(BlabError, match="予約語"):
            Meta.load(project, "resnet18").add_label("latest", "sha256:aaa")

    def test_id_field_must_match_the_directory(self, project):
        project.meta_dir.mkdir(parents=True, exist_ok=True)
        (project.meta_dir / "resnet18.json").write_text(
            '{"schema_version": 2, "id": "resnet50"}', encoding="utf-8"
        )
        with pytest.raises(BlabError, match="ディレクトリ名"):
            Meta.load(project, "resnet18")

    def test_resolve_by_label(self, project):
        hash = hash_dir(project.components_dir / "resnet18")
        freeze(project, "resnet18", project.components_dir / "resnet18", hash)
        meta = Meta.load(project, "resnet18")
        meta.add_label("v1", hash)
        meta.save(project)
        resolved = Resolver(project).resolve(parse_ref("resnet18@v1"))
        assert resolved.hash == hash
        assert resolved.label == "v1"

    def test_unknown_label_lists_the_known_ones(self, project):
        meta = Meta.load(project, "resnet18")
        meta.add_label("v1", "sha256:aaa")
        meta.save(project)
        with pytest.raises(BlabError, match="v1"):
            Resolver(project).resolve(parse_ref("resnet18@v9"))


class TestResolveByHash:
    def test_prefix_match(self, project):
        resolved = Resolver(project).resolve(parse_ref("resnet18"))
        short = resolved.hash.split(":")[1][:12]
        again = Resolver(project).resolve(parse_ref(f"resnet18@sha256:{short}"))
        assert again.hash == resolved.hash

    def test_ambiguous_prefix_is_an_error(self, project, monkeypatch):
        import blab.components as components

        monkeypatch.setattr(
            components,
            "find_frozen_by_prefix",
            lambda *a, **k: [("sha256:aa1", project.root), ("sha256:aa2", project.root)],
        )
        with pytest.raises(BlabError, match="もっと長く"):
            Resolver(project).resolve(parse_ref("resnet18@sha256:aa"))

    def test_unknown_hash_is_an_error(self, project):
        with pytest.raises(BlabError, match="一致する版がありません"):
            Resolver(project).resolve(parse_ref("resnet18@sha256:deadbeef"))


class TestCrossProject:
    def test_resolves_through_the_global_index(self, project, tmp_path):
        from blab.project import init

        other, _ = init(tmp_path / "seg", name="seg-baseline")
        write_component(other, "unet", MODEL.replace("Resnet18", "Unet"))
        resolved = Resolver(project).resolve(parse_ref("seg-baseline/unet"))
        assert resolved.id == "unet"
        assert resolved.project_uid == other.uid

    def test_unknown_project_says_how_to_fix(self, project):
        with pytest.raises(BlabError, match="blab link"):
            Resolver(project).resolve(parse_ref("nope/unet"))


class TestLoader:
    def test_relative_import_works_without_init_py(self, project):
        resolved = Resolver(project).resolve(parse_ref("standard_trainer"))
        name, obj, module = load_entry(resolved.id, resolved.hash, resolved.path)
        assert name == "StandardTrainer"
        assert module.__name__.startswith("blab._c.standard_trainer__")

    def test_same_hash_is_cached(self, project):
        resolved = Resolver(project).resolve(parse_ref("resnet18"))
        first = load_module(resolved.id, resolved.hash, resolved.path)
        second = load_module(resolved.id, resolved.hash, resolved.path)
        assert first is second

    def test_two_versions_coexist_in_one_process(self, project):
        """ハッシュが名前に入るので、同じ id の別版が衝突しない。"""
        old = Resolver(project).resolve(parse_ref("resnet18"))
        load_module(old.id, old.hash, old.path)
        (project.components_dir / "resnet18" / "main.py").write_text(
            MODEL.replace("pretrained=False", "pretrained=True"), encoding="utf-8"
        )
        new = Resolver(project).resolve(parse_ref("resnet18"))
        assert new.hash != old.hash
        assert load_module(new.id, new.hash, new.path) is not sys.modules[
            f"{old.module_name}.main"
        ]

    def test_failed_import_is_not_left_half_loaded(self, project):
        write_component(project, "broken", "import blab\nraise RuntimeError('boom')\n")
        resolved = Resolver(project).resolve(parse_ref("broken"))
        with pytest.raises(RuntimeError):
            load_module(resolved.id, resolved.hash, resolved.path)
        assert resolved.module_name not in sys.modules
        with pytest.raises(RuntimeError):
            load_module(resolved.id, resolved.hash, resolved.path)

    def test_missing_entry_is_reported(self, project):
        write_component(project, "noentry", "class Plain:\n    pass\n")
        resolved = Resolver(project).resolve(parse_ref("noentry"))
        with pytest.raises(BlabError, match="@blab.entry がありません"):
            load_entry(resolved.id, resolved.hash, resolved.path)

    def test_unresolvable_component_module_explains_spawn(self):
        """子プロセスで合成モジュール名が解けないときに、理由が分かること。"""
        import importlib

        from blab.loader import _ensure_namespace

        _ensure_namespace()
        with pytest.raises(ImportError, match="spawn"):
            importlib.import_module("blab._c.ghost__00000000.main")

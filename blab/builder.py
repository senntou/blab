"""Builder と観測（design.md §6）。

**blab は子 component を実体化しない。作り方（Builder）だけを渡す。**

    def execute(self, run):
        train = self.dataset.build(split="train")      # ここで初めて実体化
        val   = self.dataset.build(split="val")        # 同じ Builder から 2 個作れる
        model = self.model.build(k=train.n_classes)    # 実行時の値を渡せる

`.build(**kwargs)` を呼んだ瞬間に、**YAML に書かれた引数とコードから渡された引数を
合体させて**実体化する。衝突したらコード側が勝つ（YAML は既定値の位置づけ）。

記録するのは**`.build()` が実際に呼ばれたときの合体後の引数**である。

    {"args": {"pretrained": false, "k": 100},
     "args_from": {"pretrained": "yaml", "k": "runtime"}}

`k: 100` は誰も宣言していない。**実際にそう渡されたから、そう記録されている。**

> YAML は「構成の形」を宣言する。「最終的な値」は観測する。
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import BlabError
from .preflight import Prepared
from .spec import ROOT_PATH, DataRef

#: `args_from` の値（layout.md §5.2）。
FROM_YAML = "yaml"
FROM_OVERRIDE = "override"
FROM_RUNTIME = "runtime"
FROM_DEFAULT = "default"


def child_key(parent_path: str, child_path: str) -> str:
    """`run` と `run.transforms[0]` から `transforms[0]` を得る。"""
    if child_path.startswith(parent_path + "."):
        return child_path[len(parent_path) + 1 :]
    if child_path.startswith(parent_path):
        return child_path[len(parent_path) :].lstrip(".")
    return child_path


def ref_label(path: str, index: int) -> str:
    """`$ref` に使う名前。`run.dataset` の 0 番目の build なら `dataset#0`。"""
    relative = path[len(ROOT_PATH) + 1 :] if path.startswith(ROOT_PATH + ".") else path
    return f"{relative}#{index}"


# ------------------------------------------------------------------ 同一性


class _Identity:
    """`.build()` が返したオブジェクトを覚えておく。

    **weakref で持つ。** `id()` は解放後に再利用されるので、死んだオブジェクトの id と
    新しいオブジェクトの id が一致して誤った `$ref` を書いてしまう。weakref を張れない
    型（`int` や一部の C 拡張）だけ強参照で抱える。
    """

    def __init__(self) -> None:
        self._entries: list[tuple[Any, str]] = []

    def remember(self, obj: Any, label: str) -> None:
        try:
            getter = weakref.ref(obj)
        except TypeError:
            getter = lambda captured=obj: captured  # noqa: E731 - weakref 不可な型
        self._entries.append((getter, label))

    def label_for(self, obj: Any) -> str | None:
        for getter, label in self._entries:
            if getter() is obj:
                return label
        return None


# ------------------------------------------------------------------ 観測


@dataclass
class Build:
    """1 回の `.build()`。"""

    args: dict[str, Any]
    args_from: dict[str, str]


@dataclass
class Observed:
    """component 1 つ分の観測結果。`resolved.yaml` の 1 ノードになる。"""

    prepared: Prepared
    path: str
    builds: list[Build] = field(default_factory=list)
    children: dict[str, "Observed"] = field(default_factory=dict)

    def add(self, build: Build) -> int:
        """同一引数の build は 1 件に畳む。畳んだ先の添字を返す。"""
        for index, existing in enumerate(self.builds):
            if existing.args == build.args and existing.args_from == build.args_from:
                return index
        self.builds.append(build)
        return len(self.builds) - 1


class Recorder:
    """木全体の観測結果を持つ。`resolved.yaml` はここから作る。"""

    def __init__(
        self,
        root: Prepared,
        *,
        overrides: dict[str, Any] | None = None,
        data: dict[str, Path] | None = None,
    ) -> None:
        self.overrides = overrides or {}
        self.identity = _Identity()
        #: 解決済みの外部ファイル。記録では実パスではなく論理名で残す。
        self.data_names = {Path(v): k for k, v in (data or {}).items()}
        self.root = Observed(prepared=root, path=root.path)
        self._by_path: dict[str, Observed] = {root.path: self.root}

    # ---- 構造

    def observed_for(self, prepared: Prepared, parent: Observed | None) -> Observed:
        """`Prepared` に対応する `Observed` を（無ければ作って）返す。"""
        existing = self._by_path.get(prepared.path)
        if existing is not None:
            return existing
        node = Observed(prepared=prepared, path=prepared.path)
        self._by_path[prepared.path] = node
        if parent is not None:
            parent.children[child_key(parent.path, prepared.path)] = node
        return node

    # ---- 記録

    def note(self, node: Observed, merged: dict, sources: dict, obj: Any) -> None:
        build = Build(
            args=self.encode_mapping(merged),
            args_from={k: sources[k] for k in sorted(sources)},
        )
        index = node.add(build)
        self.identity.remember(obj, ref_label(node.path, index))

    def source_of(self, node_path: str, name: str) -> str:
        """YAML 由来の引数が `--set` で上書きされたものかを見る。"""
        return FROM_OVERRIDE if f"{node_path}.{name}" in self.overrides else FROM_YAML

    # ---- 値の符号化（layout.md §5.2）

    def encode_mapping(self, values: dict) -> dict:
        return {str(k): self.encode(v) for k, v in sorted(values.items(), key=lambda kv: str(kv[0]))}

    def encode(self, value: Any) -> Any:
        """記録できる形に落とす。**推測しない。**

        | 引数の種類 | 記録 |
        | --- | --- |
        | JSON 化できる値 | そのまま |
        | `.build()` が返したオブジェクト | 同一性から検出して `{"$ref": ...}` |
        | 子 Builder | `{"$child": ...}`（実体は `children` 側にある） |
        | 外部ファイル | `{"$data": 論理名}` |
        | それ以外の live object | `{"$unrecorded": 型名}` |
        """
        if isinstance(value, Builder):
            return {"$child": child_key(ROOT_PATH, value.path) if value.path != ROOT_PATH else ROOT_PATH}
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Path):
            name = self.data_names.get(value)
            return {"$data": name} if name else str(value)
        label = self.identity.label_for(value)
        if label is not None:
            return {"$ref": label}
        if isinstance(value, (list, tuple)):
            return [self.encode(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self.encode(v) for k, v in value.items()}
        return {"$unrecorded": type(value).__name__}


# ------------------------------------------------------------------ Builder


class Builder:
    """YAML に書かれた子 component の**作り方**。

    **ルールは 1 本に統一する。YAML の子は常に Builder。** 実行時引数が要らない場合も
    `self.metric.build()` と書く。「Builder のときと実体のときがある」という例外を作る
    ほうが遥かに悪い。

    メソッド名を `__call__` ではなく `.build()` にしたのは、PyTorch で `model(x)` が
    順伝播を意味するためである（`self.model(k=10)` は順伝播に見える）。
    """

    __slots__ = ("_prepared", "_recorder", "_node", "_args", "_sources")

    def __init__(self, prepared: Prepared, recorder: Recorder, node: Observed) -> None:
        self._prepared = prepared
        self._recorder = recorder
        self._node = node
        # YAML 由来の引数は 1 度だけ実体化する（同じ子 Builder を使い回すため）。
        self._args = {
            name: _materialize(value, recorder, node)
            for name, value in prepared.args.items()
        }
        self._sources = {
            name: recorder.source_of(prepared.path, name) for name in self._args
        }

    @property
    def id(self) -> str:
        return self._prepared.resolved.id

    @property
    def path(self) -> str:
        return self._prepared.path

    def __repr__(self) -> str:
        return f"<blab.Builder {self._prepared.resolved.id} at {self._prepared.path}>"

    def build(self, **runtime: Any) -> Any:
        """実体化する。YAML の引数と、ここで渡した引数を合体させる。"""
        merged, sources = self._merge(runtime)
        obj = self._prepared.entry_obj(**merged)
        self._recorder.note(self._node, merged, sources, obj)
        return obj

    def _merge(self, runtime: dict) -> tuple[dict, dict]:
        merged = dict(self._args)
        sources = dict(self._sources)

        for name, value in self._prepared.defaults.items():
            if name not in merged:
                merged[name] = value
                sources[name] = FROM_DEFAULT

        for name, value in runtime.items():
            # コード側が勝つ（YAML は既定値の位置づけ）。
            merged[name] = value
            sources[name] = FROM_RUNTIME

        missing = [n for n in self._prepared.runtime_required if n not in merged]
        if missing:
            raise BlabError(
                f"component {str(self._prepared.node.ref)!r}（{self._prepared.path}）を"
                f"build できません。必須の引数 {', '.join(missing)} が YAML にもデフォルトにも"
                f"無く、.build() にも渡されていません。"
                f"`.build({missing[0]}=...)` のように実行時に渡してください"
            )
        if not self._prepared.accepts_kwargs:
            unknown = sorted(set(runtime) - _nameable(self._prepared))
            if unknown:
                raise BlabError(
                    f"component {str(self._prepared.node.ref)!r} の entry "
                    f"{self._prepared.entry_name} に引数 {', '.join(unknown)} はありません"
                )
        return merged, sources


def _nameable(prepared: Prepared) -> set[str]:
    import inspect

    try:
        params = inspect.signature(prepared.entry_obj).parameters
    except (TypeError, ValueError):
        return set()
    return {
        name
        for name, p in params.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


def _materialize(value: Any, recorder: Recorder, parent: Observed) -> Any:
    """YAML の値を、実行時に component へ渡す形にする。

    子 component は Builder になり、`{data: NAME}` は `pathlib.Path` になる。
    """
    if isinstance(value, Prepared):
        node = recorder.observed_for(value, parent)
        return Builder(value, recorder, node)
    if isinstance(value, DataRef):
        path = recorder.data_names
        for resolved, name in path.items():
            if name == value.name:
                return resolved
        raise BlabError(f"外部ファイル {value.name!r} が解決されていません")
    if isinstance(value, list):
        return [_materialize(v, recorder, parent) for v in value]
    if isinstance(value, dict):
        return {k: _materialize(v, recorder, parent) for k, v in value.items()}
    return value


def instantiate_root(recorder: Recorder) -> Any:
    """root を実体化する。root だけは Builder を経由せず blab が直接作る。"""
    prepared = recorder.root.prepared
    args = {
        name: _materialize(value, recorder, recorder.root)
        for name, value in prepared.args.items()
    }
    sources = {name: recorder.source_of(prepared.path, name) for name in args}
    for name, value in prepared.defaults.items():
        if name not in args:
            args[name] = value
            sources[name] = FROM_DEFAULT

    obj = prepared.entry_obj(**args)
    recorder.note(recorder.root, args, sources, obj)
    return obj

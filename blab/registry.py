"""レジストリ: component の解決・整合性ゲート・import・binding の観測。

design.md §3 の実装。役割は 3 つある。

1. **解決** — ``"id@version"`` をレジストリ上のファイルに解決する
2. **整合性ゲート** — ``current/`` が登録済み版と違うなら止める（§3.4）。
   記録系と違ってここは握らない。古いコードを黙って実行するのは記録の失敗より重い
3. **観測** — 何を何の引数で load したかを binding として記録する（§3.6）。
   宣言させず、実際に起きた load から観測する

import 元は必ず凍結された ``versions/<v>/`` であって ``current/`` ではない
（``dev`` を除く）。したがって「記録された version と実際に動いたバイト列が違う」は
原理的に起こらない。
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from . import layout
from .entry import resolve_entry
from .errors import BlabError, BlabUsageError
from .io import read_json, write_json_atomic

#: 合成モジュール名の親。``sys.modules`` のキー衝突を避けるための名前空間（§3.4）。
MODULE_PREFIX = "blab._components"


# ------------------------------------------------------------ spec の解析


def parse_spec(spec: str) -> tuple[str, str | None]:
    """``"id"`` / ``"id@version"`` を ``(id, version | None)`` に分解する。"""
    if not isinstance(spec, str) or not spec.strip():
        raise BlabUsageError("blab.load() には component の spec 文字列が必要です")
    text = spec.strip()
    if text.count("@") > 1:
        raise BlabUsageError(f"invalid spec {spec!r}: '@' は 1 つだけです")
    id, _, version = text.partition("@")
    layout.check_component_id(id)
    if not version:
        return id, None
    if version == "latest":
        return id, None  # latest は「versions 配列の末尾」の別名
    return id, layout.check_version_id(version)


def dev_requested(dev: bool | None, id: str) -> tuple[bool, bool]:
    """dev 実行するかを ``(dev するか, 明示的な指定か)`` で返す。

    - ``blab.load(..., dev=True)`` — その load だけ。**明示的**
    - ``BLAB_DEV=1`` — このプロセスの全 component。**包括的**
    - ``BLAB_DEV=id1,id2`` — 名指しした component だけ。**包括的**

    包括的な指定は「pin していないものは編集中のものを使う」という意味に留める。
    version を明示した load は意図の表明なので、そちらが勝つ（§3.4）。
    """
    if dev is not None:
        return bool(dev), True
    raw = os.environ.get("BLAB_DEV", "").strip()
    if raw in ("", "0", "false", "False"):
        return False, False
    if raw in ("1", "true", "True", "all", "*"):
        return True, False
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return id in wanted, False


def dev_enabled(dev: bool | None, id: str = "") -> bool:
    """後方互換の薄いラッパ。"""
    return dev_requested(dev, id)[0]


# ------------------------------------------------------------ component.json


def read_component(root: Path, id: str) -> dict:
    """``component.json`` を読む。無ければエラー（未登録のものは使えない）。"""
    path = layout.component_meta_path(root, id)
    meta = read_json(path)
    if not isinstance(meta, dict):
        raise BlabError(
            f"component {id!r} がレジストリにありません（{path} が無い）。"
            f"`blab new {id}` で雛形を作り、`blab register {id} v1` で登録してください"
        )
    meta.setdefault("id", id)
    versions = meta.get("versions")
    meta["versions"] = versions if isinstance(versions, list) else []
    tags = meta.get("tags")
    meta["tags"] = [str(t) for t in tags] if isinstance(tags, list) else []
    return meta


def versions_of(meta: dict) -> list[dict]:
    return [v for v in meta.get("versions", []) if isinstance(v, dict) and v.get("id")]


def find_version(meta: dict, version: str) -> dict | None:
    for v in versions_of(meta):
        if v.get("id") == version:
            return v
    return None


def latest_version(meta: dict) -> dict | None:
    """``versions`` 配列の**末尾**。登録順が唯一の順序（§3.2）。"""
    versions = versions_of(meta)
    return versions[-1] if versions else None


# ------------------------------------------------------------ 解決と整合性ゲート


@dataclass(frozen=True)
class Resolved:
    """load が使うソースの所在。"""

    id: str
    version: str | None  # dirty のときは None
    source: Path
    hash: str
    dirty: bool = False
    entry_name: str | None = None


def resolve(root: Path, spec: str, *, dev: bool | None = None) -> Resolved:
    """spec をソースファイルに解決する。整合性ゲートはここで効く。"""
    id, version = parse_spec(spec)
    is_dev, dev_is_explicit = dev_requested(dev, id)
    current = layout.current_source_path(root, id)

    if is_dev and version is not None:
        if dev_is_explicit:
            raise BlabUsageError(
                f"dev 実行は current/ から import するので version 指定と両立しません（{spec!r}）"
            )
        # BLAB_DEV による包括指定。pin してある load は version をそのまま使う。
        is_dev = False

    if is_dev:
        if not current.is_file():
            raise BlabError(f"dev 実行するには {current} が必要です")
        return Resolved(id=id, version=None, source=current, hash=layout.hash_source(current), dirty=True)

    meta = read_component(root, id)
    if version is None:
        picked = latest_version(meta)
        if picked is None:
            raise BlabError(
                f"component {id!r} は登録済み version を持ちません。"
                f"`blab register {id} v1` で登録してください（BLAB_DEV=1 で current/ を直接使えます）"
            )
        explicit = False
    else:
        picked = find_version(meta, version)
        if picked is None:
            known = ", ".join(v["id"] for v in versions_of(meta)) or "（無し）"
            raise BlabError(f"component {id!r} に version {version!r} はありません。登録済み: {known}")
        explicit = True

    resolved_version = str(picked["id"])
    source = layout.version_source_path(root, id, resolved_version)
    if not source.is_file():
        raise BlabError(
            f"component {id}@{resolved_version} のソースがありません（{source}）。"
            "component.json と versions/ が食い違っています"
        )
    frozen_hash = str(picked.get("hash") or "")
    actual_hash = layout.hash_source(source)
    if frozen_hash and frozen_hash != actual_hash:
        raise BlabError(
            f"component {id}@{resolved_version} の versions/ の内容が component.json の "
            f"hash と一致しません（凍結されたはずのファイルが書き換えられています）"
        )

    # 整合性ゲート（§3.4）。「編集したつもりで古いコードを黙って実行してしまう」事故を防ぐ。
    # version を明示した load は「その版を使う」という表明なので、ここでは検査しない。
    if not explicit and current.is_file():
        current_hash = layout.hash_source(current)
        if current_hash != actual_hash:
            raise BlabError(
                f"current/{id}.py は {resolved_version} と異なります。"
                f"`blab register {id} <新version>` で登録してから実行してください"
                f"（開発中は BLAB_DEV=1 で current/ をそのまま使えます）"
            )

    return Resolved(
        id=id,
        version=resolved_version,
        source=source,
        hash=actual_hash,
        dirty=False,
        entry_name=meta.get("entry") or None,
    )


# ------------------------------------------------------------ import


def module_name_for(resolved: Resolved) -> str:
    if resolved.dirty:
        return f"{MODULE_PREFIX}.{resolved.id}__dev_{resolved.hash[-8:]}"
    return f"{MODULE_PREFIX}.{resolved.id}__{resolved.version}"


def import_source(resolved: Resolved) -> ModuleType:
    """ソースを合成モジュール名で載せる。同一 (id, version, 内容) はキャッシュを再利用する。

    ``<id>__<version>`` はレジストリをまたぐと一意でない（別プロジェクトの同名 version）。
    キャッシュにあるものが**別のファイル**だった場合はハッシュを足した名前で載せ直す。
    キャッシュの取り違えは「表示している version と実際に動いたバイト列が違う」を
    引き起こすので、名前の見た目より正しさを採る。
    """
    name = module_name_for(resolved)
    cached = sys.modules.get(name)
    if cached is not None:
        cached_file = getattr(cached, "__file__", None)
        if cached_file and Path(cached_file) == Path(resolved.source):
            return cached
        name = f"{name}_{resolved.hash[-8:]}"
        cached = sys.modules.get(name)
        if cached is not None:
            return cached
    spec = importlib.util.spec_from_file_location(name, resolved.source)
    if spec is None or spec.loader is None:
        raise BlabError(f"component {resolved.id} を import できません（{resolved.source}）")
    module = importlib.util.module_from_spec(spec)
    module.__blab_component__ = resolved.id
    module.__blab_version__ = resolved.version
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# ------------------------------------------------------------ binding の観測


@dataclass
class Binding:
    """「この run で何を何の引数で使ったか」という観測結果（§4.1）。"""

    index: int
    id: str
    version: str | None
    role: str | None
    hash: str
    loaded_by: int | None = None
    args: dict = field(default_factory=dict)
    dirty: bool = False
    failed: bool = False
    #: どの entrypoint の実行中に load されたか（``entrypoints`` 配列の添字）。
    #: 後から blab.open() で書き足した run で、どのスクリプトが何を使ったかを区別する。
    entrypoint: int | None = None
    #: dirty 実行時のソース。run への snapshot 用に持つだけで、JSON には出さない。
    source: Path | None = field(default=None, repr=False)
    #: dirty 実行のソースを run に snapshot した先（``code/_components/<id>.py``）。
    snapshot: str | None = None

    def to_json(self) -> dict:
        out: dict[str, Any] = {
            "index": self.index,
            "id": self.id,
            "version": self.version,
            "role": self.role,
            "hash": self.hash,
            "loaded_by": self.loaded_by,
            "args": self.args,
        }
        if self.entrypoint is not None:
            out["entrypoint"] = self.entrypoint
        # dirty / failed は既定 false。真のときだけ出す（既定値で埋めない）。
        if self.dirty:
            out["dirty"] = True
            out["snapshot"] = self.snapshot
        if self.failed:
            out["failed"] = True
        return out


#: 実体化の入れ子を追う load スタック。先頭が ``loaded_by``（§3.6）。
_load_stack: contextvars.ContextVar[tuple[int, ...]] = contextvars.ContextVar(
    "blab_load_stack", default=()
)


class BindingRecorder:
    """1 run 分の binding を観測して溜める。

    ``index`` は **load 開始順**に振る。この採番により ``loaded_by`` は必ず自分より
    小さい index を指すので、閲覧層はトポロジカルソートなしに構成の木を描ける（§4.1）。
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        start_index: int = 0,
        entrypoint: int | None = None,
        on_change=None,
    ) -> None:
        #: この run が書き込んでいるルート。load の解決先をここに合わせる
        #: （``blab.init(dir=...)`` で cwd と違うルートを指定した場合のため）。
        self.root = Path(root) if root is not None else None
        #: 既に記録済みの binding がある run を開き直したとき、index を続きから振る。
        self._start_index = int(start_index)
        #: この recorder が回っている間の entrypoint（``entrypoints`` の添字）。
        self._entrypoint = entrypoint
        #: binding が増えた / 状態が変わったときに呼ばれる。記録層が永続化に使う。
        self._on_change = on_change
        self._lock = threading.Lock()
        self._bindings: list[Binding] = []
        self._by_key: dict[tuple, int] = {}
        #: load が返したオブジェクト → binding index。``$ref`` の検出に使う。
        #: weakref を張れるものは弱参照で持ち、死んだら引かない（id の再利用による
        #: 誤参照を避ける）。張れないものだけ強参照で抱える。
        self._weak: dict[int, tuple[weakref.ref, int]] = {}
        self._strong: dict[int, tuple[Any, int]] = {}

    @property
    def bindings(self) -> list[Binding]:
        with self._lock:
            return list(self._bindings)

    def __len__(self) -> int:
        with self._lock:
            return len(self._bindings)

    def to_json(self) -> list[dict]:
        return [b.to_json() for b in self.bindings]

    # -- $ref の検出 --------------------------------------------------

    def ref_of(self, obj: Any) -> int | None:
        key = id(obj)
        entry = self._weak.get(key)
        if entry is not None:
            ref, index = entry
            alive = ref()
            if alive is None:
                self._weak.pop(key, None)  # 死んだ id は使い回されるので忘れる
            elif alive is obj:
                return index
        entry = self._strong.get(key)
        if entry is not None and entry[0] is obj:
            return entry[1]
        return None

    def remember(self, obj: Any, index: int) -> None:
        try:
            self._weak[id(obj)] = (weakref.ref(obj), index)
        except TypeError:
            self._strong[id(obj)] = (obj, index)

    # -- 引数の記録 ---------------------------------------------------

    def encode_args(self, args: dict) -> dict:
        return {str(k): self.encode_value(v) for k, v in args.items()}

    def encode_value(self, value: Any) -> Any:
        """§3.6 の 3 分類。JSON 化できる値 / ``$ref`` / ``$unrecorded``。"""
        ref = self.ref_of(value)
        if ref is not None:
            return {"$ref": ref}
        if value is None or isinstance(value, (bool, str)):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            if math.isfinite(value):
                return value
            return {"$unrecorded": f"float({value})"}
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if not isinstance(k, str):
                    return {"$unrecorded": f"dict[{type(k).__name__}]"}
                out[k] = self.encode_value(v)
            return out
        if isinstance(value, (list, tuple)):
            return [self.encode_value(v) for v in value]
        if isinstance(value, Path):
            return str(value)
        item = getattr(value, "item", None)  # numpy スカラなど 0 次元の値
        if callable(item):
            try:
                scalar = item()
            except Exception:  # noqa: BLE001 - 記録のための試行なので失敗は無視する
                scalar = None
            if isinstance(scalar, (bool, int, float, str)) and (
                not isinstance(scalar, float) or math.isfinite(scalar)
            ):
                return scalar
        return {"$unrecorded": type(value).__name__}

    # -- binding の追加 -----------------------------------------------

    def begin(
        self,
        resolved: Resolved,
        *,
        role: str | None,
        args: dict,
        loaded_by: int | None,
    ) -> Binding:
        """load 開始時に binding を確保する。完全一致は 1 件に畳む（§3.6）。"""
        encoded = self.encode_args(args)
        key = (
            resolved.id,
            resolved.version,
            resolved.dirty,
            role,
            loaded_by,
            json.dumps(encoded, sort_keys=True, ensure_ascii=False),
        )
        with self._lock:
            position = self._by_key.get(key)
            if position is not None:
                return self._bindings[position]
            binding = Binding(
                index=self._start_index + len(self._bindings),
                id=resolved.id,
                version=resolved.version,
                role=role,
                hash=resolved.hash,
                loaded_by=loaded_by,
                args=encoded,
                dirty=resolved.dirty,
                entrypoint=self._entrypoint,
                source=resolved.source if resolved.dirty else None,
            )
            self._bindings.append(binding)
            self._by_key[key] = len(self._bindings) - 1
            return binding

    def changed(self) -> None:
        """記録層に「書き直してほしい」と伝える。失敗しても学習は落とさない。"""
        if self._on_change is None:
            return
        try:
            self._on_change(self)
        except Exception as e:  # noqa: BLE001 - 記録の失敗を学習に波及させない
            from .errors import warn

            warn(f"components の書き込みに失敗しました: {e!r}")


_recorder_lock = threading.Lock()
_recorder: BindingRecorder | None = None


def set_recorder(recorder: BindingRecorder | None) -> BindingRecorder | None:
    """観測先を差し替える。記録層（Run）が所有する。"""
    global _recorder
    with _recorder_lock:
        previous, _recorder = _recorder, recorder
    return previous


def current_recorder() -> BindingRecorder | None:
    with _recorder_lock:
        return _recorder


@contextlib.contextmanager
def recording(recorder: BindingRecorder | None = None):
    """テストや run の外での観測用。抜けると元に戻す。"""
    recorder = recorder if recorder is not None else BindingRecorder()
    previous = set_recorder(recorder)
    try:
        yield recorder
    finally:
        set_recorder(previous)


# ------------------------------------------------------------ load


def load(
    spec: str,
    *,
    role: str | None = None,
    dev: bool | None = None,
    args: dict | None = None,
    root: str | os.PathLike | None = None,
    **kwargs: Any,
) -> Any:
    """component を実体化して返す（§3.6）。

    entry を args で呼び、その戻り値を返す。run の中なら binding を記録し、
    run の外なら単に記録しない。
    """
    if args is not None and not isinstance(args, dict):
        raise BlabUsageError("blab.load(args=...) には dict を渡してください")
    merged: dict[str, Any] = dict(args or {})
    merged.update(kwargs)

    resolved = resolve(_root_for_load(root), spec, dev=dev)

    recorder = current_recorder()
    stack = _load_stack.get()
    binding = None
    if recorder is not None:
        loaded_by = stack[-1] if stack else None
        binding = recorder.begin(resolved, role=role, args=merged, loaded_by=loaded_by)

    token = _load_stack.set(stack + (binding.index,)) if binding is not None else None
    try:
        module = import_source(resolved)
        _, entry_obj = resolve_entry(module, resolved.id)
        obj = entry_obj(**merged)
    except BaseException:
        # entry が落ちた load も残す。何が起きたかを隠さない（§3.6）。
        if binding is not None:
            binding.failed = True
            recorder.changed()  # type: ignore[union-attr]
        raise
    finally:
        if token is not None:
            _load_stack.reset(token)

    if recorder is not None and binding is not None:
        recorder.remember(obj, binding.index)
        recorder.changed()
    return obj


def _root_for_load(root: str | os.PathLike | None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    # 実行中の run があれば、その run が書いているルートのレジストリを使う。
    # cwd からの探索に落とすと、blab.init(dir=...) で別ルートを指定した実行が
    # 無関係なレジストリを引いてしまう。
    recorder = current_recorder()
    if recorder is not None and recorder.root is not None:
        return recorder.root
    found = layout.resolve_root(create=False)
    if not layout.is_root(found):
        raise BlabError(
            f"blab のルートが見つかりません（{found} に {layout.ROOT_MARKER} が無い）。"
            "BLAB_DIR を設定するか、blab.init() の中で load してください"
        )
    return found


# ------------------------------------------------------------ 登録フロー（§3.7）

_TEMPLATE_SOURCE = '''"""{id} — 何をするものかを 1 行で。

規則（design.md §3.3）
- 自己完結した 1 ファイル。相対 import は使えない
- import 先はサードパーティのパッケージか blab.load() で引く別の component だけ
- 入口は @blab.entry で 1 つだけ
- 他の component のクラスを継承しない（合成のみ）
"""

import blab


@blab.entry
class {cls}:
    def __init__(self):
        pass
'''

_TEMPLATE_README = """# {id}

## これは何か

（version をまたいで安定して育つ「これは何か」を書く。version ごとの差分は
`component.json` の note に 1 行で書く）

## 使い方

```python
obj = blab.load("{id}")
```
"""


def _class_name(id: str) -> str:
    return "".join(part.title() for part in id.split("_") if part) or "Entry"


def create_component(root: Path, id: str, *, tags: list[str] | None = None) -> Path:
    """``blab new`` — ``current/<id>.py`` と ``README.ja.md`` の雛形を作る。"""
    layout.check_component_id(id)
    cdir = layout.component_dir(root, id)
    source = layout.current_source_path(root, id)
    if source.exists():
        raise BlabError(f"{source} は既にあります")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_TEMPLATE_SOURCE.format(id=id, cls=_class_name(id)), encoding="utf-8")
    readme = layout.component_readme_path(root, id)
    if not readme.exists():
        readme.write_text(_TEMPLATE_README.format(id=id), encoding="utf-8")
    meta_path = layout.component_meta_path(root, id)
    if not meta_path.exists():
        write_json_atomic(
            meta_path,
            {
                "schema_version": layout.SCHEMA_VERSION,
                "id": id,
                "entry": None,
                "tags": list(tags or []),
                "versions": [],
            },
        )
    return cdir


def check_entry_in_subprocess(source: Path, id: str) -> str:
    """別プロセスで import し、entry がちょうど 1 つあることを検査して名前を返す。

    登録は単なるコピーではなく検証を伴う（§3.3）。今のプロセスの ``sys.modules`` を
    汚さないため、また import 時の副作用を登録の外に閉じるために別プロセスで行う。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "blab.registry", str(source), id],
        capture_output=True,
        text=True,
        env=_subprocess_env(source),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BlabError(f"component {id!r} の検査に失敗しました:\n{detail}")
    try:
        return str(json.loads(proc.stdout)["entry"])
    except (ValueError, KeyError, TypeError) as e:
        raise BlabError(f"component {id!r} の検査結果を読めません: {proc.stdout!r}") from e


def _subprocess_env(source: Path) -> dict:
    """子プロセスで ``import blab`` が必ず通るようにした環境。

    blab が site-packages に入っていない状態（リポジトリ直下での開発）でも
    登録の検査が動くように、blab パッケージの親を ``PYTHONPATH`` に足す。
    """
    env = dict(os.environ)
    parent = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    parts = [parent, *(p for p in existing.split(os.pathsep) if p)]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    # 検査中の module-level load がルートを引けるように、ルートを渡しておく。
    # source は <root>/components/<id>/current/<id>.py なので 4 つ上がルート。
    parents = source.resolve().parents
    if len(parents) >= 4 and layout.is_root(parents[3]):
        env["BLAB_DIR"] = str(parents[3])
    return env


def register(
    root: Path,
    id: str,
    version: str,
    *,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """``current/`` を凍結して登録する（§3.7）。"""
    layout.check_component_id(id)
    layout.check_version_id(version)
    source = layout.current_source_path(root, id)
    if not source.is_file():
        raise BlabError(f"{source} がありません。`blab new {id}` で雛形を作ってください")

    meta = read_json(layout.component_meta_path(root, id))
    if not isinstance(meta, dict):
        meta = {
            "schema_version": layout.SCHEMA_VERSION,
            "id": id,
            "entry": None,
            "tags": [],
            "versions": [],
        }
    meta.setdefault("versions", [])
    if not isinstance(meta["versions"], list):
        meta["versions"] = []

    if find_version(meta, version) is not None:
        raise BlabError(f"{id}@{version} は既に登録されています（登録済み version は不変）")

    digest = layout.hash_source(source)
    same = [v["id"] for v in versions_of(meta) if v.get("hash") == digest]
    if same:
        raise BlabError(
            f"current/{id}.py は {', '.join(same)} と同一内容です（登録すべき変更がありません）"
        )

    entry_name = check_entry_in_subprocess(source, id)

    target = layout.version_source_path(root, id, version)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    record = {
        "id": version,
        "hash": digest,
        "note": note or "",
        "registered_at": layout.isoformat(layout.now()),
    }
    meta["schema_version"] = layout.SCHEMA_VERSION
    meta["id"] = id
    meta["entry"] = entry_name
    if tags is not None:
        meta["tags"] = list(tags)
    meta.setdefault("tags", [])
    meta["versions"].append(record)
    write_json_atomic(layout.component_meta_path(root, id), meta)
    return record


# ------------------------------------------------------------ 閲覧の補助


def source_of(root: Path, id: str, version: str | None = None) -> tuple[str, Path]:
    """ソースを読む。``version`` 省略時は latest、``"current"`` で編集中のものを読む。"""
    if version == "current":
        path = layout.current_source_path(root, id)
        if not path.is_file():
            raise BlabError(f"{path} がありません")
        return path.read_text(encoding="utf-8"), path
    meta = read_component(root, id)
    if version is None:
        picked = latest_version(meta)
        if picked is None:
            raise BlabError(f"component {id!r} は登録済み version を持ちません")
        version = str(picked["id"])
    elif find_version(meta, version) is None:
        raise BlabError(f"component {id!r} に version {version!r} はありません")
    path = layout.version_source_path(root, id, version)
    if not path.is_file():
        raise BlabError(f"{path} がありません")
    return path.read_text(encoding="utf-8"), path


def readme_of(root: Path, id: str) -> tuple[str | None, float | None]:
    """``README.ja.md`` の本文と mtime。無ければ ``(None, None)``。"""
    path = layout.component_readme_path(root, id)
    if not path.is_file():
        return None, None
    return path.read_text(encoding="utf-8"), path.stat().st_mtime


def readme_is_stale(root: Path, id: str) -> bool | None:
    """README が最新 version の登録より古いか（陳腐化マーカー、§3.2）。

    判定材料が無い場合は ``None``（黙って false にしない）。
    """
    _, mtime = readme_of(root, id)
    if mtime is None:
        return None
    picked = latest_version(read_component(root, id))
    registered = (picked or {}).get("registered_at")
    if not registered:
        return None
    from datetime import datetime

    try:
        ts = datetime.fromisoformat(str(registered)).timestamp()
    except ValueError:
        return None
    return mtime < ts - layout.README_STALE_GRACE_SEC


def diff_versions(root: Path, id: str, a: str, b: str) -> str:
    """version 間の unified diff。"""
    import difflib

    text_a, path_a = source_of(root, id, a)
    text_b, path_b = source_of(root, id, b)
    return "".join(
        difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"{id}@{a}",
            tofile=f"{id}@{b}",
        )
    ) or f"（{id}@{a} と {id}@{b} は同一内容）\n"


def describe(root: Path, id: str) -> dict:
    """component 一覧 / 詳細に出す情報を組む。"""
    meta = read_component(root, id)
    picked = latest_version(meta)
    current = layout.current_source_path(root, id)
    current_hash = layout.hash_source(current) if current.is_file() else None
    return {
        "id": id,
        "entry": meta.get("entry"),
        "tags": meta.get("tags", []),
        "versions": versions_of(meta),
        "latest": (picked or {}).get("id"),
        "registered_at": (picked or {}).get("registered_at"),
        "current_hash": current_hash,
        # current が latest と一致しているか。current が無い場合は None（未判定）。
        "current_matches_latest": (
            None if current_hash is None or picked is None else current_hash == picked.get("hash")
        ),
        "readme_stale": readme_is_stale(root, id),
    }


# ------------------------------------------------------------ 別プロセスの entry 検査


def _main(argv: list[str]) -> int:
    """``python -m blab.registry <source> <id>`` — entry を検査して JSON を出す。"""
    if len(argv) != 2:
        print("usage: python -m blab.registry <source.py> <id>", file=sys.stderr)
        return 2
    source, id = Path(argv[0]), argv[1]
    resolved = Resolved(id=id, version=None, source=source, hash=layout.hash_source(source), dirty=True)
    try:
        module = import_source(resolved)
    except BaseException as e:  # noqa: BLE001 - 検査結果として報告する
        print(f"import できません: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    try:
        name, _ = resolve_entry(module, id)
    except BlabError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps({"entry": name}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))

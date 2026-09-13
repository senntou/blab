"""実験 YAML の読み込み（layout.md §3）。

規則は 1 つ。**`use` キーを持つマッピングは component 参照、それ以外は普通の値。**

- **文字列単体の省略形は認めない。** `metric: top1_accuracy` を参照とみなすと
  `optimizer: adam` のような普通の文字列引数と区別が付かず、「その id の component が
  存在すれば参照」という規則になってしまう。component を新規作成した瞬間に既存 YAML の
  意味が黙って変わるため、「沈黙による嘘をしない」に反する
- **参照は引数の値の任意の深さに書ける。** リストでもマッピングの値でもよい
- **予約キーは `use` と `data` の 2 つだけ。** `$` で始まるキーは blab が予約する。
  普通のマッピングを渡したいときは `{$raw: {...}}` で包む

制御構文は持たない（参照 `${...}`・四則演算・条件分岐・ループ）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .components import Ref, parse_ref
from .errors import BlabError
from .project import SCHEMA_VERSION

USE_KEY = "use"
DATA_KEY = "data"
RAW_KEY = "$raw"

TOP_LEVEL_KEYS = {"schema_version", "experiment", "group", "name", "run"}

ROOT_PATH = "run"


@dataclass
class DataRef:
    """`{data: NAME}` — 外部ファイルの論理名（layout.md §5.6）。

    `blab.local.json` の `data` で実パスに解決され、component には `pathlib.Path` が渡る。
    """

    name: str
    path: str = ""


@dataclass
class Node:
    """component 参照 1 つ分。`args` の値には `Node` / `DataRef` が任意の深さで入る。"""

    ref: Ref
    args: dict[str, Any] = field(default_factory=dict)
    path: str = ROOT_PATH

    @property
    def id(self) -> str:
        return self.ref.id


@dataclass
class Experiment:
    """1 つの実験 YAML。"""

    experiment: str
    run: Node
    name: str | None = None
    group: str | None = None
    source: Path | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 値の解釈


def _reserved_key_error(key: str, path: str) -> BlabError:
    return BlabError(
        f"{path}: キー {key!r} は blab の予約です（`$` で始まるキーは使えません）。"
        f"普通のマッピングを渡したいときは {{{RAW_KEY}: {{...}}}} で包んでください"
    )


def parse_value(value: Any, path: str, warnings: list[str]) -> Any:
    """YAML の値を、`Node` / `DataRef` / 普通の値に解釈する。"""
    if isinstance(value, list):
        return [parse_value(v, f"{path}[{i}]", warnings) for i, v in enumerate(value)]
    if not isinstance(value, dict):
        return value

    keys = set(value)

    if RAW_KEY in keys:
        if len(keys) != 1:
            others = ", ".join(sorted(keys - {RAW_KEY}))
            raise BlabError(
                f"{path}: {RAW_KEY} は単独で使います（同じマッピングに {others} があります）"
            )
        # 包まれた中身は走査しない。これが `$raw` の役目。
        return value[RAW_KEY]

    for key in sorted(keys):
        if isinstance(key, str) and key.startswith("$"):
            raise _reserved_key_error(key, path)

    has_use, has_data = USE_KEY in keys, DATA_KEY in keys

    if has_use and has_data:
        # どちらの参照とも読める。`use` を優先するが、黙って決めない。
        warnings.append(
            f"{path}: {USE_KEY!r} と {DATA_KEY!r} の両方があります。"
            f"component 参照として解釈しました（{DATA_KEY!r} は引数として扱います）。"
            f"意図が違うなら {{{RAW_KEY}: {{...}}}} で包んでください"
        )

    if has_use:
        target = value[USE_KEY]
        if not isinstance(target, str):
            raise BlabError(
                f"{path}: {USE_KEY!r} の値は component 参照の文字列でなければなりません"
                f"（{type(target).__name__} が来ました）。"
                f"`{USE_KEY}` という名前の普通のキーを渡したいときは {{{RAW_KEY}: {{...}}}} で包んでください"
            )
        args = {
            str(k): parse_value(v, f"{path}.{k}", warnings)
            for k, v in value.items()
            if k != USE_KEY
        }
        return Node(ref=parse_ref(target), args=args, path=path)

    if has_data:
        target = value[DATA_KEY]
        if len(keys) != 1 or not isinstance(target, str):
            raise BlabError(
                f"{path}: {{{DATA_KEY}: NAME}} は論理名の文字列 1 つだけを取ります。"
                f"`{DATA_KEY}` という名前の普通のキーを渡したいときは "
                f"{{{RAW_KEY}: {{...}}}} で包んでください"
            )
        return DataRef(name=target, path=path)

    return {str(k): parse_value(v, f"{path}.{k}", warnings) for k, v in value.items()}


def iter_nodes(node: Node):
    """木の中の `Node` を深さ優先で列挙する（自分自身を含む）。"""
    yield node
    for value in _iter_values(node.args):
        if isinstance(value, Node):
            yield from iter_nodes(value)


def iter_data_refs(node: Node):
    """木の中の `DataRef` を列挙する（子 component の中のものも含む）。"""
    for current in iter_nodes(node):
        for value in _iter_values(current.args):
            if isinstance(value, DataRef):
                yield value


def _iter_values(value: Any):
    """入れ子の dict / list を平らに辿る（`Node` の中には降りない）。"""
    if isinstance(value, dict):
        for v in value.values():
            yield v
            if not isinstance(v, Node):
                yield from _iter_values(v)
    elif isinstance(value, list):
        for v in value:
            yield v
            if not isinstance(v, Node):
                yield from _iter_values(v)


# -------------------------------------------------------------- --set の適用

_INDEX_RE = re.compile(r"\[(-?\d+)\]")


def _split_path(text: str) -> list[str | int]:
    """`run.transforms[0].p` を `["run", "transforms", 0, "p"]` にする。"""
    parts: list[str | int] = []
    for chunk in text.split("."):
        if not chunk:
            raise BlabError(f"--set のパス {text!r} が不正です（空の区間があります）")
        head = _INDEX_RE.split(chunk)
        name = head[0]
        if name:
            parts.append(name)
        elif not parts:
            raise BlabError(f"--set のパス {text!r} が不正です（添字の前にキーがありません）")
        for token in head[1:]:
            if token == "":
                continue
            parts.append(int(token))
    if not parts:
        raise BlabError(f"--set のパス {text!r} が不正です")
    return parts


def parse_override(text: str) -> tuple[str, Any]:
    """`run.lr=1e-4` を `("run.lr", 0.0001)` にする。値は YAML スカラとして読む。"""
    if "=" not in text:
        raise BlabError(f"--set は `<パス>=<値>` の形で書きます: {text!r}")
    path, _, raw = text.partition("=")
    path = path.strip()
    if not path:
        raise BlabError(f"--set のパスが空です: {text!r}")
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise BlabError(f"--set の値を読めません（{text!r}）: {e}") from e
    return path, _coerce_number(raw, value)


def _coerce_number(raw: str, value: Any) -> Any:
    """`1e-5` を float にする。

    YAML 1.1 の float は小数点と符号付き指数を要求するので、`yaml.safe_load("1e-5")` は
    文字列 `'1e-5'` を返す。しかし `--set run.lr=1e-5` と書いた人は数を渡したつもりである。
    クォートされていないときだけ数に寄せる（`--set run.tag='"1e-5"'` は文字列のまま）。
    """
    if not isinstance(value, str):
        return value
    stripped = raw.strip()
    if not stripped or stripped[0] in "\"'":
        return value
    try:
        return float(stripped)
    except ValueError:
        return value


def apply_overrides(doc: dict, overrides: list[str]) -> dict[str, Any]:
    """`--set` を生の YAML ドキュメントに適用する。

    **値の上書きだけを許す。** component 参照の差し替え（`--set run.student.use=resnet50`）は
    禁止する。許すと YAML を書かずに実験できてしまい、「実験の定義が git に残る」という
    利点が薄れるため。制限は後から緩められるが、逆はできない。

    Returns:
        `{"run.lr": 0.0003}` の形の、実際に適用した上書きの記録。
    """
    applied: dict[str, Any] = {}
    for text in overrides:
        path, value = parse_override(text)
        parts = _split_path(path)
        if parts[-1] == USE_KEY:
            raise BlabError(
                f"--set {path} は component 参照の差し替えなので許していません。"
                "構成を変えるときは YAML を書いてください（実験の定義が git に残るように）"
            )
        _assign(doc, parts, value, path)
        applied[path] = value
    return applied


def _assign(doc: Any, parts: list[str | int], value: Any, path: str) -> None:
    cursor = doc
    for i, key in enumerate(parts[:-1]):
        where = _render_path(parts[: i + 1])
        cursor = _descend(cursor, key, where, path)
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(cursor, list):
            raise BlabError(f"--set {path}: {_render_path(parts[:-1])} はリストではありません")
        if not -len(cursor) <= last < len(cursor):
            raise BlabError(
                f"--set {path}: 添字 [{last}] は範囲外です（要素数 {len(cursor)}）"
            )
        cursor[last] = value
        return
    if not isinstance(cursor, dict):
        raise BlabError(f"--set {path}: {_render_path(parts[:-1])} はマッピングではありません")
    # 新しいキーの作成は許す。引数名のタイポは事前検証がシグネチャと照合して捕まえる。
    cursor[last] = value


def _descend(cursor: Any, key: str | int, where: str, path: str) -> Any:
    if isinstance(key, int):
        if not isinstance(cursor, list):
            raise BlabError(f"--set {path}: {where} の手前はリストではありません")
        if not -len(cursor) <= key < len(cursor):
            raise BlabError(f"--set {path}: {where} は範囲外です（要素数 {len(cursor)}）")
        return cursor[key]
    if not isinstance(cursor, dict):
        raise BlabError(f"--set {path}: {where} の手前はマッピングではありません")
    if key not in cursor:
        raise BlabError(
            f"--set {path}: {where} が YAML にありません。"
            "--set は既にある位置の値を上書きするものです"
        )
    return cursor[key]


def _render_path(parts: list[str | int]) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out


# -------------------------------------------------------------- 読み込み


def load_document(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise BlabError(f"実験 YAML を読めません（{path}）: {e}") from e
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise BlabError(f"実験 YAML の構文エラー（{path}）: {e}") from e
    if doc is None:
        raise BlabError(f"実験 YAML が空です: {path}")
    if not isinstance(doc, dict):
        raise BlabError(f"実験 YAML のトップレベルはマッピングでなければなりません: {path}")
    return doc


def parse_document(doc: dict, source: Path | None = None) -> Experiment:
    unknown = sorted(k for k in doc if k not in TOP_LEVEL_KEYS)
    if unknown:
        known = ", ".join(sorted(TOP_LEVEL_KEYS))
        raise BlabError(
            f"実験 YAML に知らないトップレベルのキーがあります: {', '.join(unknown)}"
            f"（使えるのは {known}）"
        )

    version = doc.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise BlabError(
            f"実験 YAML の schema_version が {version!r} です（この blab は {SCHEMA_VERSION} を読みます）"
        )

    experiment = doc.get("experiment")
    if not experiment or not isinstance(experiment, str):
        raise BlabError("実験 YAML に experiment（文字列）が必要です")

    run = doc.get("run")
    if run is None:
        raise BlabError("実験 YAML に run（root component）が必要です")
    if not isinstance(run, dict) or USE_KEY not in run:
        raise BlabError(
            f"run は root component の参照でなければなりません（`{USE_KEY}:` を持つマッピング）"
        )

    warnings: list[str] = []
    node = parse_value(run, ROOT_PATH, warnings)
    if not isinstance(node, Node):  # pragma: no cover - 上の検査で弾かれている
        raise BlabError("run を component 参照として読めませんでした")

    for key in ("name", "group"):
        if doc.get(key) is not None and not isinstance(doc[key], str):
            raise BlabError(f"{key} は文字列でなければなりません")

    return Experiment(
        experiment=experiment,
        run=node,
        name=doc.get("name"),
        group=doc.get("group"),
        source=Path(source) if source else None,
        warnings=warnings,
    )


def load_experiment(
    path: Path, overrides: list[str] | None = None, *, name: str | None = None,
    group: str | None = None,
) -> Experiment:
    """実験 YAML を読み、`--set` を適用して `Experiment` にする。"""
    path = Path(path)
    doc = load_document(path)
    applied = apply_overrides(doc, overrides or []) if overrides else {}
    experiment = parse_document(doc, source=path)
    experiment.overrides = applied
    if name is not None:
        experiment.name = name
    if group is not None:
        experiment.group = group
    return experiment

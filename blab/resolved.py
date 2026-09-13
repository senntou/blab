"""`resolved.yaml` の書き出しと読み込み（layout.md §5.2）。

**あなたが書く YAML と、blab が記録する YAML を分ける。**

| | |
| --- | --- |
| `experiments/distill.yaml` | `use: resnet18` とゆるく書ける。書きやすさ優先。git 管理下 |
| `<run>/resolved.yaml` | 全部ハッシュに解決済み。実行時に渡された引数も埋まっている |

`package.json` と `package-lock.json` の関係と同じ。**記録であると同時に、再実行可能な
入力**でもある（layout.md §7）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .builder import Observed, Recorder
from .errors import BlabError
from .project import SCHEMA_VERSION
from .spec import Experiment

RESOLVED_NAME = "resolved.yaml"


def dump(path: Path, doc: dict) -> None:
    """`resolved.yaml` を原子的に書く。"""
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100
    )
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        os.chmod(tmp, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: Path) -> dict:
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise BlabError(f"{path} を読めません: {e}") from e
    except yaml.YAMLError as e:
        raise BlabError(f"{path} の構文エラー: {e}") from e
    if not isinstance(doc, dict):
        raise BlabError(f"{path} のトップレベルがマッピングではありません")
    return doc


def build_document(
    experiment: Experiment,
    recorder: Recorder,
    *,
    project_uid: str,
    source: str | None = None,
) -> dict:
    """観測結果から `resolved.yaml` の中身を組み立てる。"""
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "experiment": experiment.experiment,
    }
    if experiment.group:
        doc["group"] = experiment.group
    if experiment.name:
        doc["name"] = experiment.name
    doc["project_uid"] = project_uid
    if source:
        doc["source"] = source
    if experiment.overrides:
        doc["overrides"] = dict(experiment.overrides)
    doc["run"] = _node(recorder.root, is_root=True)
    return doc


def _node(observed: Observed, *, is_root: bool = False) -> dict:
    resolved = observed.prepared.resolved
    out: dict[str, Any] = {"use": resolved.id}
    if resolved.project_uid:
        out["project_uid"] = resolved.project_uid
    out["hash"] = resolved.hash
    if resolved.label:
        # ラベルは表示のためだけ。版の同一性は hash が持つ。
        out["label"] = resolved.label
    out["entry"] = observed.prepared.entry_name

    if is_root:
        # root は誰も `.build()` しない。blab が 1 度だけ実体化する。
        build = observed.builds[0] if observed.builds else None
        out["args"] = build.args if build else {}
        out["args_from"] = build.args_from if build else {}
    else:
        # 一度も `.build()` されなかった子は `builds: []` として残す。
        out["builds"] = [{"args": b.args, "args_from": b.args_from} for b in observed.builds]

    if observed.children:
        out["children"] = {key: _node(child) for key, child in observed.children.items()}
    return out


def unused_children(observed: Observed, prefix: str = "") -> list[str]:
    """宣言されたのに一度も `.build()` されなかった子のパスを集める。

    エラーにはしない（条件次第で使わない子は正当にありうる）が、**黙って消さない**。
    """
    found: list[str] = []
    for key, child in observed.children.items():
        path = f"{prefix}{key}"
        if not child.builds:
            found.append(path)
        found.extend(unused_children(child, prefix=f"{path}."))
    return found


def iter_components(doc: dict):
    """`resolved.yaml` の中の component を `(パス, ノード)` で列挙する。"""
    root = doc.get("run")
    if not isinstance(root, dict):
        return
    stack = [("run", root)]
    while stack:
        path, node = stack.pop()
        yield path, node
        children = node.get("children") or {}
        if isinstance(children, dict):
            for key, child in children.items():
                if isinstance(child, dict):
                    stack.append((f"{path}.{key}", child))

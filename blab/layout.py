"""レイアウト仕様の中心: ルート探索・ID・ディレクトリ命名・パス安全性。

記録層も閲覧層もこのモジュールの規約だけを共有する。blab を使わずに手で
この規約どおりのディレクトリを作っても UI から閲覧できる。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import BlabError
from .io import read_json, write_json_atomic

SCHEMA_VERSION = 1
ROOT_MARKER = "blab.json"
#: 既定のルート名。プロジェクト直下に隠しディレクトリとして作る。
#: 複数プロジェクトで 1 つのルートを共有する使い方は ``BLAB_DIR`` で行う。
DEFAULT_ROOT_NAME = ".blab"
META_NAME = "meta.json"
PARAMS_NAME = "params.json"
SUMMARY_NAME = "summary.json"
COMPONENTS_NAME = "components.json"
METRICS_NAME = "metrics.jsonl"
COMPONENTS_DIR = "components"
COMPONENT_META = "component.json"
COMPONENT_README = "README.ja.md"
CURRENT_DIR = "current"
VERSIONS_DIR = "versions"
ARTIFACTS_DIR = "artifacts"
LOGS_DIR = "logs"
#: 削除した node の退避先（ルート直下）。中身は索引の走査対象から外れる。
TRASH_DIR = ".blab-trash"

KIND_EXPERIMENT = "experiment"
KIND_GROUP = "group"
KIND_RUN = "run"

#: README が最新 version の登録より古いときに陳腐化マーカーを出すが、README を書いた
#: 直後に register するのが普通の順序なので、この猶予より短い差は古いとみなさない。
#: （マーカーの意味は「登録を重ねたのに README を書き直していない」であって、
#: 「README を書いてから登録した」ではない）
README_STALE_GRACE_SEC = 3600
#: ``status == "running"`` のまま heartbeat がこの秒数より古い run は stale。
STALE_AFTER_SEC = 60
#: heartbeat デーモンスレッドの更新間隔。
HEARTBEAT_INTERVAL_SEC = 15

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ts_ms: int | None = None) -> str:
    """ULID を採番する（48bit ミリ秒 + 80bit 乱数、Crockford Base32 26 文字）。

    標準ライブラリのみで実装する（記録層は依存を増やさない）。
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    value = (ts_ms & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    chars = []
    for i in range(26):
        chars.append(_CROCKFORD[(value >> (5 * (25 - i))) & 0x1F])
    return "".join(chars)


def short_id(ulid: str) -> str:
    """ディレクトリ名に使う短 ID = ULID の末尾 4 文字（小文字）。"""
    return ulid[-4:].lower()


def slugify(name: str | None, max_len: int = 60) -> str:
    if not name:
        return ""
    s = re.sub(r"[^0-9A-Za-z._-]+", "-", str(name)).strip("-.")
    s = re.sub(r"-{2,}", "-", s).lower()
    return s[:max_len]


def format_dir_name(created: datetime, ulid: str, name: str | None) -> str:
    """``{YYYYMMDD-HHMMSS}_{短ID}_{slug}``。日時は UTC 固定。"""
    stamp = created.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parts = [stamp, short_id(ulid)]
    slug = slugify(name)
    if slug:
        parts.append(slug)
    return "_".join(parts)


def now() -> datetime:
    """ローカルタイムゾーン付きの現在時刻（``created_at`` などに使う）。"""
    return datetime.now(timezone.utc).astimezone()


def isoformat(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(timespec="seconds")


def create_node_dir(parent: Path, name: str | None) -> tuple[Path, str, datetime]:
    """親の下に一意なノードディレクトリを作る。

    ``os.mkdir``（exist_ok なし）で作り、既存で失敗したら ULID ごと採番し直して
    リトライする。同一秒・同名の連投でも衝突しない。
    """
    parent.mkdir(parents=True, exist_ok=True)
    created = now()
    for _ in range(64):
        ulid = new_ulid(int(created.timestamp() * 1000))
        path = parent / format_dir_name(created, ulid, name)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        return path, ulid, created
    raise BlabError(f"could not create a unique directory under {parent}")


# ---------------------------------------------------------------- root 探索


def is_root(path: Path) -> bool:
    return (path / ROOT_MARKER).is_file()


def ensure_root(path: Path) -> Path:
    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ROOT_MARKER
    if not marker.exists():
        # ポリシーは既定値を明示して書く。挙動を隠さないため（§0）。
        write_json_atomic(
            marker,
            {
                "schema_version": SCHEMA_VERSION,
                "require_components": False,
                "require_tags": [],
                "on_missing": "warn",
            },
        )
    return path.resolve()


def find_root(start: Path | None = None) -> Path | None:
    """cwd から上に向かって既存のルートを探す。見つからなければ None。"""
    start = Path(start or Path.cwd()).resolve()
    for d in [start, *start.parents]:
        if is_root(d):
            return d
        if is_root(d / DEFAULT_ROOT_NAME):
            return (d / DEFAULT_ROOT_NAME).resolve()
    return None


def resolve_root(dir: str | os.PathLike | None = None, *, create: bool = True) -> Path:
    """ルートを決める。

    優先順位: 引数 ``dir`` > 環境変数 ``BLAB_DIR`` > 上位探索 > ``./.blab``。
    探索でヒットした場合は 1 行ログ出力する（別プロジェクト配下での誤ヒット検知）。
    """
    if dir is not None:
        return ensure_root(Path(dir)) if create else Path(dir).expanduser().resolve()
    env = os.environ.get("BLAB_DIR")
    if env:
        return ensure_root(Path(env)) if create else Path(env).expanduser().resolve()
    found = find_root()
    if found is not None:
        print(f"[blab] using root {found}")
        return found
    default = Path.cwd() / DEFAULT_ROOT_NAME
    if not create:
        return default.resolve()
    return ensure_root(default)


# ------------------------------------------------------------ component のパス規約

#: component id。合成モジュール名 ``blab._components.<id>__<version>`` の一部になるので
#: Python の識別子に限る。ディレクトリ名・ファイル名・id はすべてこの文字列で一致する。
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: version id は人間が付ける自由文字列だが、ディレクトリ名と spec の区切りに使うので
#: パス区切り・``@``・先頭のドットは許さない。``latest`` は「配列の末尾」の予約語。
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESERVED_VERSIONS = ("latest",)


def check_component_id(id: str) -> str:
    if not _ID_RE.match(id or ""):
        raise BlabError(
            f"invalid component id {id!r}: "
            "Python の識別子（英字か _ で始まり、英数字と _ のみ）にしてください"
        )
    return id


def check_version_id(version: str) -> str:
    if not _VERSION_RE.match(version or ""):
        raise BlabError(
            f"invalid version {version!r}: 英数字で始まり、英数字と . _ - のみ使えます"
        )
    if version in RESERVED_VERSIONS:
        raise BlabError(f"version {version!r} は予約語です（latest は配列の末尾を指す）")
    return version


def components_dir(root: Path) -> Path:
    return Path(root) / COMPONENTS_DIR


def component_dir(root: Path, id: str) -> Path:
    return components_dir(root) / check_component_id(id)


def component_meta_path(root: Path, id: str) -> Path:
    return component_dir(root, id) / COMPONENT_META


def component_readme_path(root: Path, id: str) -> Path:
    return component_dir(root, id) / COMPONENT_README


def current_source_path(root: Path, id: str) -> Path:
    """あなたが編集する場所。``current/<id>.py``。"""
    return component_dir(root, id) / CURRENT_DIR / f"{id}.py"


def version_source_path(root: Path, id: str, version: str) -> Path:
    """凍結された場所。``versions/<version>/<id>.py``。import 元は必ずこちら。"""
    return component_dir(root, id) / VERSIONS_DIR / check_version_id(version) / f"{id}.py"


def iter_component_ids(root: Path):
    """レジストリにある component id を名前順に返す。"""
    base = components_dir(root)
    if not base.is_dir():
        return
    for d in sorted(base.iterdir(), key=lambda p: p.name):
        if d.is_dir() and _ID_RE.match(d.name):
            yield d.name


def hash_source(path: Path) -> str:
    """ソースの内容ハッシュ。生バイトをそのまま SHA-256 する（正規化しない）。

    正規化すると「表示しているものが実際に動いたバイト列」からズレるため、
    空白や改行の違いも別内容として扱う。
    """
    h = hashlib.sha256(Path(path).read_bytes())
    return f"sha256:{h.hexdigest()}"


# ------------------------------------------------------------ 種別の判定など


def read_meta(path: Path) -> dict | None:
    meta = read_json(path / META_NAME)
    return meta if isinstance(meta, dict) else None


def kind_of(path: Path) -> str | None:
    meta = read_meta(path)
    return meta.get("kind") if meta else None


def rename_node(node_dir: Path, name: str) -> str:
    """表示名だけを書き換える。ディレクトリは動かさない（ID はディレクトリ名に依存しない）。"""
    name = str(name or "").strip()
    if not name:
        raise BlabError("name is required")
    if len(name) > 200:
        raise BlabError("name is too long (max 200 chars)")
    meta = read_meta(node_dir)
    if meta is None:
        raise BlabError(f"meta.json not found: {node_dir}")
    meta["name"] = name
    write_json_atomic(node_dir / META_NAME, meta)
    return name


def trash_node(root: Path, node_dir: Path) -> Path:
    """node_dir を ``<root>/.blab-trash/`` へ移動する（ゴミ箱方式の削除）。"""
    root = root.resolve()
    node_dir = node_dir.resolve()
    if node_dir == root or root not in node_dir.parents:
        raise BlabError(f"refusing to trash a path outside root: {node_dir}")
    trash_root = root / TRASH_DIR
    trash_root.mkdir(exist_ok=True)
    stamp = now().astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = trash_root / f"{stamp}_{node_dir.name}"
    n = 1
    while dest.exists():
        n += 1
        dest = trash_root / f"{stamp}_{node_dir.name}_{n}"
    shutil.move(str(node_dir), str(dest))
    return dest


def ensure_experiment(root: Path, name: str) -> Path:
    """Experiment ディレクトリを（無ければ）作る。名前はそのままディレクトリ名。"""
    slug = slugify(name)
    if not slug:
        raise BlabError(f"invalid experiment name: {name!r}")
    path = root / slug
    path.mkdir(parents=True, exist_ok=True)
    if not (path / META_NAME).exists():
        write_json_atomic(
            path / META_NAME,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": KIND_EXPERIMENT,
                "name": name,
                "created_at": isoformat(now()),
            },
        )
    return path


def iter_nodes(root: Path):
    """ルート配下の node ディレクトリ（``meta.json`` を持つもの）を列挙する。

    ``artifacts/`` と ``logs/`` の中は見ない（ユーザが置いた ``meta.json`` を
    node と誤認しないため）。
    """
    root = Path(root)
    for meta_path in sorted(root.rglob(META_NAME)):
        rel_parts = meta_path.relative_to(root).parts
        if ARTIFACTS_DIR in rel_parts or LOGS_DIR in rel_parts:
            continue
        yield meta_path.parent


def resolve_node(root: Path, target: str | os.PathLike, kind: str | None = None) -> Path:
    """``target`` から node ディレクトリを 1 つに決める（``blab.open`` の入口）。

    受け付ける表記（この優先順で解決する）:

    1. ルートからの相対パス（``mnist-cnn/20260814-063012_a1b2_baseline``）や実パス
    2. ディレクトリ名そのもの（``20260814-063012_a1b2_baseline``）
    3. ULID（``meta.json`` の ``id``）または短 ID（末尾 4 文字）
    4. ``name``（``fold0`` など）

    2〜4 は走査で探す。**複数見つかったら曖昧なのでエラーにする**（候補を並べて
    示す）。実験記録の取り違えは黙って起きてはいけない。
    """
    root = Path(root).resolve()
    text = str(target).strip()
    # 相対パス表記のための strip("/") を絶対パスに掛けると絶対でなくなる。
    # パス解決には元の text を、名前 / ID の照合には strip 済みを使う。
    raw = text.strip("/")
    if not raw:
        raise BlabError("open() の対象が空です")

    direct = Path(text).expanduser()
    candidates = [direct] if direct.is_absolute() else [root / raw, Path.cwd() / text]
    for cand in candidates:
        if (cand / META_NAME).is_file():
            return _check_kind(cand.resolve(), kind, raw)

    matches: list[Path] = []
    for node_dir in iter_nodes(root):
        meta = read_meta(node_dir) or {}
        ident = str(meta.get("id") or "")
        if raw in (node_dir.name, ident, meta.get("name") or "") or (
            len(raw) == 4 and ident and short_id(ident) == raw.lower()
        ):
            matches.append(node_dir)

    if kind is not None:
        typed = [d for d in matches if (read_meta(d) or {}).get("kind") == kind]
        matches = typed or matches
    if not matches:
        raise BlabError(f"{raw!r} に一致する run / group がありません（root={root}）")
    if len(matches) > 1:
        listed = "\n  ".join(relpath(root, d) for d in matches[:8])
        raise BlabError(
            f"{raw!r} に一致するものが {len(matches)} 件あります。相対パスか ID で指定してください:\n"
            f"  {listed}"
        )
    return _check_kind(matches[0].resolve(), kind, raw)


def _check_kind(path: Path, kind: str | None, raw: str) -> Path:
    actual = kind_of(path)
    if kind is not None and actual != kind:
        raise BlabError(f"{raw!r} は {actual} であって {kind} ではありません")
    return path


def store_artifact(node_dir: Path, src: Path, name: str | None, mode: str) -> Path:
    """``node_dir/artifacts/`` にファイル / ディレクトリを保存する（run / group 共通）。"""
    src = Path(src).expanduser()
    if not src.exists():
        raise BlabError(f"artifact not found: {src}")
    if mode not in ("copy", "move"):
        raise BlabError(f"unknown artifact mode: {mode!r} (use 'copy' or 'move')")
    dest = safe_join(node_dir / ARTIFACTS_DIR, name or src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        if mode == "move":
            shutil.rmtree(src)
    elif mode == "copy":
        shutil.copy2(src, dest)
    else:
        shutil.move(str(src), str(dest))
    return dest


def safe_join(root: Path, relpath: str) -> Path:
    """ルート配下の相対パスを解決する。ルート外への脱出は拒否する。"""
    raw = str(relpath or "").strip()
    if Path(raw).is_absolute():
        raise BlabError(f"unsafe path: {relpath!r}")
    rel = raw.strip("/")
    if not rel:
        return root
    if any(p == ".." for p in Path(rel).parts):
        raise BlabError(f"unsafe path: {relpath!r}")
    root = root.resolve()
    target = (root / rel).resolve()
    # symlink 経由の脱出も realpath 解決後に弾く。
    if target != root and root not in target.parents:
        raise BlabError(f"path escapes root: {relpath!r}")
    return target


def relpath(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_stale(meta: dict, now_ts: float | None = None) -> bool:
    """``running`` のまま heartbeat が途絶えている（＝プロセスが落ちた疑い）か。"""
    if meta.get("status") != "running":
        return False
    hb = meta.get("heartbeat_at")
    if not hb:
        return False
    try:
        ts = datetime.fromisoformat(hb).timestamp()
    except (TypeError, ValueError):
        return False
    return (now_ts or time.time()) - ts > STALE_AFTER_SEC

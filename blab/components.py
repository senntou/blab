"""レジストリ（`components/`）— ハッシュ・自動凍結・ラベル・参照の解決。

仕様は layout.md §2。要点。

- **component id はディレクトリ名**であり、Python の識別子に限る（合成モジュール名の
  一部になるため）。`id` フィールドを別途の真実にはしない
- **ハッシュはディレクトリの中身の全部から決まる。** README も入る。除外があると
  「凍結ディレクトリの中身とハッシュが 1:1」が崩れる（同じハッシュで中身の違う版が作れる）
- **凍結は自動で起きる。** 参照を解決するたびに作業コピーをハッシュし、未凍結なら
  `.blab/frozen/<id>/<hash>/` へコピーする。人間は何もしない
- ラベル・タグは `components/.meta/<id>.json`。**本体の外に置く**（中に置くと、
  ラベルを貼る行為がそのコンポーネントのハッシュを変えてしまう）
"""

from __future__ import annotations

import hashlib
import keyword
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BlabError
from .ids import isoformat, now
from .io import read_json, write_json_atomic
from .project import SCHEMA_VERSION, GlobalIndex, Project

#: ハッシュの対象外。`.meta/` と `.frozen/` は本体の外にあるので元々対象にならない。
#: ドット始まりを除くのは `.ipynb_checkpoints` のたぐいを巻き込まないため。
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_SUFFIXES = (".pyc",)

#: `latest` はラベル配列の末尾を指す予約語（ラベルは自由文字列なので大小比較ができない）。
LATEST = "latest"

ENTRY_MODULE = "main.py"


# ------------------------------------------------------------------------ id


def validate_id(id: str) -> str:
    """component id（= ディレクトリ名）の規則を検査する。

    合成モジュール名 `blab._c.<id>__<hash8>` の一部になるので Python の識別子に限る。
    `-` が使えないのはこのため。
    """
    if not id:
        raise BlabError("component id が空です")
    if not id.isidentifier() or keyword.iskeyword(id):
        raise BlabError(
            f"component id {id!r} は Python の識別子ではありません。"
            "合成モジュール名の一部になるため、英数字と _ のみで、数字始まりと予約語は使えません"
            "（`-` は使えません）"
        )
    return id


def list_ids(project: Project) -> list[str]:
    """`components/` 直下の component を列挙する。

    ドットで始まるエントリは component ではない（`.meta` / `.frozen`）。id が Python の
    識別子に限られるので、この判定と id の規則は衝突しない。
    """
    root = project.components_dir
    if not root.is_dir():
        return []
    ids = []
    for child in sorted(root.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        ids.append(child.name)
    return ids


# --------------------------------------------------------------------- ハッシュ


def iter_hashed_files(root: Path) -> list[tuple[str, Path]]:
    """ハッシュ対象のファイルを `(相対パス, 実パス)` で、相対パス昇順に返す。

    この関数が「ディレクトリの中身」の唯一の定義である。ハッシュ計算と凍結コピーの
    両方がこれを使うので、凍結ディレクトリの中身とハッシュが必ず 1:1 に対応する。
    """
    out: list[tuple[str, Path]] = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        # 降りる前に落とす（走査もしない）。
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in EXCLUDED_DIRS
        )
        here = Path(dirpath)
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith(EXCLUDED_SUFFIXES):
                continue
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            out.append((path.relative_to(root).as_posix(), path))
    out.sort(key=lambda item: item[0])
    return out


def hash_dir(root: Path) -> str:
    """ディレクトリ全体の内容ハッシュ（layout.md §2.1）。

        sha256( ソートした (相対パス, ファイルの生バイト) の列 )

    正規化はしない（空白の違いも別内容）。
    """
    root = Path(root)
    if not root.is_dir():
        raise BlabError(f"ディレクトリがありません: {root}")
    h = hashlib.sha256()
    for rel, path in iter_hashed_files(root):
        # 長さを混ぜて (パス, 中身) の境界を曖昧にしない。
        rel_bytes = rel.encode("utf-8")
        h.update(len(rel_bytes).to_bytes(8, "big"))
        h.update(rel_bytes)
        data = path.read_bytes()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return "sha256:" + h.hexdigest()


def short_hash(hash: str) -> str:
    """合成モジュール名に使う先頭 8 桁。"""
    return hash.split(":", 1)[-1][:8]


def copy_hashed(src: Path, dst: Path) -> None:
    """ハッシュ対象のファイルだけをコピーする。

    `shutil.copytree` を使わないのは、`__pycache__` や `.ipynb_checkpoints` が
    紛れ込むと凍結ディレクトリの中身とハッシュが食い違うためである。
    """
    for rel, path in iter_hashed_files(src):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


# --------------------------------------------------------------------- 凍結


def freeze(project: Project, id: str, source: Path, hash: str) -> Path:
    """`source` の中身を `.blab/frozen/<id>/<hash>/` に凍結して、そのパスを返す。

    既に凍結済みなら何もしない。**内容ハッシュで名前が決まるので、衝突した中身は
    必ず同一である**（layout.md §8）。並列実行で競合しても、一時ディレクトリに
    展開してから `os.rename` するので壊れない。
    """
    dest = project.frozen_dir / id / hash.replace(":", "-")
    if dest.is_dir():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=str(dest.parent)))
    try:
        copy_hashed(source, tmp)
        try:
            os.rename(tmp, dest)
        except OSError:
            # 別プロセスが先に置いた。内容は同一なので捨ててよい。
            if not dest.is_dir():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def find_frozen(project: Project, id: str, hash: str) -> Path | None:
    """凍結済みの版を探す。`.blab/frozen/` → `components/.frozen/` の順。"""
    dest = project.frozen_dir / id / hash.replace(":", "-")
    if dest.is_dir():
        return dest
    labeled = project.labeled_dir / id
    if labeled.is_dir():
        for child in sorted(labeled.iterdir()):
            if child.is_dir() and hash_dir(child) == hash:
                return child
    return None


def find_frozen_by_prefix(project: Project, id: str, prefix: str) -> list[tuple[str, Path]]:
    """ハッシュの前方一致で凍結版を探す。`(ハッシュ, パス)` を返す。"""
    hits: dict[str, Path] = {}
    auto = project.frozen_dir / id
    if auto.is_dir():
        for child in sorted(auto.iterdir()):
            if not child.is_dir():
                continue
            hash = child.name.replace("sha256-", "sha256:", 1)
            if hash.split(":", 1)[-1].startswith(prefix):
                hits.setdefault(hash, child)
    labeled = project.labeled_dir / id
    if labeled.is_dir():
        for child in sorted(labeled.iterdir()):
            if not child.is_dir():
                continue
            hash = hash_dir(child)
            if hash.split(":", 1)[-1].startswith(prefix):
                hits.setdefault(hash, child)
    return sorted(hits.items())


# ----------------------------------------------------------------- メタ情報


@dataclass
class Meta:
    """`components/.meta/<id>.json`（layout.md §2.3）。

    **無くてもよい。無ければ「ラベルもタグも無い」とみなす。** 手で作った component の
    ディレクトリがそのまま動くほうが、ファイルシステムを真実とする方針に合う。
    """

    id: str
    tags: list[str] = field(default_factory=list)
    labels: list[dict] = field(default_factory=list)
    exists: bool = False

    @classmethod
    def load(cls, project: Project, id: str) -> Meta:
        path = meta_path(project, id)
        doc = read_json(path, default=None)
        if not isinstance(doc, dict):
            return cls(id=id)
        declared = doc.get("id")
        if declared is not None and str(declared) != id:
            # 二重の真実を作らないため、id フィールドは一致検査にだけ使う。
            raise BlabError(
                f"{path} の id が {declared!r} ですが、ディレクトリ名は {id!r} です。"
                "component id はディレクトリ名が真実なので、どちらかを直してください"
            )
        tags = doc.get("tags") or []
        labels = doc.get("labels") or []
        return cls(
            id=id,
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            labels=[x for x in labels if isinstance(x, dict)] if isinstance(labels, list) else [],
            exists=True,
        )

    def save(self, project: Project) -> None:
        write_json_atomic(
            meta_path(project, self.id),
            {
                "schema_version": SCHEMA_VERSION,
                "id": self.id,
                "tags": self.tags,
                "labels": self.labels,
            },
            indent=2,
        )

    # ---- ラベル

    def label_hash(self, name: str) -> str | None:
        """ラベル名からハッシュを引く。`latest` は配列の末尾。"""
        if name == LATEST:
            return str(self.labels[-1]["hash"]) if self.labels else None
        for entry in self.labels:
            if entry.get("name") == name:
                return str(entry.get("hash"))
        return None

    def label_of(self, hash: str) -> str | None:
        """ハッシュに貼られたラベル名（複数あれば最後に貼られたもの）。"""
        found = None
        for entry in self.labels:
            if entry.get("hash") == hash:
                found = str(entry.get("name"))
        return found

    def add_label(self, name: str, hash: str, note: str | None = None) -> None:
        """ラベルを貼る。**ラベルは不変**なので、既存の名前は付け替えられない。"""
        if name == LATEST:
            raise BlabError(f"{LATEST!r} は予約語なのでラベル名に使えません")
        existing = self.label_hash(name)
        if existing is not None:
            if existing == hash:
                return
            raise BlabError(
                f"ラベル {name!r} は既に {existing} に貼られています。"
                "ラベルは不変です（過去の YAML が指す先が変わってしまうため）。別の名前を使ってください"
            )
        entry = {"name": name, "hash": hash, "tagged_at": isoformat(now())}
        if note:
            entry["note"] = note
        self.labels.append(entry)


def meta_path(project: Project, id: str) -> Path:
    return project.meta_dir / f"{id}.json"


# ------------------------------------------------------------------- 参照


@dataclass(frozen=True)
class Ref:
    """YAML に書かれた component 参照（layout.md §3.1）。

        <id>                   作業コピー（実行時に自動凍結）
        <id>@<label>           ラベルで固定
        <id>@sha256:<hash>     ハッシュで完全固定
        <project>/<id>[@...]   別プロジェクト
    """

    id: str
    project: str | None = None
    label: str | None = None
    hash: str | None = None
    text: str = ""

    def __str__(self) -> str:
        return self.text or self.id


def parse_ref(text: str) -> Ref:
    if not isinstance(text, str) or not text.strip():
        raise BlabError(f"component 参照が文字列ではありません: {text!r}")
    raw = text.strip()

    project = None
    body = raw
    if "/" in body:
        # `/` はプロジェクト区切りとして予約。
        project, _, body = body.partition("/")
        if not project or "/" in body:
            raise BlabError(
                f"component 参照 {raw!r} が不正です。別プロジェクトは `<project>/<id>` の形で書きます"
            )

    label = hash = None
    if "@" in body:
        id_part, _, selector = body.partition("@")
        if "@" in selector:
            raise BlabError(f"component 参照 {raw!r} に `@` が 2 つ以上あります")
        if not selector:
            raise BlabError(f"component 参照 {raw!r} の `@` の後ろが空です")
        if selector.startswith("sha256:"):
            hash = selector
            digest = selector.split(":", 1)[1]
            if not digest or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise BlabError(f"component 参照 {raw!r} のハッシュが 16 進数ではありません")
        else:
            label = selector
    else:
        id_part = body

    validate_id(id_part)
    return Ref(id=id_part, project=project, label=label, hash=hash, text=raw)


@dataclass
class Resolved:
    """解決済みの component。**ここから先は必ず凍結版を見る**（作業コピーは見ない）。"""

    ref: Ref
    id: str
    project_uid: str
    project_name: str
    hash: str
    path: Path
    label: str | None
    tags: list[str]
    froze_now: bool = False

    @property
    def module_name(self) -> str:
        return f"blab._c.{self.id}__{short_hash(self.hash)}"


class Resolver:
    """参照を凍結版に解決する。プロジェクトをまたぐ解決もここが持つ。

    解決結果はプロセス内でキャッシュする（同じ component が木に何度も現れるため）。
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self._index: GlobalIndex | None = None
        self._projects: dict[str, Project] = {project.name: project}
        self._cache: dict[str, Resolved] = {}

    def _project_for(self, name: str | None) -> Project:
        if name is None:
            return self.project
        if name in self._projects:
            return self._projects[name]
        if self._index is None:
            self._index = GlobalIndex.load()
        path = self._index.by_name(name)
        if path is None:
            raise BlabError(
                f"プロジェクト {name!r} がグローバル索引にありません。"
                f"`blab link <path>` で登録してください"
            )
        if not (path / "blab.json").is_file():
            raise BlabError(
                f"プロジェクト {name!r} の登録先 {path} に blab.json がありません。"
                f"移動したなら `blab link <新しいパス>` で登録し直してください"
            )
        other = Project.at(path)
        self._projects[name] = other
        return other

    def resolve(self, ref: Ref) -> Resolved:
        key = str(ref)
        if key in self._cache:
            return self._cache[key]
        resolved = self._resolve(ref)
        self._cache[key] = resolved
        return resolved

    def _resolve(self, ref: Ref) -> Resolved:
        project = self._project_for(ref.project)
        meta = Meta.load(project, ref.id)

        if ref.hash is not None:
            hash, path = self._by_hash(project, ref, meta)
            froze = False
        elif ref.label is not None:
            hash, path = self._by_label(project, ref, meta)
            froze = False
        else:
            hash, path, froze = self._working_copy(project, ref)

        return Resolved(
            ref=ref,
            id=ref.id,
            project_uid=project.uid,
            project_name=project.name,
            hash=hash,
            path=path,
            label=meta.label_of(hash),
            tags=list(meta.tags),
            froze_now=froze,
        )

    def _working_copy(self, project: Project, ref: Ref) -> tuple[str, Path, bool]:
        source = project.components_dir / ref.id
        if not source.is_dir():
            raise BlabError(
                f"component {str(ref)!r} が見つかりません（{source} がありません）"
            )
        if not (source / ENTRY_MODULE).is_file():
            raise BlabError(
                f"component {str(ref)!r} に {ENTRY_MODULE} がありません。"
                f"入口モジュールは {ENTRY_MODULE} です"
            )
        hash = hash_dir(source)
        existing = project.frozen_dir / ref.id / hash.replace(":", "-")
        already = existing.is_dir()
        return hash, freeze(project, ref.id, source, hash), not already

    def _by_label(self, project: Project, ref: Ref, meta: Meta) -> tuple[str, Path]:
        hash = meta.label_hash(ref.label)
        if hash is None:
            known = ", ".join(str(e.get("name")) for e in meta.labels) or "（ラベルなし）"
            raise BlabError(
                f"component {ref.id!r} にラベル {ref.label!r} がありません。"
                f"貼られているラベル: {known}"
            )
        path = find_frozen(project, ref.id, hash)
        if path is None:
            raise BlabError(
                f"component {str(ref)!r}（{hash}）の実体が見つかりません。"
                f"{project.frozen_dir / ref.id} にも {project.labeled_dir / ref.id} にもありません。"
                "その版を使った run が残っていれば `blab tag --from-run` で復元できます"
            )
        return hash, path

    def _by_hash(self, project: Project, ref: Ref, meta: Meta) -> tuple[str, Path]:
        prefix = ref.hash.split(":", 1)[1].lower()
        hits = find_frozen_by_prefix(project, ref.id, prefix)

        # 作業コピーが一致することもある（凍結前に固定ハッシュで参照した場合）。
        source = project.components_dir / ref.id
        if source.is_dir():
            hash = hash_dir(source)
            if hash.split(":", 1)[1].startswith(prefix) and all(h != hash for h, _ in hits):
                hits.append((hash, freeze(project, ref.id, source, hash)))

        if not hits:
            raise BlabError(
                f"component {str(ref)!r} に一致する版がありません。"
                f"`blab components` で凍結済みの版を確認してください"
            )
        if len(hits) > 1:
            found = ", ".join(h for h, _ in hits)
            raise BlabError(
                f"component 参照 {str(ref)!r} のハッシュが {len(hits)} 件に一致します（{found}）。"
                "もっと長く書いてください"
            )
        return hits[0]

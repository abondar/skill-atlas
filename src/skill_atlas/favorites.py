"""Pinned repositories and skills: mutable user state next to the store (SPEC section 7.5).

Snapshots are immutable, so pins live in their own file, `$SKILL_ATLAS_HOME/favorites.json`.
A repository pin matches by `repo_key`, or by GitHub `node_id` after a rename. A skill pin
adds the skill `id` (`<type>:<path>`): a moved skill loses its pin.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skill_atlas.errors import StoreError
from skill_atlas.model import SourceInfo
from skill_atlas.store import Loaded

FILE = "favorites.json"
VERSION = 1

_lock = threading.Lock()  # one read-modify-write at a time within a process


def default_path(store_dir: Path) -> Path:
    """The favorites file next to the snapshot directory `$SKILL_ATLAS_HOME/scans`."""
    return store_dir.parent / FILE


@dataclass(frozen=True, order=True)
class RepoRef:
    repo_key: str
    node_id: str | None = None

    @classmethod
    def of(cls, source: SourceInfo) -> RepoRef:
        return cls(source.repo_key, source.node_id)

    @classmethod
    def of_repo(cls, items: list[Loaded]) -> RepoRef:
        """A repository's snapshots, oldest first: the latest key, any known node_id."""
        node = next((i.snapshot.source.node_id for i in items if i.snapshot.source.node_id), None)
        return cls(items[-1].snapshot.source.repo_key, node)

    def matches(self, repo_key: str, node_id: str | None) -> bool:
        return self.repo_key == repo_key or (self.node_id is not None and self.node_id == node_id)


@dataclass(frozen=True, order=True)
class SkillRef:
    repo: RepoRef
    id: str


class _RepoEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_key: str = Field(min_length=1)
    node_id: str | None = None


class _SkillEntry(_RepoEntry):
    id: str = Field(min_length=1)


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    repos: list[_RepoEntry] = Field(default_factory=list)
    skills: list[_SkillEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class Favorites:
    repos: frozenset[RepoRef] = frozenset()
    skills: frozenset[SkillRef] = frozenset()
    _repo_keys: frozenset[str] = field(init=False, repr=False, compare=False)
    _repo_nodes: frozenset[str] = field(init=False, repr=False, compare=False)
    _skill_keys: frozenset[tuple[str, str]] = field(init=False, repr=False, compare=False)
    _skill_nodes: frozenset[tuple[str, str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Lookup sets: a TUI or web page asks for every skill of a snapshot.
        set_ = object.__setattr__
        set_(self, "_repo_keys", frozenset(r.repo_key for r in self.repos))
        set_(self, "_repo_nodes", frozenset(r.node_id for r in self.repos if r.node_id))
        set_(self, "_skill_keys", frozenset((s.repo.repo_key, s.id) for s in self.skills))
        set_(
            self,
            "_skill_nodes",
            frozenset((s.repo.node_id, s.id) for s in self.skills if s.repo.node_id),
        )

    def repo_pinned(self, source: SourceInfo) -> bool:
        return source.repo_key in self._repo_keys or (
            source.node_id is not None and source.node_id in self._repo_nodes
        )

    def any_repo_pinned(self, items: Iterable[Loaded]) -> bool:
        """One repository: pinned when any of its snapshots matches a pin."""
        return any(self.repo_pinned(i.snapshot.source) for i in items)

    def skill_pinned(self, source: SourceInfo, skill_id: str) -> bool:
        return (source.repo_key, skill_id) in self._skill_keys or (
            source.node_id is not None and (source.node_id, skill_id) in self._skill_nodes
        )

    def with_repo(self, repo: RepoRef, pinned: bool) -> Favorites:
        keep = {r for r in self.repos if not r.matches(repo.repo_key, repo.node_id)}
        return Favorites(frozenset(keep | ({repo} if pinned else set())), self.skills)

    def with_skills(self, refs: Iterable[SkillRef], pinned: bool) -> Favorites:
        skills = set(self.skills)
        for ref in refs:
            skills = {
                s
                for s in skills
                if not (s.id == ref.id and s.repo.matches(ref.repo.repo_key, ref.repo.node_id))
            }
            if pinned:
                skills.add(ref)
        return Favorites(self.repos, frozenset(skills))


def load(path: Path) -> Favorites:
    """Read the favorites file; a missing file means no pins."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Favorites()
    except OSError as exc:
        raise StoreError(f"cannot read {path}: {exc}") from None
    try:
        data = _File.model_validate_json(text)
    except ValidationError as exc:
        raise StoreError(f"{path}: invalid favorites file: {exc.error_count()} errors") from None
    if data.version > VERSION:
        raise StoreError(f"{path}: unsupported favorites version {data.version}")
    return Favorites(
        frozenset(RepoRef(r.repo_key, r.node_id) for r in data.repos),
        frozenset(SkillRef(RepoRef(s.repo_key, s.node_id), s.id) for s in data.skills),
    )


def load_or_empty(path: Path | None, warnings: list[str]) -> Favorites:
    """For display: a broken file shows no pins and a warning instead of failing."""
    if path is None:
        return Favorites()
    try:
        return load(path)
    except StoreError as exc:
        warnings.append(str(exc))
        return Favorites()


def dumps(fav: Favorites) -> str:
    data = {
        "version": VERSION,
        "repos": [{"repo_key": r.repo_key, "node_id": r.node_id} for r in sorted(fav.repos)],
        "skills": [
            {"repo_key": s.repo.repo_key, "node_id": s.repo.node_id, "id": s.id}
            for s in sorted(fav.skills)
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(path: Path, fav: Favorites) -> None:
    """Replace the file atomically: a temporary file in the same directory, then rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-favorites-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(dumps(fav))
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:
        raise StoreError(f"cannot write {path}: {exc}") from None


def update(path: Path, change: Callable[[Favorites], Favorites]) -> Favorites:
    """Read, change and write back. A broken file is an error, never silently replaced."""
    with _lock:
        fav = change(load(path))
        save(path, fav)
        return fav

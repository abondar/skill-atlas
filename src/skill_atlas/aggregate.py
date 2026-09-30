"""Aggregation over the snapshot store (SPEC section 7.4)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from skill_atlas.model import Skill
from skill_atlas.store import Loaded, iter_snapshots

GroupBy = Literal["none", "name", "hash"]


@dataclass
class Store:
    snapshots: list[Loaded]
    warnings: list[str]

    @classmethod
    def open(cls, directory: Path) -> Store:
        warnings: list[str] = []
        return cls(list(iter_snapshots(directory, warnings)), warnings)

    def identity(self, loaded: Loaded) -> str:
        return self._identities()[id(loaded)]

    def _identities(self) -> dict[int, str]:
        # Link snapshots of a renamed repository through GitHub node_id.
        node_by_key: dict[str, str] = {}
        for item in self.snapshots:
            s = item.snapshot.source
            if s.node_id:
                node_by_key.setdefault(s.repo_key, s.node_id)
        out: dict[int, str] = {}
        for item in self.snapshots:
            s = item.snapshot.source
            node = s.node_id or node_by_key.get(s.repo_key)
            out[id(item)] = f"node:{node}" if node else f"key:{s.repo_key}"
        return out

    def by_repo(self) -> dict[str, list[Loaded]]:
        ids = self._identities()
        groups: dict[str, list[Loaded]] = defaultdict(list)
        for item in self.snapshots:
            groups[ids[id(item)]].append(item)
        for items in groups.values():
            items.sort(key=lambda x: (x.snapshot.scan.scanned_at, x.path.name))
        return dict(groups)

    def latest(self) -> list[Loaded]:
        return [items[-1] for items in self.by_repo().values()]

    def find(self, repo_key: str, sha: str | None = None) -> Loaded | None:
        key = repo_key.lower()
        matches = [
            item
            for item in self.snapshots
            if item.snapshot.source.repo_key == key
            and (sha is None or (item.snapshot.source.commit_sha or "").startswith(sha.lower()))
        ]
        if not matches:
            return None
        return max(matches, key=lambda x: (x.snapshot.scan.scanned_at, x.path.name))


@dataclass
class RepoRow:
    repo_key: str
    last_scanned_at: str
    commit_sha: str | None
    dirty: bool | None
    skills: int
    agents: int
    scans: int
    snapshot: str


def repos(store: Store, sort: Literal["scanned", "skills", "name"] = "scanned") -> list[RepoRow]:
    rows: list[RepoRow] = []
    for items in store.by_repo().values():
        last = items[-1]
        snap = last.snapshot
        rows.append(
            RepoRow(
                repo_key=snap.source.repo_key,
                last_scanned_at=snap.scan.scanned_at,
                commit_sha=snap.source.commit_sha,
                dirty=snap.source.dirty,
                skills=snap.stats.skills,
                agents=snap.stats.agents,
                scans=len(items),
                snapshot=last.path.name,
            )
        )
    if sort == "skills":
        rows.sort(key=lambda r: (-r.skills, r.repo_key))
    elif sort == "name":
        rows.sort(key=lambda r: r.repo_key)
    else:
        rows.sort(key=lambda r: r.last_scanned_at, reverse=True)
    return rows


@dataclass
class SkillRow:
    repo_key: str
    commit_sha: str | None
    scanned_at: str
    skill: Skill


@dataclass
class SkillGroup:
    key: str
    names: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    variants: int = 0
    occurrences: int = 0


def skill_rows(
    store: Store,
    *,
    name: str | None = None,
    repo: str | None = None,
    type_: str | None = None,
    kind: str | None = "skill",
    all_scans: bool = False,
) -> list[SkillRow]:
    items = store.snapshots if all_scans else store.latest()
    needle = name.lower() if name else None
    rows: list[SkillRow] = []
    for item in items:
        snap = item.snapshot
        if repo and snap.source.repo_key != repo.lower():
            continue
        for s in snap.skills:
            if kind and s.kind != kind:
                continue
            if type_ and s.type != type_:
                continue
            if needle and needle not in (s.name or "").lower():
                continue
            rows.append(
                SkillRow(snap.source.repo_key, snap.source.commit_sha, snap.scan.scanned_at, s)
            )
    rows.sort(key=lambda r: (r.repo_key, r.skill.name or "", r.skill.id, r.scanned_at))
    return rows


def group(rows: list[SkillRow], by: Literal["name", "hash"]) -> list[SkillGroup]:
    groups: dict[str, SkillGroup] = {}
    hashes: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        s = r.skill
        key = (s.name or "(unnamed)") if by == "name" else (s.content_sha256 or "(no content)")
        g = groups.setdefault(key, SkillGroup(key))
        g.occurrences += 1
        if s.name and s.name not in g.names:
            g.names.append(s.name)
        if s.type not in g.types:
            g.types.append(s.type)
        if r.repo_key not in g.repos:
            g.repos.append(r.repo_key)
        hashes[key].add(s.content_sha256 or "")
    for key, g in groups.items():
        g.variants = len(hashes[key])
        g.names.sort()
        g.types.sort()
        g.repos.sort()
    return sorted(groups.values(), key=lambda g: (-len(g.repos), g.key))

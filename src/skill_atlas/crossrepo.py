"""The same skill in other repositories (SPEC section 5.8).

The corpus is the latest snapshot of every repository in the store. Signals are the
ones of `similarity`, with IDF over the whole corpus. Copied text is the primary signal:
its share does not depend on the rest of the store, so a new repository does not change
it. Vocabulary finds rewritten versions; its IDF, and so its score, does change as the
store grows.

A name is not an identity across repositories: `code-review` in two repositories can be
two different skills. Only content links skills across repositories.

Candidates come from two inverted indexes, so a query does not compare with every skill:
- sampled shingles: every shingle whose hash is divisible by `SAMPLE`. Two bodies that
  share a passage share its sampled shingles, whatever the length of either body;
- rare terms: terms in at most `RARE_DF` of the corpus. The partial cosine over them must
  reach `CANDIDATE_TOPIC`; frequent terms have low IDF and add little to the cosine.
Candidates are then compared exactly.

Families: skills linked across repositories by a match of at least `FAMILY_THRESHOLD`,
plus other versions of a skill in the same repository, form one family (union-find).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skill_atlas.model import Skill
from skill_atlas.similarity import (
    MIN_SHINGLES,
    THRESHOLD,
    Corpus,
    Key,
    Match,
    level,
    shingles_of,
    term_counts,
)
from skill_atlas.store import Loaded
from skill_atlas.views import dup_key

SAMPLE = 4
MAX_POSTINGS = 64  # a sampled shingle in more bodies than this is boilerplate
RARE_DF = 0.05
CANDIDATE_TOPIC = 0.15
FAMILY_THRESHOLD = 0.6
FORK = 0.5  # share of copied text that makes a match a fork, not a related skill

Status = Literal["identical", "fork", "related"]


@dataclass(frozen=True)
class Repo:
    ident: str  # `aggregate.Store` identity: survives renames through node_id
    repo_key: str
    path: Path


@dataclass(frozen=True)
class CrossMatch:
    repo: Repo
    match: Match
    status: Status

    @property
    def level(self) -> str:
        return level(self.match.score)


def status_of(query_sha: str | None, m: Match) -> Status:
    if query_sha is not None and query_sha == m.skill.content_sha256:
        return "identical"
    if max(m.overlap_here, m.overlap_there) >= FORK:
        return "fork"
    return "related"


class CrossIndex:
    """Skills of the latest snapshot of every repository."""

    def __init__(self, latest: list[tuple[str, Loaded]]) -> None:
        self.repo_of: dict[int, Repo] = {}  # id(skill) -> its repository
        self.repos = [
            Repo(ident, item.snapshot.source.repo_key, item.path) for ident, item in latest
        ]
        groups: dict[Key, list[Skill]] = {}
        for repo, (_, item) in zip(self.repos, latest, strict=True):
            for s in item.snapshot.skills:
                if s.category == "external":
                    continue
                self.repo_of[id(s)] = repo
                groups.setdefault(dup_key(s), []).append(s)
        self.corpus = Corpus(groups)
        n = len(groups)
        self._rare_df = max(2, int(RARE_DF * n))
        self._samples: dict[int, list[Key]] = {}
        self._terms: dict[str, list[Key]] = {}
        for key, sh in self.corpus.shingles.items():
            for h in sh:
                if h % SAMPLE == 0:
                    self._samples.setdefault(h, []).append(key)
        for key, vec in self.corpus.vectors.items():
            for t in vec:
                if self.corpus.df.get(t, 0) <= self._rare_df:
                    self._terms.setdefault(t, []).append(key)

    def repos_of(self, key: Key) -> set[str]:
        return {self.repo_of[id(s)].ident for s in self.corpus.groups[key]}

    def _query(self, skill: Skill) -> tuple[dict[str, float], frozenset[int]]:
        key = dup_key(skill)
        if key in self.corpus.vectors:
            return self.corpus.vectors[key], self.corpus.shingles[key]
        # A skill from an older snapshot: vectorize it against this corpus.
        return self.corpus.vector(term_counts(skill)), shingles_of(skill)

    def candidates(self, vec: dict[str, float], sh: frozenset[int]) -> list[Key]:
        found: set[Key] = set()
        if len(sh) >= MIN_SHINGLES:
            for h in sh:
                if h % SAMPLE == 0:
                    postings = self._samples.get(h, ())
                    if len(postings) <= MAX_POSTINGS:
                        found.update(postings)
        partial: dict[Key, float] = {}
        for t, w in vec.items():
            for key in self._terms.get(t, ()):
                partial[key] = partial.get(key, 0.0) + w * self.corpus.vectors[key][t]
        found.update(k for k, v in partial.items() if v >= CANDIDATE_TOPIC)
        return sorted(found, key=repr)  # deterministic order

    def matches(
        self, skill: Skill, exclude: set[str], threshold: float = THRESHOLD
    ) -> list[CrossMatch]:
        """Entries in other repositories at or above `threshold`, one per repository and
        content. `exclude` holds the identities of the skill's own repository."""
        vec, sh = self._query(skill)
        own = dup_key(skill)
        out: list[CrossMatch] = []
        keys = self.candidates(vec, sh)
        if own in self.corpus.groups and own not in keys:
            keys.append(own)  # identical copies elsewhere, even without shingles
        for key in keys:
            by_repo: dict[str, list[Skill]] = {}
            for s in self.corpus.groups[key]:
                repo = self.repo_of[id(s)]
                if repo.ident not in exclude:
                    by_repo.setdefault(repo.ident, []).append(s)
            if not by_repo:
                continue
            base = self.corpus.compare_with(vec, sh, key)
            if key == own:
                base = Match(base.skill, 1.0, 1.0, 1.0, 1.0, base.shared_terms, base.copies)
            if base.score < threshold:
                continue
            for members in by_repo.values():
                first = members[0]
                m = Match(
                    first,
                    base.score,
                    base.topic,
                    base.overlap_here,
                    base.overlap_there,
                    base.shared_terms,
                    len(members) - 1,
                )
                out.append(
                    CrossMatch(self.repo_of[id(first)], m, status_of(skill.content_sha256, m))
                )
        out.sort(key=lambda c: (-c.match.score, c.repo.repo_key, c.match.skill.id))
        return out

    def families(self) -> dict[Key, Key]:
        """Map every content key to its family root (the smallest key by repr)."""
        parent: dict[Key, Key] = {k: k for k in self.corpus.groups}

        def find(k: Key) -> Key:
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a: Key, b: Key) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                lo, hi = sorted((ra, rb), key=repr)
                parent[hi] = lo

        # Other versions of one skill in one repository.
        by_name: dict[tuple[str, str, str], Key] = {}
        for key, members in self.corpus.groups.items():
            for s in members:
                if s.name is None:
                    continue
                ident = (self.repo_of[id(s)].ident, s.kind, s.name)
                if ident in by_name:
                    union(by_name[ident], key)
                else:
                    by_name[ident] = key
        # Strong matches across repositories.
        for key in sorted(self.corpus.groups, key=repr):
            repos = self.repos_of(key)
            vec, sh = self.corpus.vectors[key], self.corpus.shingles[key]
            for other in self.candidates(vec, sh):
                if other == key or not (self.repos_of(other) - repos):
                    continue
                if self.corpus.compare(key, other).score >= FAMILY_THRESHOLD:
                    union(key, other)
        return {k: find(k) for k in self.corpus.groups}

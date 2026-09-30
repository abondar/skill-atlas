"""Repository path helpers and exclusion rules (SPEC section 5.3)."""

from __future__ import annotations

import fnmatch
import itertools
import posixpath

DEFAULT_EXCLUDED_DIRS = frozenset(
    {".git", "node_modules", "vendor", ".venv", "venv", "dist", "build", "target", "__pycache__"}
)
_FIXTURE_PAIRS = (("test", "fixtures"), ("tests", "fixtures"))
_FIXTURE_SEGMENTS = frozenset({"testdata", "__fixtures__"})


def segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def parent(path: str) -> str:
    return posixpath.dirname(path)


def join(*parts: str) -> str:
    return posixpath.join(*[p for p in parts if p])


def is_under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix.rstrip("/") + "/")


def normalize_inside(base_dir: str, relative: str) -> str | None:
    """Resolve `relative` against `base_dir`. Return None when it escapes the repository."""
    if relative.startswith("/"):
        return None
    joined = (
        posixpath.normpath(posixpath.join(base_dir, relative))
        if base_dir
        else (posixpath.normpath(relative))
    )
    if joined == ".":
        return ""
    if joined == ".." or joined.startswith("../"):
        return None
    return joined


def in_default_excluded_dir(path: str) -> bool:
    return any(s in DEFAULT_EXCLUDED_DIRS for s in segments(path)[:-1])


def is_fixture_path(path: str) -> bool:
    segs = segments(path)[:-1]
    if any(s in _FIXTURE_SEGMENTS for s in segs):
        return True
    return any((a, b) in _FIXTURE_PAIRS for a, b in itertools.pairwise(segs))


def matches_glob(path: str, patterns: list[str]) -> bool:
    """fnmatch-style match against the path and each of its ancestor directories.

    `*` matches `/` as well, so `docs/*` covers the whole `docs` subtree.
    """
    if not patterns:
        return False
    segs = segments(path)
    candidates = ["/".join(segs[: i + 1]) for i in range(len(segs))]
    return any(fnmatch.fnmatchcase(c, p) for c in candidates for p in patterns)

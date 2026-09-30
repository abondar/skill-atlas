"""Parsing of the `scan` target argument (SPEC section 3.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from skill_atlas.errors import UsageError

_SHORT = re.compile(r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<name>[A-Za-z0-9._-]+)$")
_SSH = re.compile(r"^git@(?P<host>[^:]+):(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class LocalTarget:
    path: Path
    looks_remote: bool = False


@dataclass(frozen=True)
class GitHubTarget:
    host: str
    owner: str
    name: str
    # Segments after /tree/ or /blob/. The ref/path split needs the API, see scan.py.
    tree_segments: list[str] = field(default_factory=list)


Target = LocalTarget | GitHubTarget


def parse_target(raw: str, host: str = "github.com") -> Target:
    if not raw:
        raise UsageError("empty target")
    short = _SHORT.match(raw)
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return LocalTarget(candidate.resolve(), looks_remote=short is not None)
    if m := _SSH.match(raw):
        return GitHubTarget(m["host"].lower(), m["owner"], m["name"])
    if raw.startswith(("https://", "http://")):
        return _parse_url(raw)
    if short:
        return GitHubTarget(host, short["owner"], short["name"].removesuffix(".git"))
    raise UsageError(f"cannot parse target {raw!r}: expected a path, URL or owner/repo")


def _parse_url(raw: str) -> GitHubTarget:
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    segs = [unquote(s) for s in parts.path.split("/") if s]
    if not host or len(segs) < 2:
        raise UsageError(f"cannot parse repository URL {raw!r}")
    owner, name = segs[0], segs[1].removesuffix(".git")
    rest = segs[2:]
    tree: list[str] = []
    if rest and rest[0] in ("tree", "blob") and len(rest) > 1:
        tree = rest[1:]
    elif rest:
        raise UsageError(f"unsupported repository URL {raw!r}")
    return GitHubTarget(host, owner, name, tree)

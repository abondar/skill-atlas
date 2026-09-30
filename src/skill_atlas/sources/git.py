"""Local git helpers and a tree source over a git revision."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from skill_atlas.errors import AtlasError, UsageError
from skill_atlas.sources.base import EntryKind, TreeEntry
from skill_atlas.sources.indexed import IndexedTreeSource


def git(
    cwd: Path, *args: str, env: Mapping[str, str] | None = None, check: bool = False
) -> str | None:
    """Run git and return stdout, or None on failure (unless `check`)."""
    if shutil.which("git") is None:
        if check:
            raise AtlasError("git is not installed")
        return None
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        if check:
            message = proc.stderr.decode("utf-8", errors="replace").strip()
            raise AtlasError(f"git {args[0]} failed: {message}")
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def toplevel(path: Path) -> Path | None:
    out = git(path, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def head_commit(root: Path, ref: str = "HEAD") -> tuple[str, str] | None:
    out = git(root, "log", "-1", "--format=%H%n%cI", f"{ref}^{{commit}}", "--")
    if not out:
        return None
    sha, date = out.strip().split("\n")
    return sha, date


def resolve_ref(root: Path, ref: str) -> str:
    out = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if not out:
        raise UsageError(f"ref not found in local repository: {ref}")
    return out.strip()


def is_dirty(root: Path) -> bool:
    out = git(root, "status", "--porcelain")
    return bool(out and out.strip())


def origin_url(root: Path) -> str | None:
    out = git(root, "remote", "get-url", "origin")
    return strip_credentials(out.strip()) if out else None


def strip_credentials(url: str) -> str:
    """Drop user:password@ from a URL so tokens never reach a snapshot."""
    if "://" not in url:
        return url
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return parts._replace(netloc=host).geturl()


_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


def remote_to_key(url: str) -> tuple[str, str] | None:
    """Parse a git remote into (host, path) without `.git`, both lower-cased."""
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        m = _SCP_LIKE.match(url)
        if not m:
            return None
        host, path = m["host"], m["path"]
    path = path.strip("/")
    path = path.removesuffix(".git")
    if not host or not path:
        return None
    return host.lower(), path.lower()


def ls_tree(
    root: Path, sha: str, *, with_sizes: bool = True, env: Mapping[str, str] | None = None
) -> list[TreeEntry]:
    """List all entries of a commit.

    In a blobless partial clone pass `with_sizes=False`: `-l` needs every blob and
    makes git fetch them one by one from the remote.
    """
    args = ["ls-tree", "-r", "-z", "--full-tree"]
    if with_sizes:
        args.append("-l")
    out = git(root, *args, sha, env=env, check=True)
    assert out is not None
    entries: list[TreeEntry] = []
    for record in out.split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        fields = meta.split()
        mode, obj_type, obj_sha = fields[:3]
        size = fields[3] if with_sizes else "-"
        kind: EntryKind
        if obj_type == "commit":
            kind = "submodule"
        elif mode == "120000":
            kind = "symlink"
        else:
            kind = "file"
        entries.append(
            TreeEntry(path, kind, size=None if size == "-" else int(size), blob_sha=obj_sha)
        )
    return entries


class GitTreeSource(IndexedTreeSource):
    def __init__(self, root: Path, sha: str, env: Mapping[str, str] | None = None) -> None:
        self.root = root
        self.sha = sha
        self._env = env
        super().__init__(ls_tree(root, sha), self._read)

    def _read(self, entry: TreeEntry) -> bytes:
        assert entry.blob_sha is not None
        proc = subprocess.run(
            ["git", "cat-file", "blob", entry.blob_sha],
            cwd=self.root,
            capture_output=True,
            env=self._env,
            check=False,
        )
        if proc.returncode != 0:
            raise OSError(f"cannot read blob {entry.blob_sha} for {entry.path}")
        return proc.stdout

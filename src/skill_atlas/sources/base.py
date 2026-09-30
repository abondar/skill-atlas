"""Tree source abstraction: a read-only view of repository files at one revision."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol

MAX_TREE_ENTRIES = 200_000
MAX_FILE_BYTES = 1024 * 1024

EntryKind = Literal["file", "symlink", "submodule"]


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: EntryKind
    size: int | None = None
    blob_sha: str | None = None


class TreeSource(Protocol):
    def entries(self, prefix: str) -> list[TreeEntry]:
        """All entries under `prefix` (repo-relative, "" for the root), sorted by path.

        Implementations skip DEFAULT_EXCLUDED_DIRS and stop at MAX_TREE_ENTRIES.
        """
        ...

    def read(self, path: str) -> bytes: ...

    def link_target(self, path: str) -> str: ...

    def blob_sha(self, entry: TreeEntry) -> str | None: ...

    @property
    def warnings(self) -> list[str]: ...


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()

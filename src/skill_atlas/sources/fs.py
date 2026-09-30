"""Tree source over a working directory on disk."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from skill_atlas import paths
from skill_atlas.sources.base import MAX_FILE_BYTES, MAX_TREE_ENTRIES, TreeEntry, git_blob_sha


class FsTreeSource:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def entries(self, prefix: str) -> list[TreeEntry]:
        start = self.root / prefix if prefix else self.root
        if not start.is_dir() or start.is_symlink():
            return []
        found: list[TreeEntry] = []
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            keep: list[str] = []
            for d in sorted(dirnames):
                full = Path(dirpath) / d
                rel = paths.join(rel_dir, d)
                if d in paths.DEFAULT_EXCLUDED_DIRS:
                    continue
                if full.is_symlink():
                    found.append(TreeEntry(rel, "symlink"))
                elif (full / ".git").is_file():
                    found.append(TreeEntry(rel, "submodule"))
                else:
                    keep.append(d)
            dirnames[:] = keep
            for f in filenames:
                if f == ".git":
                    continue
                full = Path(dirpath) / f
                rel = paths.join(rel_dir, f)
                try:
                    st = full.lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode):
                    found.append(TreeEntry(rel, "symlink"))
                elif stat.S_ISREG(st.st_mode):
                    found.append(TreeEntry(rel, "file", size=st.st_size))
            if len(found) > MAX_TREE_ENTRIES:
                self._warnings.append(
                    f"tree has more than {MAX_TREE_ENTRIES} entries; the rest were skipped"
                )
                found = found[:MAX_TREE_ENTRIES]
                break
        found.sort(key=lambda e: e.path)
        return found

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def link_target(self, path: str) -> str:
        return (self.root / path).readlink().as_posix()

    def blob_sha(self, entry: TreeEntry) -> str | None:
        if entry.kind != "file" or entry.size is None or entry.size > MAX_FILE_BYTES:
            return None
        try:
            return git_blob_sha(self.read(entry.path))
        except OSError:
            return None

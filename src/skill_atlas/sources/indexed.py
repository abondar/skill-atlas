"""Tree source backed by a complete, pre-fetched listing (git ls-tree or GitHub Trees API)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from skill_atlas import paths
from skill_atlas.sources.base import MAX_TREE_ENTRIES, TreeEntry


class IndexedTreeSource:
    def __init__(
        self,
        listing: Iterable[TreeEntry],
        read_blob: Callable[[TreeEntry], bytes],
    ) -> None:
        self._warnings: list[str] = []
        kept: list[TreeEntry] = []
        for entry in listing:
            if paths.in_default_excluded_dir(entry.path):
                continue
            if len(kept) >= MAX_TREE_ENTRIES:
                self._warnings.append(
                    f"tree has more than {MAX_TREE_ENTRIES} entries; the rest were skipped"
                )
                break
            kept.append(entry)
        kept.sort(key=lambda e: e.path)
        self._entries = kept
        self._by_path = {e.path: e for e in kept}
        self._read_blob = read_blob

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def entries(self, prefix: str) -> list[TreeEntry]:
        if not prefix:
            return list(self._entries)
        return [e for e in self._entries if paths.is_under(e.path, prefix) and e.path != prefix]

    def read(self, path: str) -> bytes:
        return self._read_blob(self._by_path[path])

    def link_target(self, path: str) -> str:
        return self._read_blob(self._by_path[path]).decode("utf-8", errors="replace")

    def blob_sha(self, entry: TreeEntry) -> str | None:
        return entry.blob_sha

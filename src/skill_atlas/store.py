"""Snapshot store: a flat directory of immutable JSON files (SPEC section 7)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from skill_atlas import SCHEMA_VERSION
from skill_atlas.errors import StoreError
from skill_atlas.model import ScanOptions, Snapshot

_SLUG_BAD = re.compile(r"[^a-z0-9.-]+")


def home() -> Path:
    if env := os.environ.get("SKILL_ATLAS_HOME"):
        return Path(env).expanduser()
    if xdg := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg).expanduser() / "skill-atlas"
    return Path.home() / ".local" / "share" / "skill-atlas"


def scans_dir() -> Path:
    return home() / "scans"


def slug(repo_key: str) -> str:
    return _SLUG_BAD.sub("-", repo_key.lower()).strip("-")


def filename(snapshot: Snapshot) -> str:
    stamp = snapshot.scan.scanned_at.replace("-", "").replace(":", "")
    sha = (snapshot.source.commit_sha or "")[:8] or "nosha"
    suffix = "_dirty" if snapshot.source.dirty else ""
    return f"{stamp}_{slug(snapshot.source.repo_key)}_{sha}{suffix}.json"


def dumps(snapshot: Snapshot) -> str:
    data = snapshot.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_new(path: Path, content: str) -> Path:
    """Write `content` to `path` atomically; never overwrite. Add -1, -2... on collision."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        stem, ext = path.stem, path.suffix
        for i in range(1000):
            target = path if i == 0 else path.with_name(f"{stem}-{i}{ext}")
            try:
                os.link(tmp, target)
            except FileExistsError:
                continue
            return target
        raise StoreError(f"cannot find a free file name for {path}")
    except OSError as exc:
        raise StoreError(f"cannot write {path}: {exc}") from None
    finally:
        tmp.unlink(missing_ok=True)


def save(snapshot: Snapshot, directory: Path) -> Path:
    return write_new(directory / filename(snapshot), dumps(snapshot))


def load(path: Path) -> Snapshot:
    """Load one snapshot, migrating older schema versions in memory."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreError(f"cannot read snapshot {path}: {exc}") from None
    if not isinstance(data, dict):
        raise StoreError(f"{path}: snapshot is not a JSON object")
    version = data.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise StoreError(f"{path}: unsupported schema_version {version!r}")
    data = _migrate(data, version)
    try:
        return Snapshot.model_validate(data)
    except ValidationError as exc:
        raise StoreError(
            f"{path}: invalid snapshot: {exc.error_count()} validation errors"
        ) from None


def _migrate(data: dict[str, object], version: int) -> dict[str, object]:
    # Only schema version 1 exists. Future migrations chain here: v1 -> v2 -> ...
    # Detectors v1 wrote `include_fixtures`; v2 keeps fixtures as category `test`.
    scan = data.get("scan")
    options = scan.get("options") if isinstance(scan, dict) else None
    if isinstance(options, dict):
        options.pop("include_fixtures", None)
    return data


@dataclass(frozen=True)
class Loaded:
    path: Path
    snapshot: Snapshot


def iter_snapshots(directory: Path, warnings: list[str]) -> Iterator[Loaded]:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            yield Loaded(path, load(path))
        except StoreError as exc:
            warnings.append(f"skipped: {exc}")


def find_cached(
    directory: Path,
    repo_key: str,
    commit_sha: str,
    detectors_version: int,
    options: ScanOptions,
) -> Loaded | None:
    """Latest clean snapshot of the same commit, detectors and options (SPEC 7.3)."""
    if not directory.is_dir():
        return None
    marker = f"_{slug(repo_key)}_{commit_sha[:8]}"
    for path in sorted(directory.glob(f"*{marker}*.json"), reverse=True):
        if "_dirty" in path.name:
            continue
        try:
            snap = load(path)
        except StoreError:
            continue
        s = snap.source
        if (
            s.repo_key == repo_key
            and s.commit_sha == commit_sha
            and not s.dirty
            and snap.scan.detectors_version == detectors_version
            and snap.scan.options.cache_key() == options.cache_key()
        ):
            return Loaded(path, snap)
    return None

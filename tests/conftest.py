from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from skill_atlas import store
from skill_atlas.model import Snapshot
from skill_atlas.scan import ScanRequest, run_scan

GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass(frozen=True)
class Link:
    target: str


FileSpec = Mapping[str, "str | bytes | Link"]


def make_tree(root: Path, spec: FileSpec) -> Path:
    for rel, content in spec.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Link):
            path.symlink_to(content.target)
        elif isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def git(root: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    )
    return subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout


def make_git_repo(root: Path, spec: FileSpec, remote: str | None = None) -> str:
    make_tree(root, spec)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    if remote:
        git(root, "remote", "add", "origin", remote)
    return git(root, "rev-parse", "HEAD").strip()


def scan_path(root: Path, store_dir: Path | None = None, **kwargs: Any) -> Snapshot:
    return run_scan(ScanRequest(target=str(root), **kwargs), store_dir=store_dir).snapshot


def normalized(snapshot: Snapshot) -> dict[str, Any]:
    """Snapshot JSON without fields that change between runs or machines."""
    data: dict[str, Any] = json.loads(store.dumps(snapshot))
    for key in ("id", "scanned_at", "duration_ms"):
        data["scan"].pop(key)
    data["source"].pop("local_path")
    data["source"].pop("repo_key")
    return data


def assert_golden(name: str, snapshot: Snapshot) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    actual = json.dumps(normalized(snapshot), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text(encoding="utf-8"), f"golden mismatch: {path}"


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store" / "scans"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_ATLAS_HOME", str(tmp_path / "home"))
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)

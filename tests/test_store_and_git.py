from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_atlas import store
from skill_atlas.errors import StoreError, UsageError
from skill_atlas.scan import ScanRequest, run_scan
from tests.cases import CASES, skill
from tests.conftest import make_git_repo, make_tree, normalized, scan_path


def test_filename_is_sortable_and_slugged(tmp_path: Path, store_dir: Path) -> None:
    root = make_tree(tmp_path / "My Repo", CASES["d1_agent_skill"])
    outcome = run_scan(ScanRequest(target=str(root)), store_dir=store_dir)
    assert outcome.saved_path is not None
    name = outcome.saved_path.name
    assert name.endswith("_nosha.json")
    assert name[:8].isdigit() and name[8] == "T"
    assert "_local-my-repo-" in name


def test_write_new_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "a.json"
    first = store.write_new(target, "1")
    second = store.write_new(target, "2")
    assert (first.name, second.name) == ("a.json", "a-1.json")
    assert first.read_text() == "1"
    assert not list(tmp_path.glob(".tmp-*"))


def test_git_identity_and_cache_hit(tmp_path: Path, store_dir: Path) -> None:
    root = tmp_path / "repo"
    sha = make_git_repo(root, CASES["d1_agent_skill"], remote="git@github.com:Org/Repo.git")
    first = run_scan(ScanRequest(target=str(root)), store_dir=store_dir)
    src = first.snapshot.source
    assert (src.repo_key, src.commit_sha, src.dirty) == ("github.com/org/repo", sha, False)
    assert (src.owner, src.name, src.commit_date) == ("org", "repo", "2026-01-01T00:00:00Z")
    second = run_scan(ScanRequest(target=str(root)), store_dir=store_dir)
    assert second.cache_hit
    assert second.saved_path == first.saved_path
    assert len(list(store_dir.glob("*.json"))) == 1
    forced = run_scan(ScanRequest(target=str(root), force=True), store_dir=store_dir)
    assert not forced.cache_hit
    assert len(list(store_dir.glob("*.json"))) == 2
    other_opts = run_scan(ScanRequest(target=str(root), path="skills"), store_dir=store_dir)
    assert not other_opts.cache_hit


def test_dirty_tree_always_writes(tmp_path: Path, store_dir: Path) -> None:
    root = tmp_path / "repo"
    make_git_repo(root, CASES["d1_agent_skill"])
    (root / "skills" / "new").mkdir()
    (root / "skills" / "new" / "SKILL.md").write_text(skill("new"))
    a = run_scan(ScanRequest(target=str(root)), store_dir=store_dir)
    b = run_scan(ScanRequest(target=str(root)), store_dir=store_dir)
    assert a.snapshot.source.dirty and not b.cache_hit
    assert a.saved_path is not None and a.saved_path.name.endswith("_dirty.json")
    assert any(s.path == "skills/new/SKILL.md" for s in a.snapshot.skills)
    assert a.snapshot.source.repo_key.startswith("local/repo-")


def test_ref_reads_git_tree_not_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_git_repo(root, CASES["d1_agent_skill"])
    (root / "skills" / "unscoped" / "SKILL.md").unlink()
    snap = scan_path(root, ref="main")
    assert snap.scan.fetch_method == "git"
    assert snap.source.dirty is None
    assert any(s.path == "skills/unscoped/SKILL.md" for s in snap.skills)


def test_fs_and_git_tree_give_same_skills(tmp_path: Path) -> None:
    spec = {
        k: v
        for case in ("d2_claude_command", "d3_d4_plugin", "symlinks", "nested", "noise")
        for k, v in CASES[case].items()
    }
    root = tmp_path / "repo"
    make_git_repo(root, spec)
    fs = normalized(scan_path(root))
    tree = normalized(scan_path(root, ref="HEAD"))
    assert fs["skills"] == tree["skills"]
    assert fs["plugins"] == tree["plugins"]


def test_subdirectory_target_maps_to_repo_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_git_repo(root, CASES["d1_agent_skill"])
    snap = scan_path(root / ".claude")
    assert snap.scan.options.path == ".claude"
    assert [s.path for s in snap.skills] == [".claude/skills/pdf/SKILL.md"]


def test_ref_without_git_is_usage_error(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "plain", CASES["empty"])
    with pytest.raises(UsageError):
        scan_path(root, ref="main")


def test_load_rejects_future_schema(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema_version": 99}))
    with pytest.raises(StoreError, match="unsupported schema_version"):
        store.load(path)

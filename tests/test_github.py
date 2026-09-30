"""GitHub source tests against a fake API built from a real local git repository."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import respx

from skill_atlas import scan as scan_module
from skill_atlas.errors import AccessError, NetworkError, RateLimitError
from skill_atlas.scan import ScanRequest, run_scan
from skill_atlas.sources.base import TreeEntry
from skill_atlas.sources.git import GitTreeSource, ls_tree
from skill_atlas.sources.github import GitHubClient
from tests.cases import CASES
from tests.conftest import git, make_git_repo, normalized, scan_path

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class FakeGitHub:
    def __init__(self, root: Path, owner: str = "Org", name: str = "Repo") -> None:
        self.root, self.owner, self.name = root, owner, name
        self.truncated = False
        self.fail_raw: set[str] = set()
        self.requests: list[httpx.Request] = []

    def refs(self) -> dict[str, str]:
        out = {}
        for line in git(self.root, "show-ref").splitlines():
            sha, ref = line.split()
            out[ref.removeprefix("refs/heads/").removeprefix("refs/tags/")] = sha
        return out

    def install(self, router: respx.MockRouter) -> None:
        base = f"{API}/repos/{self.owner}/{self.name}"
        router.get(re.compile(rf"{re.escape(base)}$")).mock(side_effect=self._repo)
        router.get(re.compile(rf"{re.escape(base)}/commits/(?P<ref>.+)$")).mock(
            side_effect=self._commit
        )
        router.get(re.compile(rf"{re.escape(base)}/git/trees/(?P<sha>\w+)")).mock(
            side_effect=self._tree
        )
        router.get(re.compile(rf"{re.escape(base)}/git/blobs/(?P<sha>\w+)")).mock(
            side_effect=self._blob
        )
        router.get(re.compile(rf"{RAW}/{self.owner}/{self.name}/(?P<sha>\w+)/(?P<path>.+)$")).mock(
            side_effect=self._raw
        )

    def _log(self, request: httpx.Request) -> None:
        self.requests.append(request)

    def _repo(self, request: httpx.Request) -> httpx.Response:
        self._log(request)
        return httpx.Response(
            200,
            json={
                "full_name": f"{self.owner}/{self.name}",
                "default_branch": "main",
                "node_id": "R_node1",
                "description": "Fake repo",
                "license": {"spdx_id": "MIT"},
                "topics": ["skills"],
                "stargazers_count": 5,
                "forks_count": 1,
                "visibility": "public",
                "archived": False,
                "fork": False,
                "pushed_at": "2026-01-01T00:00:00Z",
            },
        )

    def _commit(self, request: httpx.Request, ref: str) -> httpx.Response:
        self._log(request)
        ref = unquote(ref)
        sha = self.refs().get(ref) or (ref if re.fullmatch(r"[0-9a-f]{40}", ref) else None)
        if sha is None:
            return httpx.Response(422, json={"message": "No commit found"})
        return httpx.Response(
            200,
            json={
                "sha": sha,
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}},
            },
        )

    def _tree(self, request: httpx.Request, sha: str) -> httpx.Response:
        self._log(request)
        items: list[dict[str, Any]] = []
        for e in ls_tree(self.root, sha):
            mode = {"symlink": "120000", "submodule": "160000"}.get(e.kind, "100644")
            item_type = "commit" if e.kind == "submodule" else "blob"
            items.append(
                {"path": e.path, "mode": mode, "type": item_type, "sha": e.blob_sha, "size": e.size}
            )
        return httpx.Response(200, json={"sha": sha, "tree": items, "truncated": self.truncated})

    def _blob(self, request: httpx.Request, sha: str) -> httpx.Response:
        self._log(request)
        data = git(self.root, "cat-file", "blob", sha).encode()
        return httpx.Response(200, json={"content": base64.b64encode(data).decode()})

    def _raw(self, request: httpx.Request, sha: str, path: str) -> httpx.Response:
        self._log(request)
        path = unquote(path)
        if path in self.fail_raw:
            return httpx.Response(404, text="404: Not Found")
        entries = {e.path: e for e in ls_tree(self.root, sha)}
        entry = entries.get(path)
        if entry is None or entry.blob_sha is None:
            return httpx.Response(404, text="404: Not Found")
        tree = GitTreeSource(self.root, sha)
        return httpx.Response(200, content=tree.read(path))


SPEC = {
    k: v
    for case in ("d2_claude_command", "d3_d4_plugin", "symlinks", "nested", "noise")
    for k, v in CASES[case].items()
}


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[FakeGitHub]:
    root = tmp_path / "repo"
    make_git_repo(root, SPEC)
    gh = FakeGitHub(root)
    with respx.mock(assert_all_called=False) as router:
        gh.install(router)
        yield gh


def client(token: str | None = None) -> GitHubClient:
    return GitHubClient("github.com", token, sleep=lambda _: None)


def gh_scan(target: str = "Org/Repo", token: str | None = None, **kwargs: Any) -> Any:
    return run_scan(ScanRequest(target=target, **kwargs), store_dir=None, client=client(token))


def test_remote_scan_matches_local_scan(fake: FakeGitHub) -> None:
    remote = gh_scan().snapshot
    local = scan_path(fake.root, ref="HEAD")
    assert normalized(remote)["skills"] == normalized(local)["skills"]
    assert normalized(remote)["plugins"] == normalized(local)["plugins"]
    src = remote.source
    assert (src.kind, src.repo_key, src.node_id, src.resolved_ref) == (
        "github",
        "github.com/org/repo",
        "R_node1",
        "main",
    )
    assert remote.repo is not None and remote.repo.license == "MIT"
    assert remote.scan.fetch_method == "api"


def test_url_with_slash_branch_and_path(fake: FakeGitHub) -> None:
    git(fake.root, "branch", "feature/x")
    snap = gh_scan("https://github.com/Org/Repo/tree/feature/x/plugins").snapshot
    assert snap.source.resolved_ref == "feature/x"
    assert snap.scan.options.path == "plugins"
    assert snap.skills and all(s.path is None or s.path.startswith("plugins/") for s in snap.skills)


def test_explicit_ref_and_missing_ref(fake: FakeGitHub) -> None:
    sha = git(fake.root, "rev-parse", "HEAD").strip()
    assert gh_scan(ref=sha).snapshot.source.commit_sha == sha
    with pytest.raises(AccessError, match="ref not found"):
        gh_scan(ref="nope")


def test_token_is_sent_and_not_stored(fake: FakeGitHub) -> None:
    snap = gh_scan(token="ghp_secret").snapshot
    assert all(r.headers.get("authorization") == "Bearer ghp_secret" for r in fake.requests)
    assert "ghp_secret" not in snap.model_dump_json()


def test_single_file_failure_is_per_skill(fake: FakeGitHub) -> None:
    fake.fail_raw.add(".claude/commands/deploy.md")
    snap = gh_scan().snapshot
    deploy = next(s for s in snap.skills if s.path == ".claude/commands/deploy.md")
    assert deploy.compliance.status == "broken"
    assert deploy.compliance.violations[0].code == "unreadable"


def test_truncated_tree_uses_clone_listing_and_http_reads(
    fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.truncated = True
    calls: list[tuple[str, str, str, str]] = []

    def fake_clone_listing(
        host: str, owner: str, name: str, sha: str, token: str | None, progress: object
    ) -> list[TreeEntry]:
        calls.append((host, owner, name, sha))
        return ls_tree(fake.root, sha, with_sizes=False)

    monkeypatch.setattr(scan_module, "clone_listing", fake_clone_listing)
    progress = RecordingProgress()
    snap = run_scan(
        ScanRequest(target="Org/Repo"), store_dir=None, client=client(), progress=progress
    ).snapshot
    assert snap.scan.fetch_method == "clone"
    assert len(calls) == 1
    assert any("truncated" in s for s in progress.stages)
    # File contents still come over HTTP, not from git.
    assert any("raw.githubusercontent.com" in str(r.url) for r in fake.requests)

    def without_sizes(data: dict[str, Any]) -> list[dict[str, Any]]:
        for s in data["skills"]:
            for r in s["resources"]:
                r["bytes"] = None
        return list(data["skills"])

    local = normalized(scan_path(fake.root, ref="HEAD"))
    assert without_sizes(normalized(snap)) == without_sizes(local)


def test_ls_tree_in_blobless_clone_does_not_fetch_blobs(tmp_path: Path) -> None:
    """Regression: `ls-tree -l` in a partial clone fetched every blob one by one."""
    origin = tmp_path / "origin"
    sha = make_git_repo(origin, SPEC)
    git(origin, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone"
    clone.mkdir()
    git(clone, "init", "-q")
    git(clone, "fetch", "-q", "--depth", "1", "--filter=blob:none", f"file://{origin}", sha)
    # Remove the origin: any lazy fetch attempt now fails loudly.
    import shutil

    shutil.rmtree(origin)
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    entries = ls_tree(clone, sha, with_sizes=False, env=env)
    assert any(e.path == "plugins/tools/cmds/status.md" for e in entries)
    assert all(e.size is None and e.blob_sha for e in entries if e.kind == "file")


def test_progress_stages(fake: FakeGitHub) -> None:
    progress = RecordingProgress()
    run_scan(ScanRequest(target="Org/Repo"), store_dir=None, client=client(), progress=progress)
    assert progress.stages[:3] == [
        "fetching github.com/Org/Repo metadata",
        "resolving ref",
        f"fetching file tree of main@{git(fake.root, 'rev-parse', 'HEAD')[:8]}",
    ]
    assert any(s.startswith("reading ") and "candidate files" in s for s in progress.stages)
    assert progress.finished


class RecordingProgress:
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.details: list[str] = []
        self.finished = False

    def stage(self, message: str) -> None:
        self.stages.append(message)

    def detail(self, message: str) -> None:
        self.details.append(message)

    def done(self) -> None:
        self.finished = True


def test_repo_not_found() -> None:
    with respx.mock() as router:
        router.get(f"{API}/repos/o/missing").mock(return_value=httpx.Response(404, json={}))
        with pytest.raises(AccessError, match="GITHUB_TOKEN"):
            gh_scan("o/missing")


def test_rate_limit() -> None:
    with respx.mock() as router:
        router.get(f"{API}/repos/o/r").mock(
            return_value=httpx.Response(
                403,
                json={"message": "rate limit"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"},
            )
        )
        with pytest.raises(RateLimitError, match="resets at 2027-01-15"):
            gh_scan("o/r")


def test_bad_token() -> None:
    with respx.mock() as router:
        router.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(401, json={}))
        with pytest.raises(AccessError, match="401"):
            gh_scan("o/r", token="bad")


def test_retries_5xx_then_fails() -> None:
    with respx.mock() as router:
        route = router.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(502))
        with pytest.raises(NetworkError, match="after 4 attempts"):
            gh_scan("o/r")
        assert route.call_count == 4


def test_retry_recovers() -> None:
    with respx.mock() as router:
        router.get(f"{API}/repos/o/r").mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.Response(200, json={"full_name": "o/r", "default_branch": "main"}),
            ]
        )
        assert client().repo("o", "r")["full_name"] == "o/r"

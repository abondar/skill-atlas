"""Scan orchestration: target -> identity -> tree source -> detection -> snapshot."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skill_atlas import DETECTORS_VERSION, __version__, store
from skill_atlas.errors import AccessError, UsageError
from skill_atlas.model import (
    FetchMethod,
    RepoMeta,
    ScanInfo,
    ScanOptions,
    Snapshot,
    SourceInfo,
    Stats,
)
from skill_atlas.progress import NullProgress, Progress
from skill_atlas.scanner import Scanner
from skill_atlas.sources import git as gitsrc
from skill_atlas.sources.base import TreeSource
from skill_atlas.sources.fs import FsTreeSource
from skill_atlas.sources.github import (
    GitHubClient,
    GitHubTreeSource,
    clone_listing,
    find_token,
    tree_entries,
)
from skill_atlas.target import GitHubTarget, LocalTarget, parse_target

MAX_REF_SPLIT_ATTEMPTS = 5


@dataclass
class ScanRequest:
    target: str
    ref: str | None = None
    path: str | None = None
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    host: str = "github.com"
    force: bool = False


@dataclass
class ScanOutcome:
    snapshot: Snapshot
    saved_path: Path | None
    cache_hit: bool
    notices: list[str] = field(default_factory=list)


@dataclass
class _Prepared:
    source: SourceInfo
    repo: RepoMeta | None
    options: ScanOptions
    fetch_method: FetchMethod
    open_tree: Callable[[], Any]  # context manager factory yielding a TreeSource


def _now() -> str:
    now = dt.datetime.now(dt.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_scan_id() -> str:
    """ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _clean_path(path: str | None) -> str | None:
    if path is None:
        return None
    cleaned = path.strip().strip("/")
    if ".." in cleaned.split("/"):
        raise UsageError("--path must not contain '..'")
    return cleaned or None


@contextmanager
def _static(tree: TreeSource) -> Iterator[TreeSource]:
    yield tree


def prepare_local(req: ScanRequest, target: LocalTarget) -> _Prepared:
    path = target.path
    if not path.is_dir():
        raise UsageError(f"not a directory: {path}")
    top = gitsrc.toplevel(path)
    user_path = _clean_path(req.path)
    if top is None:
        if req.ref:
            raise UsageError("--ref needs a git repository")
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:8]
        source = SourceInfo(
            kind="local",
            repo_key=f"local/{path.name.lower()}-{digest}",
            name=path.name,
            local_path=str(path),
        )
        options = ScanOptions(
            path=user_path,
            include=req.include,
            exclude=req.exclude,
        )
        return _Prepared(source, None, options, "fs", lambda: _static(FsTreeSource(path)))

    top = top.resolve()
    rel = path.relative_to(top).as_posix()
    rel = "" if rel == "." else rel
    scan_path = "/".join(p for p in (rel, user_path or "") if p) or None
    remote = gitsrc.origin_url(top)
    parsed = gitsrc.remote_to_key(remote) if remote else None
    if parsed:
        host, repo_path = parsed
        repo_key = f"{host}/{repo_path}"
        segs = repo_path.split("/")
        owner, name = ("/".join(segs[:-1]) or None), segs[-1]
    else:
        host, owner, name = None, None, top.name
        digest = hashlib.sha256(str(top).encode()).hexdigest()[:8]
        repo_key = f"local/{top.name.lower()}-{digest}"
    options = ScanOptions(
        ref=req.ref,
        path=scan_path,
        include=req.include,
        exclude=req.exclude,
    )
    common: dict[str, Any] = {
        "kind": "local",
        "repo_key": repo_key,
        "host": host,
        "owner": owner,
        "name": name,
        "local_path": str(top),
        "remote_url": remote,
    }
    if req.ref:
        sha = gitsrc.resolve_ref(top, req.ref)
        info = gitsrc.head_commit(top, sha)
        source = SourceInfo(
            **common,
            requested_ref=req.ref,
            resolved_ref=req.ref,
            commit_sha=sha,
            commit_date=info[1] if info else None,
            dirty=None,
        )
        return _Prepared(
            source, None, options, "git", lambda: _static(gitsrc.GitTreeSource(top, sha))
        )
    info = gitsrc.head_commit(top)
    source = SourceInfo(
        **common,
        resolved_ref=gitsrc.current_branch(top) or "HEAD",
        commit_sha=info[0] if info else None,
        commit_date=info[1] if info else None,
        dirty=gitsrc.is_dirty(top),
    )
    return _Prepared(source, None, options, "fs", lambda: _static(FsTreeSource(top)))


def _resolve_github_ref(
    client: GitHubClient, target: GitHubTarget, req: ScanRequest, default_branch: str
) -> tuple[str, str | None, dict[str, Any]]:
    """Return (ref, path from URL, commit JSON)."""
    segs = target.tree_segments
    if req.ref:
        commit = client.commit(target.owner, target.name, req.ref)
        if commit is None:
            raise AccessError(f"ref not found: {req.ref}")
        ref_segs = req.ref.split("/")
        url_path = "/".join(segs[len(ref_segs) :]) if segs[: len(ref_segs)] == ref_segs else None
        return req.ref, url_path or None, commit
    if segs:
        # Branch names may contain '/': try the shortest ref prefix first.
        for i in range(1, min(len(segs), MAX_REF_SPLIT_ATTEMPTS) + 1):
            ref = "/".join(segs[:i])
            commit = client.commit(target.owner, target.name, ref)
            if commit is not None:
                return ref, "/".join(segs[i:]) or None, commit
        raise AccessError(f"ref not found in URL: {'/'.join(segs)}")
    commit = client.commit(target.owner, target.name, default_branch)
    if commit is None:
        raise AccessError(f"default branch {default_branch!r} not found")
    return default_branch, None, commit


def prepare_github(
    req: ScanRequest,
    target: GitHubTarget,
    client: GitHubClient | None = None,
    progress: Progress | None = None,
) -> _Prepared:
    progress = progress or NullProgress()
    if client is None:
        client = GitHubClient(target.host, find_token(target.host))
    progress.stage(f"fetching {target.host}/{target.owner}/{target.name} metadata")
    repo = client.repo(target.owner, target.name)
    full_name = str(repo.get("full_name") or f"{target.owner}/{target.name}")
    owner, name = full_name.split("/", 1)
    target = GitHubTarget(target.host, owner, name, target.tree_segments)
    default_branch = str(repo.get("default_branch") or "main")
    progress.stage("resolving ref")
    ref, url_path, commit = _resolve_github_ref(client, target, req, default_branch)
    sha = str(commit["sha"])
    commit_date = ((commit.get("commit") or {}).get("committer") or {}).get("date")
    scan_path = _clean_path(req.path) or _clean_path(url_path)

    license_info = repo.get("license") or {}
    meta = RepoMeta(
        description=repo.get("description"),
        default_branch=default_branch,
        license=license_info.get("spdx_id") if isinstance(license_info, dict) else None,
        topics=list(repo.get("topics") or []),
        stars=repo.get("stargazers_count"),
        forks=repo.get("forks_count"),
        visibility=repo.get("visibility") or ("private" if repo.get("private") else "public"),
        archived=repo.get("archived"),
        is_fork=repo.get("fork"),
        pushed_at=repo.get("pushed_at"),
    )
    source = SourceInfo(
        kind="github",
        repo_key=f"{target.host}/{owner}/{name}".lower(),
        host=target.host,
        owner=owner,
        name=name,
        url=f"https://{target.host}/{owner}/{name}",
        node_id=repo.get("node_id"),
        requested_ref=req.ref or ("/".join(target.tree_segments) or None),
        resolved_ref=ref,
        commit_sha=sha,
        commit_date=commit_date,
    )
    options = ScanOptions(
        ref=req.ref,
        path=scan_path,
        include=req.include,
        exclude=req.exclude,
    )
    prepared = _Prepared(source, meta, options, "api", lambda: None)

    @contextmanager
    def open_tree() -> Iterator[TreeSource]:
        progress.stage(f"fetching file tree of {ref}@{sha[:8]}")
        tree = client.tree(owner, name, sha)
        listing = tree_entries(tree)
        if tree.get("truncated"):
            prepared.fetch_method = "clone"
            progress.stage(
                f"Trees API truncated the listing at {len(listing)} entries; "
                "fetching tree objects with git (no file contents)"
            )
            listing = clone_listing(target.host, owner, name, sha, client.token, progress)
        yield GitHubTreeSource(client, owner, name, sha, listing)

    prepared.open_tree = open_tree
    return prepared


def run_scan(
    req: ScanRequest,
    *,
    store_dir: Path | None,
    save: bool = True,
    client: GitHubClient | None = None,
    progress: Progress | None = None,
) -> ScanOutcome:
    progress = progress or NullProgress()
    try:
        return _run_scan(req, store_dir, save, client, progress)
    finally:
        progress.done()


def _run_scan(
    req: ScanRequest,
    store_dir: Path | None,
    save: bool,
    client: GitHubClient | None,
    progress: Progress,
) -> ScanOutcome:
    started = time.monotonic()
    target = parse_target(req.target, req.host)
    notices: list[str] = []
    if isinstance(target, LocalTarget):
        if target.looks_remote:
            notices.append(
                f"{req.target!r} exists on disk and is scanned as a local path; "
                "use a URL to scan the GitHub repository"
            )
        progress.stage(f"reading local repository {target.path}")
        prepared = prepare_local(req, target)
    else:
        prepared = prepare_github(req, target, client, progress)

    src = prepared.source
    if store_dir is not None and not req.force and src.commit_sha and not src.dirty:
        cached = store.find_cached(
            store_dir, src.repo_key, src.commit_sha, DETECTORS_VERSION, prepared.options
        )
        if cached is not None:
            return ScanOutcome(cached.snapshot, cached.path, cache_hit=True, notices=notices)

    with prepared.open_tree() as tree:
        result = Scanner(tree, prepared.options, progress).run()

    snapshot = Snapshot(
        scan=ScanInfo(
            id=new_scan_id(),
            scanned_at=_now(),
            tool_version=__version__,
            detectors_version=DETECTORS_VERSION,
            fetch_method=prepared.fetch_method,
            duration_ms=int((time.monotonic() - started) * 1000),
            options=prepared.options,
            excluded_candidates=result.excluded_candidates,
            warnings=result.warnings,
        ),
        source=src,
        repo=prepared.repo,
        plugins=result.plugins,
        skills=result.skills,
        stats=Stats.of(result.skills),
    )
    saved = None
    if save and store_dir is not None:
        progress.stage("saving snapshot")
        saved = store.save(snapshot, store_dir)
    return ScanOutcome(snapshot, saved, cache_hit=False, notices=notices)

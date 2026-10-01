"""Organization scan: every repository of a GitHub organization or user (SPEC section 3.4).

Cost in REST API requests:
- the account and the listing: 1 + one per 100 repositories (+1 for the token owner's
  own account, to list private repositories);
- a repository whose `pushed_at` and default branch match its latest snapshot: 0;
- any other repository: 2 (commit, tree). File contents come from
  raw.githubusercontent.com, outside the API quota (contents API on GitHub Enterprise).

Each repository gets its own snapshot as soon as it is scanned, so an interrupted scan
continues where it stopped: the next run finds those snapshots unchanged.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from skill_atlas import DETECTORS_VERSION, __version__, aggregate, store
from skill_atlas import scan as scan_module
from skill_atlas.errors import (
    PARTIAL_EXIT_CODE,
    AtlasError,
    Interrupted,
    RateLimitError,
    TokenError,
    UsageError,
)
from skill_atlas.model import OrgOptions, OrgRepo, OrgScan, OrgScanStatus, ScanOptions
from skill_atlas.progress import NullProgress
from skill_atlas.sanitize import sanitize_line
from skill_atlas.sources.github import GitHubClient, find_token
from skill_atlas.target import GitHubTarget, org_target

DEFAULT_JOBS = 4
MAX_JOBS = 8


@dataclass
class OrgRequest:
    owner: str
    host: str = "github.com"
    include_archived: bool = False
    include_forks: bool = False
    match: list[str] = field(default_factory=list)  # globs on the repository name
    limit: int | None = None
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    force: bool = False
    wait: bool = False  # wait for the primary rate limit to reset instead of stopping
    jobs: int = DEFAULT_JOBS

    def options(self) -> OrgOptions:
        return OrgOptions(
            include_archived=self.include_archived,
            include_forks=self.include_forks,
            match=self.match,
            limit=self.limit,
            include=self.include,
            exclude=self.exclude,
            force=self.force,
        )


@dataclass
class OrgState:
    """What a UI shows while an organization scan runs. Progress gets a copy."""

    owner: str
    phase: str = "listing"  # listing | scanning | done
    listed: int = 0
    total: int = 0  # repositories to scan after the filters
    done: int = 0
    counts: dict[str, int] = field(default_factory=dict)  # by OrgRepo.status
    with_skills: int = 0
    active: list[str] = field(default_factory=list)  # repositories being scanned
    failures: list[tuple[str, str]] = field(default_factory=list)  # (full_name, error)
    wait_until: float | None = None  # epoch seconds: waiting for a rate limit
    api_requests: int = 0
    rate_remaining: int | None = None

    def copy(self) -> OrgState:
        return replace(
            self, counts=dict(self.counts), active=list(self.active), failures=list(self.failures)
        )


class OrgProgress(Protocol):
    def update(self, state: OrgState) -> None:
        """Called on every change, from any thread, never concurrently."""
        ...

    def finished(self, repo: OrgRepo) -> None:
        """One repository is done (any status but pending)."""
        ...


class NullOrgProgress:
    def update(self, state: OrgState) -> None:
        pass

    def finished(self, repo: OrgRepo) -> None:
        pass


@dataclass
class OrgOutcome:
    report: OrgScan
    saved_path: Path | None
    stop_error: AtlasError | None = None

    @property
    def exit_code(self) -> int:
        status = self.report.status
        if status == "complete":
            return 0
        if status == "partial":
            return PARTIAL_EXIT_CODE
        if status == "interrupted":
            return Interrupted.exit_code
        return self.stop_error.exit_code if self.stop_error else 1


def select(
    repos: list[dict[str, Any]], req: OrgRequest
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the filters. Return the repositories to scan and the skipped counts."""
    skipped: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    patterns = [p.lower() for p in req.match]
    for repo in sorted(repos, key=lambda r: str(r.get("full_name", "")).lower()):
        name = str(repo.get("name") or "").lower()
        reason = None
        if repo.get("disabled"):
            reason = "disabled"
        elif repo.get("archived") and not req.include_archived:
            reason = "archived"
        elif repo.get("fork") and not req.include_forks:
            reason = "fork"
        elif patterns and not any(fnmatch.fnmatchcase(name, p) for p in patterns):
            reason = "name"
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        else:
            out.append(repo)
    if req.limit is not None and len(out) > req.limit:
        skipped["limit"] = len(out) - req.limit
        out = out[: req.limit]
    return out, skipped


def _repo_key(host: str, repo: dict[str, Any]) -> str:
    return f"{host}/{repo['full_name']}".lower()


def unchanged_snapshot(
    snapshots: list[store.Loaded], repo: dict[str, Any], options: ScanOptions
) -> store.Loaded | None:
    """A snapshot that is still current without asking GitHub (SPEC section 7.3).

    `pushed_at` changes on every push to any branch, so the same `pushed_at` and default
    branch mean the same default-branch commit.
    """
    pushed = repo.get("pushed_at")
    if not pushed:
        return None
    for item in sorted(snapshots, key=lambda x: x.snapshot.scan.scanned_at, reverse=True):
        snap = item.snapshot
        if (
            snap.source.kind == "github"
            and snap.repo is not None
            and snap.repo.pushed_at == pushed
            and snap.source.resolved_ref == repo.get("default_branch")
            and snap.scan.detectors_version == DETECTORS_VERSION
            and snap.scan.options.cache_key() == options.cache_key()
        ):
            return item
    return None


def _result(repo_key: str, full_name: str, status: str, item: store.Loaded) -> OrgRepo:
    snap = item.snapshot
    return OrgRepo.model_validate(
        {
            "repo_key": repo_key,
            "full_name": full_name,
            "status": status,
            "snapshot": item.path.name,
            "commit_sha": snap.source.commit_sha,
            "skills": snap.stats.skills,
            "agents": snap.stats.agents,
        }
    )


class _Run:
    def __init__(
        self,
        req: OrgRequest,
        store_dir: Path,
        client: GitHubClient,
        progress: OrgProgress,
        cancel: threading.Event,
    ) -> None:
        self.req, self.store_dir, self.client = req, store_dir, client
        self.progress, self.cancel = progress, cancel
        self.state = OrgState(req.owner)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stop_error: AtlasError | None = None
        self.results: dict[str, OrgRepo] = {}
        self.options = ScanOptions(include=req.include, exclude=req.exclude)
        self.futures: dict[Future[OrgRepo], dict[str, Any]] = {}
        self.unfinished: set[Future[OrgRepo]] = set()

    def _update(self, **changes: Any) -> None:
        with self.lock:
            for k, v in changes.items():
                setattr(self.state, k, v)
            rate = self.client.rate_limit
            self.state.rate_remaining = rate.remaining if rate else None
            self.state.api_requests = self.client.api_requests
            self.progress.update(self.state.copy())

    def _on_wait(self, seconds: float) -> None:
        self._update(wait_until=time.time() + seconds)

    def record(self, result: OrgRepo) -> None:
        with self.lock:
            self.results[result.full_name] = result
            s = self.state
            s.done += 1
            s.counts[result.status] = s.counts.get(result.status, 0) + 1
            if (result.skills or 0) + (result.agents or 0) > 0:
                s.with_skills += 1
            if result.status == "failed":
                s.failures.append((result.full_name, result.error or ""))
            if s.wait_until is not None and s.wait_until <= time.time():
                s.wait_until = None
            self.progress.finished(result)
        self._update()

    def scan_one(self, repo: dict[str, Any]) -> OrgRepo:
        full_name = str(repo["full_name"])
        repo_key = _repo_key(self.req.host, repo)
        pending = OrgRepo(repo_key=repo_key, full_name=full_name, status="pending")
        if self.stop.is_set() or self.cancel.is_set():
            return pending
        with self.lock:
            self.state.active.append(full_name)
        try:
            self._update()
            owner, name = full_name.split("/", 1)
            sreq = scan_module.ScanRequest(
                target=full_name,
                host=self.req.host,
                include=self.req.include,
                exclude=self.req.exclude,
                force=self.req.force,
            )
            target = GitHubTarget(self.req.host, owner, name)
            started = time.monotonic()
            prepared = scan_module.prepare_github(
                sreq, target, self.client, NullProgress(), repo=repo
            )
            outcome = scan_module.complete_scan(
                prepared, sreq, self.store_dir, True, NullProgress(), started
            )
            assert outcome.saved_path is not None
            status = "unchanged" if outcome.cache_hit else "scanned"
            return _result(
                repo_key, full_name, status, store.Loaded(outcome.saved_path, outcome.snapshot)
            )
        except Interrupted:
            return pending
        except (RateLimitError, TokenError):
            raise  # the coordinator stops the whole scan
        except AtlasError as exc:
            if repo.get("size") == 0:  # an empty repository has no commit to resolve
                return OrgRepo(repo_key=repo_key, full_name=full_name, status="empty")
            error = sanitize_line(str(exc))
        except Exception as exc:  # one broken repository must not stop the others
            error = f"unexpected error: {exc!r}"
        finally:
            with self.lock:
                self.state.active.remove(full_name)
        return OrgRepo(repo_key=repo_key, full_name=full_name, status="failed", error=error)

    def _halt(self, error: AtlasError | None = None) -> None:
        """Start no more repositories. Running ones finish (their waits end if cancelled)."""
        if error is not None and self.stop_error is None:
            self.stop_error = error
        self.stop.set()
        for fut in self.futures:
            fut.cancel()

    def _collect(self) -> None:
        """Record results until every submitted repository is done."""
        while self.unfinished:
            finished, _ = wait(self.unfinished, timeout=0.25, return_when=FIRST_COMPLETED)
            if self.cancel.is_set():
                self._halt()
            for fut in finished:
                # One at a time: after Ctrl+C here, the next pass still sees the rest.
                self.unfinished.discard(fut)
                if fut.cancelled():
                    continue
                try:
                    result = fut.result()
                except (RateLimitError, TokenError) as exc:
                    self._halt(exc)
                    continue
                except Exception as exc:  # a bug in a progress callback: keep the report
                    repo = self.futures[fut]
                    result = OrgRepo(
                        repo_key=_repo_key(self.req.host, repo),
                        full_name=str(repo["full_name"]),
                        status="failed",
                        error=f"unexpected error: {exc!r}",
                    )
                if result.status != "pending":
                    self.record(result)

    def _list(self) -> tuple[str, bool, list[dict[str, Any]]]:
        account = self.client.account(self.req.owner)
        login = str(account.get("login") or self.req.owner)
        is_org = account.get("type") == "Organization"
        kind = "organization" if is_org else "user"
        if not is_org and (self.client.viewer() or "").lower() == login.lower():
            kind = "viewer"
        listed: list[dict[str, Any]] = []
        for page in self.client.list_repos(login, kind):
            listed.extend(r for r in page if isinstance(r, dict) and r.get("full_name"))
            self._update(listed=len(listed))
            if self.cancel.is_set():
                raise Interrupted("interrupted")
        return login, is_org, listed

    def run(self) -> OrgScan:
        started_at, started = scan_module._now(), time.monotonic()
        requests_before = self.client.api_requests
        self.client.wait_secondary = True
        self.client.wait_primary = self.req.wait
        self.client.on_wait = self._on_wait
        self.client.interrupted = self.cancel
        self._update()

        try:
            login, is_org, listed = self._list()
        except (Interrupted, KeyboardInterrupt):
            raise Interrupted("organization scan interrupted during the listing") from None
        selected, skipped = select(listed, self.req)
        self._update(total=len(selected), phase="scanning")

        db = aggregate.Store.open(self.store_dir)
        by_key: dict[str, list[store.Loaded]] = {}
        for item in db.snapshots:
            by_key.setdefault(item.snapshot.source.repo_key, []).append(item)
        to_scan = []
        for repo in selected:
            key = _repo_key(self.req.host, repo)
            hit = None
            if not self.req.force:
                hit = unchanged_snapshot(by_key.get(key, []), repo, self.options)
            if hit is not None:
                self.record(_result(key, str(repo["full_name"]), "unchanged", hit))
            else:
                to_scan.append(repo)

        jobs = max(1, min(self.req.jobs, MAX_JOBS))
        executor = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="org-scan")
        self.futures = {executor.submit(self.scan_one, repo): repo for repo in to_scan}
        self.unfinished = set(self.futures)
        force_exit = False
        try:
            self._collect()
        except KeyboardInterrupt:
            # Ctrl+C: start nothing new, cut waits short, record the running repositories.
            self.cancel.set()
            self._halt()
            try:
                self._collect()
            except KeyboardInterrupt:
                force_exit = True  # a second Ctrl+C: leave them behind
        finally:
            executor.shutdown(wait=not force_exit, cancel_futures=True)
        interrupted = self.cancel.is_set() and bool(to_scan)

        repos = []
        for repo in selected:
            full_name = str(repo["full_name"])
            repos.append(
                self.results.get(full_name)
                or OrgRepo(
                    repo_key=_repo_key(self.req.host, repo), full_name=full_name, status="pending"
                )
            )
        left = sum(1 for r in repos if r.status == "pending")
        status: OrgScanStatus
        if interrupted and left:
            status = "interrupted"
        elif self.stop_error is not None:
            status = "stopped"
        elif any(r.status == "failed" for r in repos):
            status = "partial"
        else:
            status = "complete"
        message = None
        if status in ("interrupted", "stopped"):
            reason = "interrupted" if interrupted else sanitize_line(str(self.stop_error))
            message = (
                f"{reason}; {left} of {len(repos)} repositories not scanned. "
                "Run the same scan again to continue: saved snapshots are reused"
            )
        report = OrgScan(
            id=scan_module.new_scan_id(),
            started_at=started_at,
            finished_at=scan_module._now(),
            duration_ms=int((time.monotonic() - started) * 1000),
            tool_version=__version__,
            host=self.req.host,
            owner=login,
            owner_type="organization" if is_org else "user",
            owner_key=f"{self.req.host}/{login}".lower(),
            status=status,
            message=message,
            options=self.req.options(),
            listed=len(listed),
            skipped=skipped,
            api_requests=self.client.api_requests - requests_before,
            repos=repos,
        )
        self._update(phase="done", active=[])
        return report


def run_org_scan(
    req: OrgRequest,
    *,
    store_dir: Path,
    client: GitHubClient | None = None,
    progress: OrgProgress | None = None,
    cancel: threading.Event | None = None,
) -> OrgOutcome:
    """Scan every selected repository and save the report next to the snapshots.

    Errors before the first repository (no such account, no access, a rate limit or a
    cancel during the listing) raise. Afterwards a failed repository is recorded and the
    scan goes on; a rate limit or a rejected token stops it with status `stopped`, a
    cancel (the `cancel` event, Ctrl+C) with status `interrupted`.
    """
    target = org_target(req.owner, req.host)
    req = replace(req, owner=target.owner, host=target.host)
    if req.limit is not None and req.limit < 1:
        raise UsageError("--limit must be at least 1")
    if client is None:
        client = GitHubClient(req.host, find_token(req.host))
    run = _Run(req, store_dir, client, progress or NullOrgProgress(), cancel or threading.Event())
    report = run.run()
    saved = store.save_org(report, store.orgs_dir(store_dir))
    return OrgOutcome(report, saved, run.stop_error)

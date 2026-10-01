"""GitHub REST client and tree source (SPEC section 4.1)."""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from skill_atlas import __version__
from skill_atlas.errors import (
    AccessError,
    AtlasError,
    Interrupted,
    NetworkError,
    RateLimitError,
    TokenError,
)
from skill_atlas.progress import Progress
from skill_atlas.sources.base import EntryKind, TreeEntry
from skill_atlas.sources.git import git, ls_tree
from skill_atlas.sources.indexed import IndexedTreeSource

RETRIES = 3
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Waits on rate limits, when enabled (organization scans, SPEC section 3.4).
RATE_LIMIT_WAITS = 3  # per request
MAX_SECONDARY_WAIT = 300.0
MAX_PRIMARY_WAIT = 3660.0  # the primary limit resets within an hour
SECONDARY_DEFAULT_WAIT = 60.0  # GitHub: wait at least a minute when no retry-after is given
PAGE_SIZE = 100


def find_token(host: str) -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var)
        if value:
            return value
    if shutil.which("gh") is None:
        return None
    try:
        proc = subprocess.run(
            ["gh", "auth", "token", "--hostname", host],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = proc.stdout.decode().strip()
    return token if proc.returncode == 0 and token else None


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class RateLimit:
    """The REST API quota from the latest response headers."""

    limit: int
    remaining: int
    reset_at: float  # epoch seconds


class GitHubClient:
    """REST client. Thread-safe: an organization scan shares one client between workers.

    By default a rate limit raises RateLimitError at once (SPEC section 4.1). An
    organization scan sets `wait_secondary` and, with `--wait`, `wait_primary`.
    """

    def __init__(
        self,
        host: str,
        token: str | None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.host = host
        self.token = token
        self.api = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        self._http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        # Set to stop: waits end at once and raise Interrupted. An organization scan puts
        # its cancel event here, so Stop also cuts a rate-limit wait short.
        self.interrupted = threading.Event()
        self._sleep = sleep  # None: wait on `interrupted`
        self._clock = clock
        self.wait_primary = False
        self.wait_secondary = False
        # Called before each rate-limit wait with the number of seconds to wait.
        self.on_wait: Callable[[float], None] | None = None
        self.rate_limit: RateLimit | None = None
        self.api_requests = 0  # requests to the REST API; raw file downloads not counted
        self._stats_lock = threading.Lock()

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": f"skill-atlas/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(
        self,
        url: str,
        *,
        accept: str = "application/vnd.github+json",
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_error = ""
        failures = waits = 0
        while failures <= RETRIES:
            if failures:
                self._wait(0.5 * 2 ** (failures - 1))
            try:
                resp = self._http.get(url, headers=self._headers(accept), params=params)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                failures += 1
                continue
            self._track(url, resp)
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                failures += 1
                continue
            try:
                self._raise_for_status(resp)
            except RateLimitError as exc:
                delay = self._rate_limit_delay(exc)
                if delay is None or waits >= RATE_LIMIT_WAITS:
                    raise
                waits += 1
                if self.on_wait is not None:
                    self.on_wait(delay)
                self._wait(delay)
                continue
            return resp
        raise NetworkError(
            f"request to {self.host} failed after {RETRIES + 1} attempts: {last_error}"
        )

    def _wait(self, seconds: float) -> None:
        if not self.interrupted.is_set():
            if self._sleep is None:
                self.interrupted.wait(seconds)
            else:
                self._sleep(seconds)
        if self.interrupted.is_set():
            raise Interrupted("interrupted while waiting for GitHub")

    def _track(self, url: str, resp: httpx.Response) -> None:
        if not url.startswith(self.api):
            return
        headers = resp.headers
        with self._stats_lock:
            self.api_requests += 1
            with contextlib.suppress(KeyError, ValueError):  # no quota headers: keep the last
                self.rate_limit = RateLimit(
                    int(headers["x-ratelimit-limit"]),
                    int(headers["x-ratelimit-remaining"]),
                    float(headers["x-ratelimit-reset"]),
                )

    def _rate_limit_delay(self, exc: RateLimitError) -> float | None:
        """Seconds to wait before a retry, or None to give up."""
        if exc.retry_after is not None:
            if self.wait_secondary and exc.retry_after <= MAX_SECONDARY_WAIT:
                return exc.retry_after
            return None
        if exc.reset_at is not None and self.wait_primary:
            delay = max(exc.reset_at - self._clock(), 0.0) + 1.0
            return delay if delay <= MAX_PRIMARY_WAIT else None
        return None

    def _raise_for_status(self, resp: httpx.Response) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code in (403, 429):
            self._raise_for_rate_limit(resp)
        if code == 401:
            raise TokenError("GitHub rejected the token (HTTP 401)")
        if code == 403:
            raise AccessError(f"GitHub denied access (HTTP 403): {_message(resp)}")
        if code in (404, 409, 422):
            raise NotFound(_message(resp))
        raise AtlasError(f"unexpected GitHub response HTTP {code}: {_message(resp)}")

    def _raise_for_rate_limit(self, resp: httpx.Response) -> None:
        headers = resp.headers
        hint = "" if self.token else "; set GITHUB_TOKEN to raise the limit"
        if headers.get("x-ratelimit-remaining") == "0":
            reset = headers.get("x-ratelimit-reset")
            reset_at = float(reset) if reset and reset.isdigit() else None
            when = ""
            if reset_at is not None:
                when = dt.datetime.fromtimestamp(reset_at, dt.UTC).strftime(
                    " (resets at %Y-%m-%d %H:%M:%S UTC)"
                )
            raise RateLimitError(f"GitHub API rate limit exceeded{when}{hint}", reset_at=reset_at)
        # Secondary limits: too many requests at once or too fast, whatever the quota.
        retry = headers.get("retry-after", "")
        if resp.status_code == 429 or retry.isdigit() or "rate limit" in _message(resp).lower():
            seconds = float(retry) if retry.isdigit() else SECONDARY_DEFAULT_WAIT
            raise RateLimitError(
                f"GitHub secondary rate limit exceeded; retry after {seconds:.0f} s{hint}",
                retry_after=seconds,
            )

    def account(self, login: str) -> dict[str, Any]:
        """`GET /users/{login}`: works for organizations as well, `type` tells them apart."""
        try:
            data: dict[str, Any] = self._get(f"{self.api}/users/{quote(login, safe='')}").json()
        except NotFound:
            raise AccessError(f"GitHub account {login!r} not found") from None
        return data

    def viewer(self) -> str | None:
        """Login of the token owner, or None without a token."""
        if not self.token:
            return None
        try:
            return str(self._get(f"{self.api}/user").json().get("login") or "") or None
        except (NotFound, AccessError):
            return None  # an app token has no user

    def list_repos(self, login: str, kind: str) -> Iterator[list[dict[str, Any]]]:
        """Every repository of an account, one page at a time.

        `kind` is `organization`, `user`, or `viewer` (the token owner: private
        repositories too; `/users/{login}/repos` lists public ones only).
        """
        name = quote(login, safe="")
        if kind == "organization":
            url, params = f"{self.api}/orgs/{name}/repos", {"type": "all"}
        elif kind == "viewer":
            url, params = f"{self.api}/user/repos", {"affiliation": "owner", "visibility": "all"}
        else:
            url, params = f"{self.api}/users/{name}/repos", {"type": "owner"}
        query: dict[str, str] | None = {**params, "per_page": str(PAGE_SIZE), "sort": "full_name"}
        next_url: str | None = url
        while next_url:
            try:
                resp = self._get(next_url, params=query)
            except NotFound:
                raise AccessError(f"cannot list repositories of {login!r}") from None
            page = resp.json()
            if not isinstance(page, list):
                raise AtlasError(f"unexpected repository listing for {login!r}")
            yield page
            # The next page URL carries the query. Never follow it off the API host:
            # the request would send the token there.
            next_url = resp.links.get("next", {}).get("url")
            if next_url and not next_url.startswith(self.api + "/"):
                raise AtlasError(f"unexpected pagination URL {next_url!r}")
            query = None

    def repo(self, owner: str, name: str) -> dict[str, Any]:
        try:
            data: dict[str, Any] = self._get(f"{self.api}/repos/{owner}/{name}").json()
        except NotFound:
            hint = "" if self.token else "; set GITHUB_TOKEN for private repositories"
            raise AccessError(f"repository {owner}/{name} not found or no access{hint}") from None
        return data

    def commit(self, owner: str, name: str, ref: str) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = self._get(
                f"{self.api}/repos/{owner}/{name}/commits/{quote(ref, safe='')}"
            ).json()
        except NotFound:
            return None
        return data

    def tree(self, owner: str, name: str, sha: str) -> dict[str, Any]:
        data: dict[str, Any] = self._get(
            f"{self.api}/repos/{owner}/{name}/git/trees/{sha}", params={"recursive": "1"}
        ).json()
        return data

    def raw_file(self, owner: str, name: str, sha: str, path: str) -> bytes:
        quoted = quote(path)
        if self.host == "github.com":
            url = f"https://raw.githubusercontent.com/{owner}/{name}/{sha}/{quoted}"
            return self._get(url, accept="*/*").content
        url = f"{self.api}/repos/{owner}/{name}/contents/{quoted}"
        return self._get(url, accept="application/vnd.github.raw", params={"ref": sha}).content

    def blob(self, owner: str, name: str, blob_sha: str) -> bytes:
        data = self._get(f"{self.api}/repos/{owner}/{name}/git/blobs/{blob_sha}").json()
        return base64.b64decode(data.get("content", ""))


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", "")) or resp.reason_phrase
    except ValueError:
        return resp.reason_phrase


def tree_entries(tree: dict[str, Any]) -> list[TreeEntry]:
    entries: list[TreeEntry] = []
    for item in tree.get("tree", []):
        kind: EntryKind
        if item.get("type") == "commit":
            kind = "submodule"
        elif item.get("type") != "blob":
            continue
        elif item.get("mode") == "120000":
            kind = "symlink"
        else:
            kind = "file"
        entries.append(
            TreeEntry(item["path"], kind, size=item.get("size"), blob_sha=item.get("sha"))
        )
    return entries


class GitHubTreeSource(IndexedTreeSource):
    """Tree listing from the Trees API (or a clone); file contents over HTTP."""

    def __init__(
        self, client: GitHubClient, owner: str, name: str, sha: str, listing: list[TreeEntry]
    ) -> None:
        self._client = client
        self._owner, self._name, self._sha = owner, name, sha
        super().__init__(listing, self._read)

    def _read(self, entry: TreeEntry) -> bytes:
        # A failed single-file read becomes OSError: the scanner records it as a
        # per-skill problem. Rate limits and access errors still abort the scan.
        try:
            if entry.kind == "symlink" and entry.blob_sha:
                return self._client.blob(self._owner, self._name, entry.blob_sha)
            return self._client.raw_file(self._owner, self._name, self._sha, entry.path)
        except NotFound as exc:
            raise OSError(f"not found: {entry.path}: {exc}") from None
        except NetworkError as exc:
            if isinstance(exc, RateLimitError):
                raise
            raise OSError(str(exc)) from None


def _git_env(host: str, token: str | None) -> dict[str, str]:
    config = [
        # Abort a stalled transfer instead of hanging: below 1 KB/s for 60 s.
        ("http.lowSpeedLimit", "1000"),
        ("http.lowSpeedTime", "60"),
    ]
    if token:
        # Pass the token through env-based config so it never shows up in argv.
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        config.append((f"http.https://{host}/.extraheader", f"Authorization: Basic {basic}"))
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT=str(len(config)))
    for i, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def _run_git_with_progress(
    cwd: Path, args: list[str], env: dict[str, str], progress: Progress
) -> None:
    """Run git, forwarding its `--progress` lines to `progress.detail`."""
    proc = subprocess.Popen(
        ["git", *args], cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    assert proc.stderr is not None
    tail: list[str] = []
    buffer = b""
    while chunk := os.read(proc.stderr.fileno(), 4096):
        buffer += chunk
        *parts, buffer = re.split(rb"[\r\n]", buffer)
        for raw in parts:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                progress.detail(line)
                tail = [*tail[-4:], line]
    if proc.wait() != 0:
        message = " | ".join(tail) or f"exit code {proc.returncode}"
        raise NetworkError(f"git {args[0]} failed: {message}")


def clone_listing(
    host: str, owner: str, name: str, sha: str, token: str | None, progress: Progress
) -> list[TreeEntry]:
    """Full tree listing for repositories where the Trees API truncates.

    Fetches only commit and tree objects (no blobs) of one commit, lists them, and
    deletes the clone. File contents are read over HTTP like in API mode.
    """
    tmp = Path(tempfile.mkdtemp(prefix="skill-atlas-"))
    try:
        env = _git_env(host, token)
        git(tmp, "init", "-q", check=True)
        url = f"https://{host}/{owner}/{name}.git"
        _run_git_with_progress(
            tmp,
            ["fetch", "--progress", "--depth", "1", "--filter=blob:none", url, sha],
            env,
            progress,
        )
        progress.stage("listing cloned tree")
        # Fail fast instead of fetching missing objects one by one from the remote.
        return ls_tree(tmp, sha, with_sizes=False, env=dict(env, GIT_NO_LAZY_FETCH="1"))
    except AtlasError as exc:
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError(f"git clone fallback failed: {exc}") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

"""GitHub REST client and tree source (SPEC section 4.1)."""

from __future__ import annotations

import base64
import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from skill_atlas import __version__
from skill_atlas.errors import AccessError, AtlasError, NetworkError, RateLimitError
from skill_atlas.progress import Progress
from skill_atlas.sources.base import EntryKind, TreeEntry
from skill_atlas.sources.git import git, ls_tree
from skill_atlas.sources.indexed import IndexedTreeSource

RETRIES = 3
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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


class GitHubClient:
    def __init__(
        self,
        host: str,
        token: str | None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.host = host
        self.token = token
        self.api = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        self._http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        self._sleep = sleep

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
        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            try:
                resp = self._http.get(url, headers=self._headers(accept), params=params)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                continue
            self._raise_for_status(resp)
            return resp
        raise NetworkError(
            f"request to {self.host} failed after {RETRIES + 1} attempts: {last_error}"
        )

    def _raise_for_status(self, resp: httpx.Response) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code == 429 or (code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
            reset = resp.headers.get("x-ratelimit-reset")
            when = ""
            if reset and reset.isdigit():
                when = dt.datetime.fromtimestamp(int(reset), dt.UTC).strftime(
                    " (resets at %Y-%m-%d %H:%M:%S UTC)"
                )
            hint = "" if self.token else "; set GITHUB_TOKEN to raise the limit"
            raise RateLimitError(f"GitHub API rate limit exceeded{when}{hint}")
        if code == 401:
            raise AccessError("GitHub rejected the token (HTTP 401)")
        if code == 403:
            raise AccessError(f"GitHub denied access (HTTP 403): {_message(resp)}")
        if code in (404, 409, 422):
            raise NotFound(_message(resp))
        raise AtlasError(f"unexpected GitHub response HTTP {code}: {_message(resp)}")

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

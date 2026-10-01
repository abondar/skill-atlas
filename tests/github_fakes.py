"""A fake GitHub account with many repositories, for organization scan tests (respx)."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import respx

from skill_atlas.sources.base import git_blob_sha
from skill_atlas.sources.github import GitHubClient

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
RESET = 1_800_000_000


def skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: Does {name}.\n---\nSteps for {name}.\n"


@dataclass
class FakeRepo:
    owner: str
    name: str
    files: dict[str, str]
    archived: bool = False
    fork: bool = False
    version: int = 1
    pushed: int = 1
    empty: bool = False

    @property
    def sha(self) -> str:
        return hashlib.sha1(f"{self.name}:{self.version}".encode()).hexdigest()

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "full_name": f"{self.owner}/{self.name}",
            "default_branch": "main",
            "node_id": f"R_{self.name}",
            "description": f"The {self.name} repository",
            "license": None,
            "topics": [],
            "stargazers_count": 1,
            "forks_count": 0,
            "visibility": "public",
            "archived": self.archived,
            "fork": self.fork,
            "pushed_at": f"2026-01-01T00:00:{self.pushed:02d}Z",
            "size": 0 if self.empty else 10,
        }


@dataclass
class FakeOrg:
    """GitHub with one account. Every API response carries the quota headers; past the
    quota the API answers 403 like GitHub does."""

    login: str = "Acme"
    kind: str = "Organization"
    viewer: str | None = None
    quota: int = 5000
    used: int = 0
    repos: dict[str, FakeRepo] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    broken: set[str] = field(default_factory=set)  # repos whose tree answers 500
    secondary: int = 0  # answer this many API requests with a secondary rate limit
    delay: float = 0.0  # seconds each tree request takes
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, name: str, files: dict[str, str] | None = None, **kwargs: Any) -> FakeRepo:
        repo = FakeRepo(self.login, name, files or {"README.md": "# hi\n"}, **kwargs)
        self.repos[name.lower()] = repo
        return repo

    def push(self, name: str, files: dict[str, str]) -> None:
        repo = self.repos[name.lower()]
        repo.files = files
        repo.version += 1
        repo.pushed += 1

    def install(self, router: respx.MockRouter) -> None:
        router.route(host="api.github.com").mock(side_effect=self._api)
        router.route(host="raw.githubusercontent.com").mock(side_effect=self._raw)

    def _api(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.calls.append(request.url.path)
            if self.secondary:
                self.secondary -= 1
                return httpx.Response(
                    403, json={"message": "secondary rate limit"}, headers={"retry-after": "30"}
                )
            if self.used >= self.quota:
                return httpx.Response(
                    403,
                    json={"message": "API rate limit exceeded"},
                    headers=self._quota_headers(),
                )
            self.used += 1
            headers = self._quota_headers()
        if self.delay and "/git/trees/" in request.url.path:
            time.sleep(self.delay)
        status, body, extra = self._route(request)
        return httpx.Response(status, json=body, headers={**headers, **extra})

    def _quota_headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit": str(self.quota),
            "x-ratelimit-remaining": str(max(self.quota - self.used, 0)),
            "x-ratelimit-reset": str(RESET),
        }

    def _route(self, request: httpx.Request) -> tuple[int, Any, dict[str, str]]:
        path = unquote(request.url.path)
        login = self.login.lower()
        if path.lower() == f"/users/{login}":
            return 200, {"login": self.login, "type": self.kind}, {}
        if path == "/user":
            if self.viewer is None:
                return 401, {"message": "Requires authentication"}, {}
            return 200, {"login": self.viewer}, {}
        listing = {
            f"/orgs/{login}/repos": self.kind == "Organization",
            f"/users/{login}/repos": self.kind == "User",
            "/user/repos": self.viewer is not None,
        }
        if listing.get(path.lower()):
            return self._page(request)
        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)/(commits|git/trees)/(.+)", path)
        if m and m[1].lower() == login and m[2].lower() in self.repos:
            repo = self.repos[m[2].lower()]
            if m[3] == "commits":
                if repo.empty:
                    return 409, {"message": "Git Repository is empty."}, {}
                if m[4] not in ("main", repo.sha):
                    return 422, {"message": "No commit found"}, {}
                return 200, {"sha": repo.sha, "commit": {"committer": {"date": "2026"}}}, {}
            if repo.name in self.broken:
                return 500, {"message": "boom"}, {}
            tree = [
                {
                    "path": p,
                    "mode": "100644",
                    "type": "blob",
                    "sha": git_blob_sha(c.encode()),
                    "size": len(c.encode()),
                }
                for p, c in sorted(repo.files.items())
            ]
            return 200, {"sha": repo.sha, "tree": tree, "truncated": False}, {}
        return 404, {"message": "Not Found"}, {}

    def _page(self, request: httpx.Request) -> tuple[int, Any, dict[str, str]]:
        query = parse_qs(urlsplit(str(request.url)).query)
        per_page = int(query.get("per_page", ["30"])[0])
        page = int(query.get("page", ["1"])[0])
        repos = sorted(self.repos.values(), key=lambda r: r.name.lower())
        chunk = repos[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(repos):
            nxt = request.url.copy_merge_params({"page": str(page + 1)})
            headers["link"] = f'<{nxt}>; rel="next"'
        return 200, [r.json() for r in chunk], headers

    def _raw(self, request: httpx.Request) -> httpx.Response:
        _, _owner, name, sha, *rest = unquote(request.url.path).split("/")
        repo = self.repos.get(name.lower())
        path = "/".join(rest)
        if repo is None or sha != repo.sha or path not in repo.files:
            return httpx.Response(404, text="404: Not Found")
        return httpx.Response(200, content=repo.files[path].encode())

    @property
    def api_calls(self) -> int:
        return len(self.calls)


class Sleeps:
    def __init__(self, org: FakeOrg | None = None) -> None:
        self.calls: list[float] = []
        self.org = org

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.org is not None:  # time passes: the quota resets
            self.org.used = 0


def client(
    token: str | None = "t", sleep: Sleeps | None = None, clock: float = RESET - 600
) -> GitHubClient:
    return GitHubClient("github.com", token, sleep=sleep or Sleeps(), clock=lambda: clock)


def populate(org: FakeOrg, n: int, with_skills: int) -> None:
    for i in range(n):
        name = f"repo-{i:03d}"
        files = {"README.md": f"# {name}\n", "src/main.py": "print(1)\n"}
        if i < with_skills:
            files[f".claude/skills/s{i}/SKILL.md"] = skill(f"s{i}")
        org.add(name, files)

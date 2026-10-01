"""A skill-atlas web server over a fixture store, for the Playwright tests.

Usage: python e2e/server.py <fixture> <root>

Builds fixture repositories under <root> (git repositories with fixed dates, so commit
SHAs are stable), optionally saves their scans, starts the server on a free port and
prints one JSON line: {"url", "repos": {name: path}}. Runs until killed.

Screenshots must not change between runs, so this process pins what a scan records:
`scanned_at` comes from a fake clock, scan IDs from a counter, durations from a fake
monotonic clock. The browser pins its own clock to FIXED_BROWSER_TIME (fixtures.ts).

GitHub is faked in process (FakeGitHub): the organization `acme` and its repositories
answer from memory, so an organization scan from the UI needs no network.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path
from urllib.parse import unquote

import httpx

from skill_atlas import org, scan
from skill_atlas.sources.base import git_blob_sha
from skill_atlas.sources.github import GitHubClient
from skill_atlas.web import Server, WebApp

TOKEN = "e2e"
CLOCK_START = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.UTC)


def pin_scan_clock() -> None:
    minutes = itertools.count()
    ids = itertools.count(1)
    ticks = itertools.count()
    scan._now = lambda: (
        (CLOCK_START + dt.timedelta(minutes=next(minutes))).isoformat(timespec="milliseconds")
    ).replace("+00:00", "Z")
    scan.new_scan_id = lambda: f"01E2E{next(ids):021d}"
    # Only scan.py sees this: each call advances 0.4 s, so durations are fixed.
    scan.time = types.SimpleNamespace(  # type: ignore[assignment,attr-defined]
        monotonic=lambda: next(ticks) * 0.4, time=lambda: CLOCK_START.timestamp()
    )
    org.time = types.SimpleNamespace(  # type: ignore[assignment,attr-defined]
        monotonic=lambda: next(ticks) * 0.4, time=lambda: CLOCK_START.timestamp()
    )


def skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def git_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="e2e",
        GIT_AUTHOR_EMAIL="e2e@example.com",
        GIT_COMMITTER_NAME="e2e",
        GIT_COMMITTER_EMAIL="e2e@example.com",
        GIT_AUTHOR_DATE="2026-01-01T09:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T09:00:00Z",
    )
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root


RELEASE = """# Prepare a release

1. Update `CHANGELOG.md` with every merged pull request since the last tag.
2. Bump the version in `pyproject.toml` and in `src/acme/__init__.py`.
3. Run the full test suite and the type checker; stop on any failure.
4. Tag the commit as `vX.Y.Z` and push the tag to trigger the release workflow.
"""
REVIEW = """# Review a pull request

Read the diff, check that every changed function has a test, look for unhandled errors
and leave one comment per finding with the file and the line number. Approve only when
the author has answered every comment.
"""
DOCS = """# Write API docs

Document every public function with a one-line summary, its arguments and the value it
returns. Put examples in fenced code blocks and run them with `pytest --doctest-glob`.
"""


def journey(root: Path) -> dict[str, Path]:
    """Two repositories, not scanned yet: the test scans them from the UI."""
    acme = git_repo(
        root / "acme-app",
        {
            ".claude/skills/release/SKILL.md": skill(
                "release", "Prepares and tags a release.", RELEASE
            ),
            # The same skill for two agents: grouped as one card with two locations.
            ".claude/skills/code-review/SKILL.md": skill(
                "code-review", "Reviews a pull request.", REVIEW
            ),
            ".agents/skills/code-review/SKILL.md": skill(
                "code-review", "Reviews a pull request.", REVIEW
            ),
            # A partial duplicate of release: found under Similar.
            ".claude/skills/hotfix/SKILL.md": skill(
                "hotfix", "Ships a hotfix release.", RELEASE + "5. Merge the tag back.\n"
            ),
            ".claude/skills/api-docs/SKILL.md": skill("api-docs", "Writes API docs.", DOCS),
            "tests/fixtures/skills/fake/SKILL.md": skill("fake", "A test fixture.", "Fake.\n"),
            ".claude/agents/triage.md": (
                "---\nname: triage\ndescription: Sorts new issues.\n---\nLabel new issues.\n"
            ),
        },
    )
    fork = git_repo(
        root / "acme-fork",
        # Copied from acme-app under another name, then edited.
        {
            ".claude/skills/ship/SKILL.md": skill(
                "ship", "Ships a release.", RELEASE + "5. Announce it in the team chat.\n"
            )
        },
    )
    return {"acme-app": acme, "acme-fork": fork}


def demo(root: Path) -> dict[str, Path]:
    """Scanned koog-like and MPS-like repositories plus one to scan from the UI."""
    java = (
        "Find the Kotlin snippet in the docs, write the same example in Java next to it, "
        "compile both with the docs Gradle task and link them from the page header.\n"
    ) * 3
    weather = "Get the weather.\n\n1. Extract the location.\n2. Call `scripts/weather.py`.\n"
    koog = git_repo(
        root / "koog",
        {
            ".claude/skills/add-java/SKILL.md": skill("add-java", "Adds Java snippets.", java),
            ".claude/skills/split/SKILL.md": skill(
                "split", "Splits JVM and non-JVM code.", java + "Then split the modules.\n"
            ),
            "integration-tests/src/jvmTest/resources/skills/weather-retrieval/SKILL.md": skill(
                "weather-retrieval", "Retrieves the weather.", weather
            ),
            "integration-tests/src/jvmTest/resources/skills/arithmetic/SKILL.md": skill(
                "arithmetic", "Evaluates arithmetic.", "Body.\n"
            ),
        },
    )
    copied = skill("mps-actions", "Guidelines for MPS actions.", "Body.\n")
    mps = git_repo(
        root / "mps",
        {
            ".agents/skills/mps-actions/SKILL.md": copied,
            ".claude/skills/mps-actions/SKILL.md": copied,
            "plugins/mcp/resources/skills/mps-actions/SKILL.md": copied + "Also menus.\n",
            ".agents/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", "Body.\n"),
        },
    )
    fresh = git_repo(
        root / "fresh",
        {".claude/skills/hello/SKILL.md": skill("hello", "Says hello.", java)},
    )
    return {"koog": koog, "mps": mps, "fresh": fresh}


FIXTURES = {"journey": (journey, ()), "demo": (demo, ("koog", "mps"))}


# Repositories of the fake organization `acme`. None: the tree request fails with HTTP 500.
ACME: dict[str, dict[str, str] | None] = {
    "agents-kit": {
        ".claude/skills/triage/SKILL.md": skill("triage", "Sorts new issues.", "Label them.\n"),
        ".claude/agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews.\n---\nRead.\n",
    },
    "platform": {
        ".agents/skills/deploy/SKILL.md": skill("deploy", "Deploys a service.", "Ship it.\n"),
    },
    "docs-site": {"README.md": "# Docs\n"},
    "website": {"index.html": "<p>hi</p>\n"},
    "flaky-service": None,
}
ACME_ARCHIVED = ("legacy-tools",)


class FakeGitHub:
    """Just enough of the GitHub REST API and raw downloads for an organization scan."""

    def __init__(self) -> None:
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))

    def client(self, host: str) -> GitHubClient:
        return GitHubClient(host, None, http=self.http, sleep=lambda _: None)

    @staticmethod
    def sha(name: str) -> str:
        return hashlib.sha1(name.encode()).hexdigest()

    def listing(self) -> list[dict[str, object]]:
        names = sorted([*ACME, *ACME_ARCHIVED])
        return [
            {
                "name": n,
                "full_name": f"acme/{n}",
                "default_branch": "main",
                "node_id": f"R_{n}",
                "description": f"Acme {n.replace('-', ' ')}",
                "stargazers_count": 3,
                "forks_count": 0,
                "visibility": "public",
                "archived": n in ACME_ARCHIVED,
                "fork": False,
                "pushed_at": "2026-01-01T08:00:00Z",
                "size": 10,
            }
            for n in names
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if request.url.host == "raw.githubusercontent.com":
            _, _owner, name, _sha, *rest = path.split("/")
            content = (ACME.get(name) or {}).get("/".join(rest))
            if content is None:
                return httpx.Response(404, text="404: Not Found")
            return httpx.Response(200, content=content.encode())
        segs = path.strip("/").split("/")
        if segs == ["users", "acme"]:
            return httpx.Response(200, json={"login": "acme", "type": "Organization"})
        if segs == ["orgs", "acme", "repos"]:
            return httpx.Response(200, json=self.listing())
        if len(segs) >= 5 and segs[:2] == ["repos", "acme"] and segs[2] in ACME:
            name, tree_files = segs[2], ACME[segs[2]]
            if segs[3] == "commits":
                commit = {"committer": {"date": "2026-01-01T08:00:00Z"}}
                return httpx.Response(200, json={"sha": self.sha(name), "commit": commit})
            if segs[3:5] == ["git", "trees"]:
                if tree_files is None:
                    return httpx.Response(500, json={"message": "Server Error"})
                tree = [
                    {
                        "path": rel,
                        "mode": "100644",
                        "type": "blob",
                        "sha": git_blob_sha(text.encode()),
                        "size": len(text.encode()),
                    }
                    for rel, text in sorted(tree_files.items())
                ]
                return httpx.Response(200, json={"sha": self.sha(name), "tree": tree})
        return httpx.Response(404, json={"message": "Not Found"})


def main() -> None:
    fixture, root = sys.argv[1], Path(sys.argv[2])
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    os.environ["SKILL_ATLAS_HOME"] = str(root / "home")
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        os.environ.pop(var, None)
    pin_scan_clock()
    build, presaved = FIXTURES[fixture]
    repos = build(root / "repos")
    scans = root / "scans"
    for name in presaved:
        outcome = scan.run_scan(scan.ScanRequest(target=str(repos[name])), store_dir=scans)
        assert outcome.saved_path is not None
    github = FakeGitHub()
    server = Server(("127.0.0.1", 0), WebApp(scans, token=TOKEN, github=github.client))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(json.dumps({"url": url, "token": TOKEN, "repos": {k: str(v) for k, v in repos.items()}}))
    sys.stdout.flush()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

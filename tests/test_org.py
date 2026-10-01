"""Organization scans against a fake GitHub API (respx): listing, filters, rate limits,
partial failures and resuming."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from skill_atlas import store
from skill_atlas.errors import AccessError, RateLimitError, UsageError
from skill_atlas.org import OrgRequest, OrgState, run_org_scan, select
from skill_atlas.scan import ScanRequest, run_scan
from skill_atlas.sources.github import GitHubClient
from skill_atlas.target import GitHubTarget, OrgTarget, parse_target
from tests.github_fakes import API, RESET, FakeOrg, Sleeps, client, populate, skill


@pytest.fixture
def org() -> Iterator[FakeOrg]:
    fake = FakeOrg()
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def scan_org(store_dir: Path, gh: GitHubClient | None = None, **kwargs: Any) -> Any:
    kwargs.setdefault("jobs", 1)
    return run_org_scan(
        OrgRequest(owner="acme", **kwargs), store_dir=store_dir, client=gh or client()
    )


def snapshot_files(store_dir: Path) -> list[Path]:
    return sorted(store_dir.glob("*.json"))


# --- targets --------------------------------------------------------------------------


def test_account_urls_are_org_targets() -> None:
    assert parse_target("https://github.com/Acme") == OrgTarget("github.com", "Acme")
    assert parse_target("https://github.com/orgs/acme/repositories") == OrgTarget(
        "github.com", "acme"
    )
    assert parse_target("https://ghe.example.com/team/") == OrgTarget("ghe.example.com", "team")
    assert isinstance(parse_target("https://github.com/acme/app"), GitHubTarget)
    with pytest.raises(UsageError, match="--org acme"):
        run_scan(ScanRequest(target="https://github.com/acme"), store_dir=None)
    with pytest.raises(UsageError, match="not a GitHub organization"):
        run_org_scan(OrgRequest(owner="a/b"), store_dir=Path("unused"))


def test_select_filters() -> None:
    repos = [
        {"name": n, "full_name": f"o/{n}", **extra}
        for n, extra in [
            ("app", {}),
            ("old", {"archived": True}),
            ("copy", {"fork": True}),
            ("gone", {"disabled": True}),
            ("skills-a", {}),
            ("skills-b", {}),
        ]
    ]
    picked, skipped = select(repos, OrgRequest("o"))
    assert [r["name"] for r in picked] == ["app", "skills-a", "skills-b"]
    assert skipped == {"archived": 1, "fork": 1, "disabled": 1}
    picked, skipped = select(
        repos, OrgRequest("o", include_archived=True, include_forks=True, match=["SKILLS-*"])
    )
    assert [r["name"] for r in picked] == ["skills-a", "skills-b"]
    assert skipped == {"disabled": 1, "name": 3}
    picked, skipped = select(repos, OrgRequest("o", limit=2))
    assert [r["name"] for r in picked] == ["app", "skills-a"]
    assert skipped["limit"] == 1


# --- scanning ---------------------------------------------------------------------------


def test_pagination_snapshots_and_report(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 230, with_skills=5)
    org.add("archived-one", archived=True)
    org.add("forked-one", fork=True)
    states: list[OrgState] = []

    class Recorder:
        def update(self, state: OrgState) -> None:
            states.append(state)

        def finished(self, repo: object) -> None:
            pass

    outcome = run_org_scan(
        OrgRequest(owner="acme", jobs=1),
        store_dir=store_dir,
        client=client(),
        progress=Recorder(),
    )
    report = outcome.report
    assert outcome.exit_code == 0
    assert (report.status, report.owner, report.owner_type) == ("complete", "Acme", "organization")
    assert report.owner_key == "github.com/acme"
    assert report.listed == 232
    assert report.skipped == {"archived": 1, "fork": 1}
    assert len(report.repos) == 230 and report.count("scanned") == 230
    assert report.with_skills == 5
    # 1 account + 3 listing pages + commit and tree per repository.
    assert report.api_requests == org.api_calls == 1 + 3 + 2 * 230
    assert len(snapshot_files(store_dir)) == 230
    snap = store.load(store_dir / report.repos[0].snapshot)  # type: ignore[operator]
    assert snap.source.repo_key == "github.com/acme/repo-000"
    assert snap.repo is not None and snap.repo.pushed_at == "2026-01-01T00:00:01Z"
    assert [s.name for s in snap.skills] == ["s0"]
    # The report is saved next to the store, not in it.
    assert outcome.saved_path is not None and outcome.saved_path.parent.name == "orgs"
    assert store.load_org(outcome.saved_path) == report
    assert states[-1].phase == "done" and states[-1].done == 230
    assert any(s.phase == "listing" and s.listed == 200 for s in states)


def test_rerun_skips_unchanged_repositories_without_requests(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 120, with_skills=3)
    scan_org(store_dir)
    before = org.api_calls

    report = scan_org(store_dir).report
    assert report.count("unchanged") == 120
    assert org.api_calls - before == 1 + 2  # the account and two listing pages only
    assert len(snapshot_files(store_dir)) == 120

    org.push("repo-001", {".claude/skills/new/SKILL.md": skill("new")})
    before = org.api_calls
    report = scan_org(store_dir).report
    changed = next(r for r in report.repos if r.full_name == "Acme/repo-001")
    assert (changed.status, changed.skills) == ("scanned", 1)
    assert org.api_calls - before == 1 + 2 + 2
    assert len(snapshot_files(store_dir)) == 121


def test_push_to_another_branch_costs_one_request(org: FakeOrg, store_dir: Path) -> None:
    org.add("app", {".claude/skills/a/SKILL.md": skill("a")})
    scan_org(store_dir)
    org.repos["app"].pushed += 1  # pushed_at changes, the default branch does not
    before = org.api_calls
    report = scan_org(store_dir).report
    assert report.repos[0].status == "unchanged"
    assert org.api_calls - before == 1 + 1 + 1  # account, listing, commit; no tree
    assert len(snapshot_files(store_dir)) == 1


def test_force_and_options_bypass_the_unchanged_check(org: FakeOrg, store_dir: Path) -> None:
    org.add("app", {".claude/skills/a/SKILL.md": skill("a"), "docs/skills/b/SKILL.md": skill("b")})
    scan_org(store_dir)
    assert scan_org(store_dir, force=True).report.repos[0].status == "scanned"
    report = scan_org(store_dir, exclude=["docs/*"]).report
    assert (report.repos[0].status, report.repos[0].skills) == ("scanned", 1)
    assert len(snapshot_files(store_dir)) == 3


def test_failures_do_not_stop_the_scan(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 4, with_skills=4)
    org.broken.add("repo-001")
    org.add("blank", empty=True)
    outcome = scan_org(store_dir, gh=client(sleep=Sleeps()))
    report = outcome.report
    statuses = {r.full_name: r.status for r in report.repos}
    assert statuses == {
        "Acme/blank": "empty",
        "Acme/repo-000": "scanned",
        "Acme/repo-001": "failed",
        "Acme/repo-002": "scanned",
        "Acme/repo-003": "scanned",
    }
    failed = next(r for r in report.repos if r.status == "failed")
    assert failed.error and "HTTP 500" in failed.error
    assert (report.status, outcome.exit_code) == ("partial", 6)

    org.broken.clear()
    report = scan_org(store_dir).report
    assert report.status == "complete"
    assert next(r for r in report.repos if r.full_name == "Acme/repo-001").status == "scanned"


def test_rate_limit_stops_and_the_next_run_resumes(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 10, with_skills=10)
    org.quota = 1 + 1 + 2 * 4 + 1  # account, listing, 4 repositories and half of the 5th
    outcome = scan_org(store_dir)
    report = outcome.report
    assert (report.status, outcome.exit_code) == ("stopped", 4)
    assert isinstance(outcome.stop_error, RateLimitError)
    assert report.count("scanned") == 4 and report.count("pending") == 6
    assert report.message and "Run the same scan again" in report.message
    assert "resets at" in report.message
    assert len(snapshot_files(store_dir)) == 4

    org.used, org.quota = 0, 5000  # the quota resets
    before = org.api_calls
    report = scan_org(store_dir).report
    assert report.status == "complete"
    assert report.count("unchanged") == 4 and report.count("scanned") == 6
    assert org.api_calls - before == 1 + 1 + 2 * 6  # nothing is fetched twice
    assert len(snapshot_files(store_dir)) == 10


def test_rate_limit_wait(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 6, with_skills=1)
    org.quota = 6
    sleeps = Sleeps(org)
    states: list[OrgState] = []

    class Recorder:
        def update(self, state: OrgState) -> None:
            states.append(state)

        def finished(self, repo: object) -> None:
            pass

    outcome = run_org_scan(
        OrgRequest(owner="acme", jobs=1, wait=True),
        store_dir=store_dir,
        client=client(sleep=sleeps, clock=RESET - 600),
        progress=Recorder(),
    )
    assert outcome.report.status == "complete"
    assert outcome.report.count("scanned") == 6
    assert sleeps.calls and sleeps.calls[0] == pytest.approx(601)
    assert any(s.wait_until is not None for s in states)


def test_rate_limit_too_far_away_stops_even_with_wait(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 3, with_skills=0)
    org.quota = 3
    outcome = scan_org(store_dir, gh=client(clock=RESET - 7200), wait=True)
    assert outcome.report.status == "stopped"


def test_secondary_rate_limit_is_waited_out(org: FakeOrg, store_dir: Path) -> None:
    org.add("app")
    org.secondary = 2
    sleeps = Sleeps()
    report = scan_org(store_dir, gh=client(sleep=sleeps)).report
    assert report.status == "complete"
    assert sleeps.calls == [30.0, 30.0]


def test_single_repository_scan_does_not_wait() -> None:
    with respx.mock() as router:
        router.get(f"{API}/repos/o/r").mock(
            return_value=httpx.Response(
                403, json={"message": "secondary rate limit"}, headers={"retry-after": "30"}
            )
        )
        sleeps = Sleeps()
        with pytest.raises(RateLimitError, match="secondary rate limit"):
            run_scan(ScanRequest(target="o/r"), store_dir=None, client=client(sleep=sleeps))
        assert sleeps.calls == []


def test_parallel_scan_matches_serial(org: FakeOrg, tmp_path: Path) -> None:
    populate(org, 40, with_skills=10)
    serial = scan_org(tmp_path / "a" / "scans", jobs=1).report
    parallel = scan_org(tmp_path / "b" / "scans", jobs=6).report
    key = [(r.full_name, r.status, r.skills, r.commit_sha) for r in serial.repos]
    assert [(r.full_name, r.status, r.skills, r.commit_sha) for r in parallel.repos] == key


def test_several_hundred_repositories_within_the_api_limit(org: FakeOrg, store_dir: Path) -> None:
    """600 repositories fit an authenticated hour (5000 requests) four times over."""
    populate(org, 600, with_skills=40)
    outcome = scan_org(store_dir, jobs=8)
    report = outcome.report
    assert report.status == "complete" and report.with_skills == 40
    assert report.api_requests == 1 + 6 + 2 * 600 < org.quota / 4
    assert org.quota - org.used > 3700
    before = org.api_calls
    assert scan_org(store_dir, jobs=8).report.count("unchanged") == 600
    assert org.api_calls - before == 1 + 6


def test_user_accounts(store_dir: Path) -> None:
    fake = FakeOrg(login="alice", kind="User")
    fake.add("dots", {".claude/commands/x.md": "Do x.\n"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        report = run_org_scan(
            OrgRequest(owner="alice"), store_dir=store_dir, client=client()
        ).report
        assert report.owner_type == "user"
        assert "/users/alice/repos" in fake.calls
        # The token owner: /user/repos lists private repositories too.
        fake.viewer = "Alice"
        run_org_scan(OrgRequest(owner="alice", force=True), store_dir=store_dir, client=client())
        assert "/user/repos" in fake.calls


def test_unknown_account(org: FakeOrg, store_dir: Path) -> None:
    with pytest.raises(AccessError, match="'nobody' not found"):
        run_org_scan(OrgRequest(owner="nobody"), store_dir=store_dir, client=client())


def test_pagination_never_leaves_the_api_host(store_dir: Path) -> None:
    with respx.mock() as router:
        router.get(f"{API}/users/acme").mock(
            return_value=httpx.Response(200, json={"login": "acme", "type": "Organization"})
        )
        router.get(f"{API}/orgs/acme/repos").mock(
            return_value=httpx.Response(
                200, json=[], headers={"link": '<https://evil.example/x?page=2>; rel="next"'}
            )
        )
        with pytest.raises(Exception, match="unexpected pagination URL"):
            run_org_scan(OrgRequest(owner="acme"), store_dir=store_dir, client=client())


def test_cancel_marks_the_rest_pending(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 5, with_skills=0)
    cancel = threading.Event()

    class CancelAfterFirst:
        def update(self, state: OrgState) -> None:
            pass

        def finished(self, repo: object) -> None:
            cancel.set()

    outcome = run_org_scan(
        OrgRequest(owner="acme", jobs=1),
        store_dir=store_dir,
        client=client(),
        progress=CancelAfterFirst(),
        cancel=cancel,
    )
    report = outcome.report
    assert (report.status, outcome.exit_code) == ("interrupted", 130)
    assert report.count("pending") >= 3
    assert len(store.latest_orgs(store.orgs_dir(store_dir))) == 1


# --- CLI --------------------------------------------------------------------------------


def cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> tuple[int, str, str]:
    from typer.testing import CliRunner

    from skill_atlas.cli import app
    from skill_atlas.errors import AtlasError

    monkeypatch.setenv("GITHUB_TOKEN", "t")  # never ask a real `gh` for a token
    result = CliRunner().invoke(app, list(args))
    code = result.exit_code
    if isinstance(result.exception, AtlasError):
        code = result.exception.exit_code
    elif result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return code, result.stdout, result.stderr


def test_cli_org_scan_summary_and_exit_codes(org: FakeOrg, monkeypatch: pytest.MonkeyPatch) -> None:
    populate(org, 5, with_skills=2)
    code, out, err = cli(monkeypatch, "scan", "--org", "acme")
    assert code == 0, err
    assert "github.com/acme (organization): complete" in out
    assert "2 with skills · 5 scanned" in out
    assert "Acme/repo-001" in out and "organization scan report: " in out
    # One progress line per repository, in the order they finish.
    assert "[5/5] " in err and "] Acme/repo-004: scanned, no skills" in err

    org.broken.add("repo-003")
    org.push("repo-003", {"x.md": "x"})
    code, out, _ = cli(monkeypatch, "scan", "https://github.com/acme", "-q")
    assert code == 6
    assert "failed: Acme/repo-003: " in out

    org.broken.clear()
    org.push("repo-004", {"y.md": "y"})
    org.quota = org.used + 3  # the account, the listing, then out of quota
    code, out, _ = cli(monkeypatch, "scan", "--org", "acme", "-q")
    assert code == 4
    assert "Run the same scan again to continue" in out


def test_cli_org_plain_report(org: FakeOrg, monkeypatch: pytest.MonkeyPatch) -> None:
    populate(org, 3, with_skills=1)
    org.add("old", archived=True)
    code, out, _ = cli(monkeypatch, "scan", "--org", "acme", "--plain", "-q", "--match", "repo-*")
    assert code == 0
    assert "owner: github.com/acme\nowner_type: organization\nstatus: complete\n" in out
    assert "skipped: archived 1\n" in out
    assert "repo: github.com/acme/repo-000 status=scanned skills=1 agents=0 snapshot=" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--org", "acme", "--ref", "main"], "--ref: not available"),
        (["--org", "acme", "--output", "-"], "--output: not available"),
        (["--org", "acme", "--jobs", "20"], "--jobs must be between"),
        (["o/r", "--org", "acme"], "either a target or --org"),
        (["o/r", "--limit", "3"], "--limit: only for an organization scan"),
        ([], "missing target"),
    ],
)
def test_cli_org_usage_errors(
    monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    from typer.testing import CliRunner

    from skill_atlas.cli import app

    result = CliRunner().invoke(app, ["scan", *args])
    assert isinstance(result.exception, UsageError)
    assert message in str(result.exception)


def test_cancel_cuts_a_rate_limit_wait_short(org: FakeOrg, store_dir: Path) -> None:
    import time

    populate(org, 4, with_skills=0)
    org.quota = 3  # the account, the listing, one commit: then a 10-minute wait
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    gh = GitHubClient("github.com", "t", clock=lambda: RESET - 600)  # real, interruptible waits
    started = time.monotonic()
    outcome = run_org_scan(
        OrgRequest(owner="acme", jobs=2, wait=True), store_dir=store_dir, client=gh, cancel=cancel
    )
    assert time.monotonic() - started < 5
    assert (outcome.report.status, outcome.exit_code) == ("interrupted", 130)
    assert outcome.report.count("pending") == 4


def test_cancel_during_the_listing(org: FakeOrg, store_dir: Path) -> None:
    from skill_atlas.errors import Interrupted

    populate(org, 150, with_skills=0)
    cancel = threading.Event()

    class CancelWhileListing:
        def update(self, state: OrgState) -> None:
            if state.listed:
                cancel.set()

        def finished(self, repo: object) -> None:
            pass

    with pytest.raises(Interrupted, match="during the listing"):
        run_org_scan(
            OrgRequest(owner="acme"),
            store_dir=store_dir,
            client=client(),
            progress=CancelWhileListing(),
            cancel=cancel,
        )
    assert "/repos/Acme/repo-000/commits/main" not in org.calls


def test_ctrl_c_records_the_running_repositories(org: FakeOrg, store_dir: Path) -> None:
    populate(org, 6, with_skills=6)

    class CtrlC:
        def __init__(self) -> None:
            self.pressed = False

        def update(self, state: OrgState) -> None:
            pass

        def finished(self, repo: object) -> None:
            if not self.pressed:
                self.pressed = True
                raise KeyboardInterrupt

    outcome = run_org_scan(
        OrgRequest(owner="acme", jobs=2),
        store_dir=store_dir,
        client=client(),
        progress=CtrlC(),
    )
    report = outcome.report
    assert (report.status, outcome.exit_code) == ("interrupted", 130)
    assert report.count("pending") >= 2
    # Every saved snapshot is in the report: nothing finished after it was written.
    saved = {p.name for p in snapshot_files(store_dir)}
    assert saved == {r.snapshot for r in report.repos if r.snapshot}

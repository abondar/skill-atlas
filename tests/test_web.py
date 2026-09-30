from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from skill_atlas import store
from skill_atlas.web import COOKIE, Job, Server, WebApp
from tests.conftest import make_tree, scan_path

EVIL = "\x1b]8;;https://evil.example\x1b\\click\x1b]8;;\x1b\\ ‮"


@pytest.fixture
def scans(tmp_path: Path) -> Path:
    root = make_tree(
        tmp_path / "repo",
        {
            ".claude/skills/real/SKILL.md": "---\nname: real\ndescription: d\n---\n# Real\n",
            ".agents/skills/real/SKILL.md": "---\nname: real\ndescription: d\n---\n# Real\n",
            "src/jvmTest/resources/skills/fake/SKILL.md": "---\nname: fake\ndescription: d\n---\n",
            "skills/evil/SKILL.md": (
                "---\nname: evil\ndescription: x\n---\n"
                f"<script>alert(1)</script> <img src=x onerror=alert(1)> {EVIL}\n"
                "[js](javascript:alert(1)) ![pixel](https://tracker.example/p.png)\n"
            ),
        },
    )
    directory = tmp_path / "scans"
    store.save(scan_path(root), directory)
    return directory


@pytest.fixture
def server(scans: Path) -> Iterator[Server]:
    srv = Server(("127.0.0.1", 0), WebApp(scans, token="secret-token"))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def base(srv: Server) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def client(server: Server) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base(server), follow_redirects=True) as c:
        r = c.get("/?token=secret-token")  # follows the redirect, keeps the cookie
        assert r.status_code == 200 and "app.js" in r.text
        yield c


def test_token_moves_into_a_strict_cookie(server: Server) -> None:
    with httpx.Client(base_url=base(server), follow_redirects=False) as c:
        r = c.get("/?token=secret-token")
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"]
        assert f"{COOKIE}=secret-token" in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_api_needs_the_cookie(server: Server) -> None:
    with httpx.Client(base_url=base(server)) as c:
        assert c.get("/").status_code == 401
        assert c.get("/?token=wrong").status_code == 401
        assert c.get("/api/repos").status_code == 401
        c.cookies.set(COOKIE, "wrong")
        assert c.get("/api/repos").status_code == 401


def test_foreign_host_header_is_rejected(client: httpx.Client) -> None:
    # DNS rebinding: evil.example resolves to 127.0.0.1 but keeps its own Host header.
    assert client.get("/api/repos", headers={"Host": "evil.example"}).status_code == 403


def test_security_headers(client: httpx.Client) -> None:
    r = client.get("/api/repos")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "default-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_repos_and_snapshot(client: httpx.Client, scans: Path) -> None:
    data = client.get("/api/repos").json()
    assert len(data["repos"]) == 1
    repo = data["repos"][0]
    assert repo["total"] == 4 and repo["unique"] == 3  # two identical copies of `real`
    assert repo["by_category"] == {"catalog": 1, "project": 2, "test": 1}
    name = repo["latest"]["file"]
    snap = client.get("/api/snapshot", params={"file": name}).json()
    skills = {s["path"]: s for s in snap["skills"]}
    fake = skills["src/jvmTest/resources/skills/fake/SKILL.md"]
    assert fake["category"] == "test" and fake["relevant"] is False
    real = skills[".claude/skills/real/SKILL.md"]
    assert real["dup_key"] == skills[".agents/skills/real/SKILL.md"]["dup_key"]
    raw = client.get("/api/snapshot/raw", params={"file": name})
    assert raw.content == (scans / name).read_bytes()
    assert "attachment" in raw.headers["content-disposition"]


def test_untrusted_content_is_sanitized(client: httpx.Client) -> None:
    name = client.get("/api/repos").json()["repos"][0]["latest"]["file"]
    snap = client.get("/api/snapshot", params={"file": name}).json()
    evil = next(s for s in snap["skills"] if s["name"] == "evil")
    html = evil["body_html"]
    assert "<script" not in html and "<img" not in html  # raw HTML is escaped
    assert "&lt;script&gt;" in html
    assert 'href="javascript' not in html
    assert "tracker.example/p.png" in html and "<img" not in html  # image shown as text
    assert "\x1b" not in evil["body"] and "‮" not in evil["body"]


@pytest.mark.parametrize(
    "name", ["../secret.json", "/etc/passwd", ".hidden.json", "x.txt", "sub/x.json", ""]
)
def test_snapshot_names_cannot_escape_the_store(client: httpx.Client, name: str) -> None:
    assert client.get("/api/snapshot", params={"file": name}).status_code == 404
    assert client.get("/api/snapshot/raw", params={"file": name}).status_code == 404


def test_scan_requires_custom_header_and_json(client: httpx.Client, tmp_path: Path) -> None:
    target = {"target": str(tmp_path)}
    assert client.post("/api/scan", json=target).status_code == 403
    form = client.post("/api/scan", data={"target": str(tmp_path)}, headers={"X-Skill-Atlas": "1"})
    assert form.status_code == 403


def wait(client: httpx.Client, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/scan/{job_id}").json()
        if job["state"] != "running":
            return dict(job)
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_scan_new_repo(client: httpx.Client, tmp_path: Path, scans: Path) -> None:
    fresh = make_tree(tmp_path / "fresh", {"skills/new/SKILL.md": "---\nname: new\n---\n"})
    headers = {"X-Skill-Atlas": "1"}
    r = client.post("/api/scan", json={"target": str(fresh)}, headers=headers)
    assert r.status_code == 202
    job = wait(client, r.json()["id"])
    assert job["state"] == "done", job
    assert job["file"] and (scans / str(job["file"])).is_file()
    assert len(client.get("/api/repos").json()["repos"]) == 2

    bad = client.post("/api/scan", json={"target": "not a target"}, headers=headers)
    failed = wait(client, bad.json()["id"])
    assert failed["state"] == "error" and failed["error"]
    missing = client.post("/api/scan", json={}, headers=headers)
    assert missing.status_code == 400


def test_one_scan_at_a_time(client: httpx.Client, server: Server) -> None:
    app = server.app
    app.jobs["busy"] = Job("busy", "x")  # state defaults to running
    r = client.post("/api/scan", json={"target": "o/r"}, headers={"X-Skill-Atlas": "1"})
    assert r.status_code == 409


def test_static_files(client: httpx.Client) -> None:
    js = client.get("/static/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/../__init__.py").status_code == 404


def test_repo_rows_carry_names_and_rescan_target(client: httpx.Client, tmp_path: Path) -> None:
    repo = client.get("/api/repos").json()["repos"][0]
    assert repo["rescan_target"] == str((tmp_path / "repo").resolve())
    assert repo["source"]["name"] == "repo"
    assert repo["meta"] is None  # local scans have no GitHub metadata


def test_skills_across_repositories(client: httpx.Client) -> None:
    relevant = client.get("/api/skills").json()["skills"]
    assert sorted(s["name"] for s in relevant) == ["evil", "real", "real"]
    assert "body" not in relevant[0]
    everything = client.get("/api/skills", params={"category": "all"}).json()["skills"]
    assert "fake" in {s["name"] for s in everything}
    tests = client.get("/api/skills", params={"category": "test"}).json()["skills"]
    assert [s["name"] for s in tests] == ["fake"]
    assert client.get("/api/skills", params={"category": "bogus"}).status_code == 400


def test_scan_job_reports_finished_stages(client: httpx.Client, tmp_path: Path) -> None:
    fresh = make_tree(tmp_path / "staged", {"skills/x/SKILL.md": "---\nname: x\n---\n"})
    r = client.post("/api/scan", json={"target": str(fresh)}, headers={"X-Skill-Atlas": "1"})
    job = wait(client, r.json()["id"])
    stages = [name for name, _ in job["stages"]]  # type: ignore[attr-defined]
    assert stages[0].startswith("reading local repository")
    assert any(s.startswith("saving snapshot") for s in stages)


def test_favicon(client: httpx.Client) -> None:
    for path in ("/favicon.ico", "/static/favicon.svg"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"


def test_similar_skills(client: httpx.Client, tmp_path: Path) -> None:
    body = "Run the Gradle wrapper, bump the version, check plugin compatibility and tests.\n" * 5
    root = make_tree(
        tmp_path / "similar",
        {
            "skills/a/SKILL.md": f"---\nname: a\ndescription: Bumps Gradle.\n---\n{body}",
            "skills/b/SKILL.md": f"---\nname: b\ndescription: Bumps Gradle.\n---\n{body}More.\n",
            "skills/c/SKILL.md": "---\nname: c\ndescription: Docker images.\n---\nBuild it.\n",
        },
    )
    store.save(scan_path(root), Path(client.get("/api/repos").json()["store"]))
    repo = next(r for r in client.get("/api/repos").json()["repos"] if "similar" in r["repo_key"])
    name = repo["latest"]["file"]
    ids = {
        s["name"]: s["id"]
        for s in client.get("/api/snapshot", params={"file": name}).json()["skills"]
    }
    data = client.get("/api/similar", params={"file": name, "id": ids["a"]}).json()
    assert data["threshold"] == 0.4
    [match] = data["similar"]
    assert match["name"] == "b" and match["level"] == "near-identical"
    assert match["overlap_here"] == 1.0 and 0 < match["score"] <= 1
    assert data["versions"] == []
    assert "gradle" in match["shared_terms"]
    assert client.get("/api/similar", params={"file": name, "id": ids["c"]}).json()["similar"] == []
    assert client.get("/api/similar", params={"file": name, "id": "nope"}).status_code == 404
    assert (
        client.get("/api/similar", params={"file": "../x.json", "id": ids["a"]}).status_code == 404
    )

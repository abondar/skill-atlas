from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cases import CASES
from tests.conftest import make_tree


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from skill_atlas.cli import main; main()", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_tree(tmp_path / "repo", CASES["d1_agent_skill"])


def test_no_command_without_tty_prints_help() -> None:
    proc = run()
    assert proc.returncode == 0, proc.stderr
    assert "Usage" in proc.stdout and "scan" in proc.stdout


def test_scan_without_tty_prints_summary_and_saves(repo: Path) -> None:
    proc = run("scan", str(repo))
    assert proc.returncode == 0, proc.stderr
    assert "3 skills, 0 agents" in proc.stdout
    assert "snapshot: " in proc.stdout


def test_output_dash_writes_json_to_stdout(repo: Path) -> None:
    proc = run("scan", str(repo), "--no-save", "--output", "-")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema_version"] == 1
    assert data["stats"]["skills"] == 3
    assert "not saved" in proc.stderr


def test_output_file_is_not_overwritten(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "o.json"
    out.write_text("keep")
    proc = run("scan", str(repo), "--no-tui", "--output", str(out))
    assert proc.returncode == 2
    assert out.read_text() == "keep"
    assert run("scan", str(repo), "--no-tui", "--output", str(out), "--force").returncode == 0
    assert json.loads(out.read_text())["stats"]["skills"] == 3


def test_empty_repo_exits_zero(tmp_path: Path) -> None:
    empty = make_tree(tmp_path / "empty", CASES["empty"])
    proc = run("scan", str(empty), "--no-tui")
    assert proc.returncode == 0
    assert "0 skills" in proc.stdout


def test_bad_target_exit_code() -> None:
    assert run("scan", "not a target").returncode == 2
    assert run("scan").returncode == 2


def test_repos_skills_show(repo: Path) -> None:
    assert run("scan", str(repo), "--no-tui").returncode == 0
    repos = json.loads(run("repos", "--json").stdout)
    assert len(repos) == 1 and repos[0]["skills"] == 3
    skills = json.loads(run("skills", "--json").stdout)
    assert sorted(s["skill"]["name"] for s in skills) == ["pdf", "review", "unscoped"]
    assert "body" not in skills[0]["skill"]
    grouped = run("skills", "--group-by", "name")
    assert "3 groups, 3 skills" in grouped.stdout
    shown = run("show", repos[0]["repo_key"])
    assert shown.returncode == 0 and "3 skills" in shown.stdout
    assert run("show", "github.com/none/none").returncode == 2


def test_pin_and_unpin(repo: Path, tmp_path: Path) -> None:
    other = make_tree(tmp_path / "other", CASES["d2_claude_command"])
    assert run("scan", str(repo), "--no-tui").returncode == 0
    assert run("scan", str(other), "--no-tui").returncode == 0
    keys = [r["repo_key"] for r in json.loads(run("repos", "--json").stdout)]
    older = keys[-1]
    proc = run("pin", older)
    assert proc.returncode == 0, proc.stderr
    assert f"pinned {older}" in proc.stdout
    repos = json.loads(run("repos", "--json").stdout)
    assert [(r["repo_key"], r["pinned"]) for r in repos] == [(older, True), (keys[0], False)]
    assert [r["repo_key"] for r in json.loads(run("repos", "--json", "--pinned").stdout)] == [older]
    assert "●" in run("repos").stdout

    skills = json.loads(run("skills", "--json", "--repo", keys[0]).stdout)
    last = skills[-1]["skill"]["id"]
    assert run("pin", keys[0], last).returncode == 0
    rows = json.loads(run("skills", "--json").stdout)
    assert (rows[0]["skill"]["id"], rows[0]["pinned"]) == (last, True)
    assert [r["skill"]["id"] for r in json.loads(run("skills", "--json", "--pinned").stdout)] == [
        last
    ]
    grouped = run("skills", "--group-by", "name", "--json")
    assert json.loads(grouped.stdout)[0]["pinned"] is True

    assert run("pin", "github.com/no/such").returncode == 2
    assert run("pin", keys[0], "agent-skill:no/SKILL.md").returncode == 2
    assert run("unpin", older).returncode == 0
    assert run("unpin", keys[0], last).returncode == 0
    assert run("repos", "--json", "--pinned").stdout.strip() == "[]"


def test_skills_category_filter(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "repo", CASES["noise"])
    assert run("scan", str(root), "--no-tui").returncode == 0

    def names(*args: str) -> list[str]:
        proc = run("skills", "--json", *args)
        assert proc.returncode == 0, proc.stderr
        return sorted(s["skill"]["name"] for s in json.loads(proc.stdout))

    assert names() == ["real"]  # relevant by default
    assert names("--category", "test") == ["fake"]
    assert names("--category", "all") == ["fake", "real"]
    assert run("skills", "--category", "bogus").returncode == 2


def test_terminal_escapes_are_sanitized_in_summary(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path / "evil",
        {
            "skills/evil/SKILL.md": '---\nname: "ev\\e[31mil"\ndescription: x\n---\nbody\n',
        },
    )
    proc = run("scan", str(root), "--no-tui")
    assert proc.returncode == 0
    assert "\x1b" not in proc.stdout
    assert "evil" in proc.stdout


def test_web_flag_only_without_command() -> None:
    proc = run("--web", "repos")
    assert proc.returncode == 2 and "--web works only without a command" in proc.stderr


def test_web_port_in_use_is_a_clean_error() -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        proc = run("--web", "--no-browser", "--web-port", str(port))
    assert proc.returncode == 1 and "cannot listen on" in proc.stderr


def test_version() -> None:
    proc = run("--version")
    assert proc.returncode == 0 and proc.stdout.startswith("skill-atlas ")


def test_plain_report(repo: Path) -> None:
    proc = run("scan", str(repo), "--plain")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("repo: local/repo-")
    assert "stats: 3 skills, 0 agents, 0 external, 0 plugins" in lines
    assert "## pdf" in lines
    assert "resource: .claude/skills/pdf/scripts/extract.py (11 bytes)" in lines
    assert "body:" not in lines
    with_body = run(
        "show", proc.stdout.split("snapshot: ")[1].split("\n")[0], "--plain", "--with-body"
    )
    assert with_body.returncode == 0, with_body.stderr
    assert "body:" in with_body.stdout.splitlines()
    assert "  # pdf" in with_body.stdout.splitlines()


def test_plain_conflicts_with_json_stdout(repo: Path) -> None:
    assert run("scan", str(repo), "--plain", "--output", "-").returncode == 2


def test_plain_report_is_sanitized(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path / "evil",
        {"skills/evil/SKILL.md": "---\nname: evil\ndescription: x\n---\n\x1b]8;;u\x1b\\a‮\n"},
    )
    proc = run("scan", str(root), "--plain", "--with-body", "--no-save")
    assert proc.returncode == 0
    assert "\x1b" not in proc.stdout and "‮" not in proc.stdout
    assert "snapshot: -" in proc.stdout

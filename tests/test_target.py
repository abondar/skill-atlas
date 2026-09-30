from __future__ import annotations

from pathlib import Path

import pytest

from skill_atlas.errors import UsageError
from skill_atlas.sources.git import remote_to_key, strip_credentials
from skill_atlas.target import GitHubTarget, LocalTarget, parse_target


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("o/r", GitHubTarget("github.com", "o", "r")),
        ("https://github.com/o/r", GitHubTarget("github.com", "o", "r")),
        ("https://github.com/o/r.git", GitHubTarget("github.com", "o", "r")),
        ("git@github.com:o/r.git", GitHubTarget("github.com", "o", "r")),
        (
            "https://github.com/o/r/tree/feature/x/skills",
            GitHubTarget("github.com", "o", "r", ["feature", "x", "skills"]),
        ),
        ("https://ghe.corp/o/r", GitHubTarget("ghe.corp", "o", "r")),
    ],
)
def test_remote_forms(raw: str, expected: GitHubTarget) -> None:
    assert parse_target(raw) == expected


def test_short_form_uses_host() -> None:
    assert parse_target("o/r", host="ghe.corp") == GitHubTarget("ghe.corp", "o", "r")


def test_existing_path_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "o" / "r").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    target = parse_target("o/r")
    assert isinstance(target, LocalTarget)
    assert target.looks_remote


@pytest.mark.parametrize(
    "raw", ["", "not a target", "https://github.com/o", "https://github.com/o/r/pulls"]
)
def test_invalid(raw: str) -> None:
    with pytest.raises(UsageError):
        parse_target(raw)


def test_remote_key_and_credentials() -> None:
    assert remote_to_key("git@github.com:Org/Repo.git") == ("github.com", "org/repo")
    assert remote_to_key("https://github.com/o/r.git") == ("github.com", "o/r")
    assert remote_to_key("ssh://git@gitlab.com:22/g/sub/r.git") == ("gitlab.com", "g/sub/r")
    assert strip_credentials("https://x-access-token:ghp_secret@github.com/o/r.git") == (
        "https://github.com/o/r.git"
    )

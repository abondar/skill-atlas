from __future__ import annotations

import pytest

from skill_atlas import frontmatter


def test_split_requires_first_line() -> None:
    assert frontmatter.split(" ---\na: 1\n---\n").raw is None
    parts = frontmatter.split("---\na: 1\n---\nbody\n")
    assert (parts.raw, parts.body) == ("a: 1", "body\n")


def test_crlf_delimiters() -> None:
    parsed = frontmatter.parse("---\r\nname: x\r\n---\r\nbody")
    assert parsed.data == {"name": "x"}


def test_empty_frontmatter_is_empty_mapping() -> None:
    assert frontmatter.parse("---\n---\nbody").data == {}


def test_non_mapping() -> None:
    parsed = frontmatter.parse("---\n- a\n---\n")
    assert parsed.error == "frontmatter is not a mapping"


def test_dates_become_strings() -> None:
    assert frontmatter.parse("---\nd: 2026-01-02\n---\n").data == {"d": "2026-01-02"}


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("a: !!python/object:os.system x", "explicit YAML tag"),
        ("a: !custom x", "explicit YAML tag"),
        ("a: &x [1]\nb: *x", "anchors"),
        ("a: " + "[" * 40 + "]" * 40, "depth"),
    ],
)
def test_unsafe_yaml_is_rejected(yaml_text: str, message: str) -> None:
    with pytest.raises(frontmatter.YamlError, match=message):
        frontmatter.safe_load(yaml_text)


def test_size_limit() -> None:
    with pytest.raises(frontmatter.YamlError, match="exceeds"):
        frontmatter.safe_load("a: " + "x" * (65 * 1024))


def test_implicit_types_allowed() -> None:
    assert frontmatter.safe_load("a: 1\nb: true\nc: null\nd: .inf") == {
        "a": 1,
        "b": True,
        "c": None,
        "d": "inf",
    }

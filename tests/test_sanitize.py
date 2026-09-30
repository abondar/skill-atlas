from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from skill_atlas.sanitize import BIDI_MARKER, sanitize, sanitize_line

FORBIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def test_strips_csi_and_osc() -> None:
    assert sanitize("\x1b[31mred\x1b[0m") == "red"
    assert sanitize("\x1b]8;;https://evil\x1b\\click\x1b]8;;\x1b\\") == "click"
    assert sanitize("\x1b]0;title\x07after") == "after"
    assert sanitize("a\x1bPq#0;2;0;0;0\x1b\\b") == "ab"


def test_strips_8bit_sequences_and_controls() -> None:
    assert sanitize("\x9b31mx") == "x"
    assert sanitize("\x9d8;;u\x9cy") == "y"
    assert sanitize("a\x00b\x07c\x7fd\re") == "abcde"
    assert sanitize("line1\r\nline2\tx") == "line1\nline2\tx"


def test_marks_bidi_overrides() -> None:
    assert sanitize("ab‮cd⁦") == f"ab{BIDI_MARKER}cd{BIDI_MARKER}"


def test_sanitize_line_collapses_whitespace() -> None:
    assert sanitize_line("a\n\n b\x1b[2J") == "a b"


def test_plain_unicode_is_kept() -> None:
    text = "Привет — skills 🚀 ✓"
    assert sanitize(text) == text


escape_heavy = st.text(
    alphabet=st.sampled_from(
        [
            "\x1b",
            "[",
            "]",
            "\\",
            "\x07",
            "\x9b",
            "\x9c",
            "\x9d",
            "P",
            "8",
            ";",
            "m",
            "‮",
            "⁦",
            "\r",
            "\n",
            "\x00",
            "a",
            "0",
        ]
    ),
    max_size=200,
)


@settings(max_examples=500)
@given(st.one_of(escape_heavy, st.text(max_size=200)))
def test_output_never_contains_control_characters(text: str) -> None:
    assert not FORBIDDEN.search(sanitize(text))


@settings(max_examples=200)
@given(st.text(max_size=100))
def test_sanitize_is_idempotent(text: str) -> None:
    once = sanitize(text)
    assert sanitize(once) == once

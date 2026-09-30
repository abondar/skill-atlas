"""Terminal output sanitization (SPEC section 9).

Snapshot content comes from untrusted repositories. Every string that reaches the
terminal goes through `sanitize` first. Snapshots themselves keep the original bytes.
"""

from __future__ import annotations

import re

BIDI_MARKER = "⟦bidi⟧"

_ESC_SEQUENCES = re.compile(
    r"""
    \x1b\[[0-?]*[ -/]*[@-~]                  # CSI
    | \x1b\][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?  # OSC, incl. OSC 8 hyperlinks
    | \x1b[PX^_][^\x1b\x9c]*(?:\x1b\\|\x9c)?       # DCS, SOS, PM, APC
    | \x1b[ -/]*[0-~]                         # other ESC sequences (nF, Fp, Fe, Fs)
    | \x9b[0-?]*[ -/]*[@-~]                  # 8-bit CSI
    | \x9d[^\x07\x9c]*(?:\x07|\x9c)?          # 8-bit OSC
    | [\x90\x98\x9e\x9f][^\x9c]*\x9c?         # 8-bit DCS, SOS, PM, APC
    """,
    re.VERBOSE,
)
# C0 except \t and \n, DEL, C1, and any leftover ESC.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BIDI = re.compile(r"[‪-‮⁦-⁩]")


def sanitize(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = _ESC_SEQUENCES.sub("", text)
    text = _CONTROL.sub("", text)
    return _BIDI.sub(BIDI_MARKER, text)


def sanitize_line(text: str) -> str:
    """Sanitize and collapse to one line, for table cells and single-line fields."""
    return " ".join(sanitize(text).split())

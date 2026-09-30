"""YAML frontmatter parsing with safety limits (SPEC sections 5.4 and 9)."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

import yaml

MAX_FRONTMATTER_BYTES = 64 * 1024
MAX_DEPTH = 32


class YamlError(ValueError):
    pass


@dataclass(frozen=True)
class Split:
    raw: str | None
    body: str
    error: str | None


@dataclass(frozen=True)
class Parsed:
    raw: str | None
    data: dict[str, Any] | None
    error: str | None
    body: str


def split(text: str) -> Split:
    """Split a Markdown file into raw frontmatter and body.

    Frontmatter counts only when `---` is the first line, as in Claude Code.
    """
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        return Split(raw=None, body=text, error=None)
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            return Split(raw="\n".join(lines[1:i]), body="\n".join(lines[i + 1 :]), error=None)
    return Split(raw=None, body=text, error="unterminated frontmatter")


def safe_load(text: str) -> Any:
    """Load YAML without tags, anchors or aliases, with depth and size limits."""
    if len(text.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise YamlError(f"frontmatter exceeds {MAX_FRONTMATTER_BYTES} bytes")
    depth = 0
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent):
                raise YamlError("YAML aliases are not allowed")
            if isinstance(event, yaml.NodeEvent) and event.anchor is not None:
                raise YamlError("YAML anchors are not allowed")
            if isinstance(event, yaml.ScalarEvent):
                if event.tag is not None and not any(event.implicit):
                    raise YamlError(f"explicit YAML tag {event.tag!r} is not allowed")
            elif isinstance(event, yaml.CollectionStartEvent):
                if event.tag is not None and not event.implicit:
                    raise YamlError(f"explicit YAML tag {event.tag!r} is not allowed")
                depth += 1
                if depth > MAX_DEPTH:
                    raise YamlError(f"YAML nesting exceeds depth {MAX_DEPTH}")
            elif isinstance(event, yaml.CollectionEndEvent):
                depth -= 1
        return to_jsonable(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise YamlError(_yaml_message(exc)) from None


def parse(text: str) -> Parsed:
    parts = split(text)
    if parts.raw is None:
        return Parsed(raw=None, data=None, error=parts.error, body=parts.body)
    try:
        data = safe_load(parts.raw)
    except YamlError as exc:
        return Parsed(raw=parts.raw, data=None, error=str(exc), body=parts.body)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return Parsed(
            raw=parts.raw, data=None, error="frontmatter is not a mapping", body=parts.body
        )
    return Parsed(raw=parts.raw, data=data, error=None, body=parts.body)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _yaml_message(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
    if mark is not None:
        return f"invalid YAML at line {mark.line + 1}: {problem}"
    return f"invalid YAML: {problem}"

"""Plain-text snapshot rendering for scripts and AI agents.

One `key: value` line per field, no colors, no tables, no wrapping. Every value
from the snapshot is sanitized. The layout mirrors the TUI detail panes.
"""

from __future__ import annotations

from pathlib import Path

from skill_atlas.model import Skill, Snapshot
from skill_atlas.sanitize import sanitize, sanitize_line


def _v(value: object) -> str:
    return sanitize_line(str(value)) if value not in (None, "") else "-"


def render(
    snapshot: Snapshot,
    path: Path | None = None,
    *,
    with_body: bool = False,
    kind: str | None = None,
) -> str:
    src, stats = snapshot.source, snapshot.stats
    lines = [
        f"repo: {_v(src.repo_key)}",
        f"source: {src.kind}",
        f"commit: {_v(src.commit_sha)}",
        f"commit_date: {_v(src.commit_date)}",
        f"ref: {_v(src.resolved_ref)}",
    ]
    if src.dirty is not None:
        lines.append(f"dirty: {'yes' if src.dirty else 'no'}")
    if snapshot.repo is not None:
        repo = snapshot.repo
        lines += [
            f"repo_description: {_v(repo.description)}",
            f"license: {_v(repo.license)}",
            f"stars: {_v(repo.stars)}",
        ]
    lines += [
        f"scanned_at: {snapshot.scan.scanned_at}",
        f"snapshot: {_v(path)}",
        f"stats: {stats.skills} skills, {stats.agents} agents, {stats.external} external, "
        f"{len(snapshot.plugins)} plugins",
    ]
    if stats.by_category:
        lines.append(
            "categories: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.by_category.items()))
        )
    for p in snapshot.plugins:
        lines.append(f"plugin: {_v(p.id)} name={_v(p.name)}")
    for w in snapshot.scan.warnings:
        lines.append(f"scan_warning: {_v(w)}")

    for skill in snapshot.skills:
        if kind and skill.kind != kind:
            continue
        lines.append("")
        lines += _skill_lines(skill, with_body)
    return "\n".join(lines) + "\n"


def _skill_lines(s: Skill, with_body: bool) -> list[str]:
    lines = [
        f"## {_v(s.name)}",
        f"id: {_v(s.id)}",
        f"kind: {s.kind}",
        f"type: {s.type} ({s.detector})",
        f"category: {_v(s.category)} ({_v(s.category_reason)})",
        f"compliance: {s.compliance.status}",
    ]
    lines += [f"violation: {v.code}: {_v(v.message)}" for v in s.compliance.violations]
    lines += [
        f"path: {_v(s.path or s.source_pointer)}",
        f"scope: {_v(s.scope)}",
        f"plugin: {_v(s.plugin_id)}",
        f"name_source: {_v(s.name_source)}",
        f"description: {_v(s.description)}",
        f"sha256: {_v(s.content_sha256)}",
        f"size: {_v(s.body_bytes)} bytes, {_v(s.body_lines)} lines",
    ]
    lines += [f"alias: {_v(a)}" for a in s.aliases]
    lines += [f"resource: {_v(r.path)} ({_v(r.bytes)} bytes)" for r in s.resources]
    lines += [f"warning: {_v(w)}" for w in s.warnings]
    if with_body and s.body is not None:
        lines.append("body:")
        lines += [f"  {line}" for line in sanitize(s.body).rstrip("\n").split("\n")]
    return lines

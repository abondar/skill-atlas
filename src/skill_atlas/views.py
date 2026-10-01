"""View logic shared by the TUI and the web UI."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from skill_atlas.model import ORG_REPO_STATUSES, OrgScan, Skill, Snapshot


def dup_key(s: Skill) -> tuple[str | None, ...]:
    """Identical copies share this key: same kind, name and content."""
    return (s.kind, s.name, s.content_sha256) if s.content_sha256 else ("id", s.id)


def same_skill(a: Skill, b: Skill) -> bool:
    """Same kind and name: one skill in several places, whatever the content."""
    return a.name is not None and (a.kind, a.name) == (b.kind, b.name)


def versions(skills: list[Skill], skill: Skill) -> list[Skill]:
    """Other entries of the same skill whose content differs (copies that drifted apart)."""
    key = dup_key(skill)
    seen: set[tuple[str | None, ...]] = {key}
    out = []
    for s in skills:
        k = dup_key(s)
        if k not in seen and same_skill(s, skill):
            seen.add(k)
            out.append(s)
    return out


def dedupe(skills: list[Skill]) -> list[tuple[Skill, list[Skill]]]:
    """Group identical copies (same kind, name and content) under the first occurrence.

    Symlinked copies are already folded into `Skill.aliases` by the scanner; this
    catches real copies, such as one skill committed to `.claude/skills` and
    `.agents/skills`. Display only: the snapshot keeps every entry.
    """
    groups: dict[tuple[str | None, ...], tuple[Skill, list[Skill]]] = {}
    for s in skills:
        key = dup_key(s)
        if key in groups:
            groups[key][1].append(s)
        else:
            groups[key] = (s, [])
    return list(groups.values())


def permalink(snap: Snapshot, skill: Skill) -> str | None:
    src = snap.source
    if src.kind != "github" or not skill.path or not src.commit_sha:
        return None
    return (
        f"https://{quote(src.host or 'github.com')}/{quote(src.owner or '')}/"
        f"{quote(src.name or '')}/blob/{src.commit_sha}/{quote(skill.path)}"
    )


def owner_key(repo_key: str) -> str:
    """`<host>/<owner>` of a repository key, the key of its organization scans."""
    return repo_key.rsplit("/", 1)[0] if "/" in repo_key else repo_key


def org_summary(report: OrgScan) -> dict[str, Any]:
    """The latest organization scan of an owner, as both UIs show it."""
    return {
        "owner_key": report.owner_key,
        "owner": report.owner,
        "owner_type": report.owner_type,
        "status": report.status,
        "message": report.message,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "duration_ms": report.duration_ms,
        "listed": report.listed,
        "skipped": report.skipped,
        "selected": len(report.repos),
        "counts": {s: n for s in ORG_REPO_STATUSES if (n := report.count(s))},
        "with_skills": report.with_skills,
        "api_requests": report.api_requests,
        "failures": [
            {"full_name": r.full_name, "error": r.error or ""}
            for r in report.repos
            if r.status == "failed"
        ],
    }


def org_counts_line(summary: dict[str, Any]) -> str:
    skipped = ", ".join(f"{n} {k}" for k, n in sorted(summary["skipped"].items()))
    counts = ", ".join(f"{n} {s}" for s, n in summary["counts"].items())
    return (
        f"{summary['listed']} listed"
        + (f" ({skipped} skipped)" if skipped else "")
        + f" · {summary['selected']} selected · {summary['with_skills']} with skills"
        + (f" · {counts}" if counts else "")
    )

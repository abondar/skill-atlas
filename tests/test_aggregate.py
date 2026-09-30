from __future__ import annotations

import json
import time
from pathlib import Path

from skill_atlas import aggregate, store
from skill_atlas.model import (
    Compliance,
    ScanInfo,
    ScanOptions,
    Skill,
    Snapshot,
    SourceInfo,
    Stats,
)


def make_snapshot(
    repo_key: str,
    scanned_at: str,
    skills: list[tuple[str, str]],
    sha: str = "a" * 40,
    node_id: str | None = None,
) -> Snapshot:
    items = [
        Skill(
            id=f"agent-skill:skills/{name}/SKILL.md",
            kind="skill",
            type="agent-skill",
            detector="D1",
            path=f"skills/{name}/SKILL.md",
            name=name,
            body="x",
            content_sha256=digest,
            compliance=Compliance(status="compliant"),
        )
        for name, digest in skills
    ]
    return Snapshot(
        scan=ScanInfo(
            id="01",
            scanned_at=scanned_at,
            tool_version="0",
            detectors_version=1,
            fetch_method="api",
            duration_ms=1,
            options=ScanOptions(),
        ),
        source=SourceInfo(kind="github", repo_key=repo_key, commit_sha=sha, node_id=node_id),
        skills=items,
        stats=Stats.of(items),
    )


def test_latest_snapshot_per_repo(tmp_path: Path) -> None:
    store.save(
        make_snapshot("github.com/o/a", "2026-01-01T00:00:00.000Z", [("pdf", "h1")]), tmp_path
    )
    store.save(
        make_snapshot(
            "github.com/o/a",
            "2026-02-01T00:00:00.000Z",
            [("pdf", "h1"), ("docx", "h2")],
            sha="b" * 40,
        ),
        tmp_path,
    )
    store.save(
        make_snapshot("github.com/o/b", "2026-01-15T00:00:00.000Z", [("pdf", "h3")]), tmp_path
    )
    db = aggregate.Store.open(tmp_path)
    rows = aggregate.repos(db)
    assert [(r.repo_key, r.skills, r.scans) for r in rows] == [
        ("github.com/o/a", 2, 2),
        ("github.com/o/b", 1, 1),
    ]
    assert rows[0].commit_sha == "b" * 40
    assert len(aggregate.skill_rows(db)) == 3
    assert len(aggregate.skill_rows(db, all_scans=True)) == 4


def test_group_by_name_and_hash(tmp_path: Path) -> None:
    store.save(
        make_snapshot("github.com/o/a", "2026-01-01T00:00:00.000Z", [("pdf", "h1")]), tmp_path
    )
    store.save(
        make_snapshot("github.com/o/b", "2026-01-01T00:00:00.000Z", [("pdf", "h1")]), tmp_path
    )
    store.save(
        make_snapshot("github.com/o/c", "2026-01-01T00:00:00.000Z", [("pdf", "h9")]), tmp_path
    )
    rows = aggregate.skill_rows(aggregate.Store.open(tmp_path))
    by_name = aggregate.group(rows, "name")
    assert [(g.key, len(g.repos), g.variants) for g in by_name] == [("pdf", 3, 2)]
    by_hash = aggregate.group(rows, "hash")
    assert [(g.key, g.repos) for g in by_hash] == [
        ("h1", ["github.com/o/a", "github.com/o/b"]),
        ("h9", ["github.com/o/c"]),
    ]


def test_renamed_repo_is_linked_by_node_id(tmp_path: Path) -> None:
    store.save(
        make_snapshot(
            "github.com/old/name", "2026-01-01T00:00:00.000Z", [("a", "1")], node_id="R_1"
        ),
        tmp_path,
    )
    store.save(
        make_snapshot(
            "github.com/new/name", "2026-02-01T00:00:00.000Z", [("a", "1")], node_id="R_1"
        ),
        tmp_path,
    )
    rows = aggregate.repos(aggregate.Store.open(tmp_path))
    assert [(r.repo_key, r.scans) for r in rows] == [("github.com/new/name", 2)]


def test_filters_and_find(tmp_path: Path) -> None:
    store.save(
        make_snapshot(
            "github.com/o/a",
            "2026-01-01T00:00:00.000Z",
            [("pdf-tools", "1"), ("docx", "2")],
            sha="c" * 40,
        ),
        tmp_path,
    )
    db = aggregate.Store.open(tmp_path)
    assert [r.skill.name for r in aggregate.skill_rows(db, name="PDF")] == ["pdf-tools"]
    assert aggregate.skill_rows(db, repo="github.com/o/zzz") == []
    assert db.find("GitHub.com/o/a", "ccc") is not None
    assert db.find("github.com/o/a", "ddd") is None


def test_unreadable_and_future_snapshots_are_skipped(tmp_path: Path) -> None:
    store.save(make_snapshot("github.com/o/a", "2026-01-01T00:00:00.000Z", [("a", "1")]), tmp_path)
    (tmp_path / "broken.json").write_text("{nope")
    (tmp_path / "future.json").write_text(json.dumps({"schema_version": 2}))
    db = aggregate.Store.open(tmp_path)
    assert len(db.snapshots) == 1
    assert len(db.warnings) == 2


def test_aggregating_1000_snapshots_is_fast(tmp_path: Path) -> None:
    for i in range(1000):
        snap = make_snapshot(
            f"github.com/o/r{i % 300}",
            f"2026-01-01T00:00:{i % 60:02d}.{i:03d}Z",
            [(f"s{j}", f"h{j}") for j in range(5)],
        )
        (tmp_path / f"{i:04d}.json").write_text(store.dumps(snap))
    started = time.monotonic()
    db = aggregate.Store.open(tmp_path)
    rows = aggregate.repos(db)
    aggregate.group(aggregate.skill_rows(db), "name")
    elapsed = time.monotonic() - started
    assert len(rows) == 300
    assert elapsed < 2.0, f"aggregation took {elapsed:.2f}s"

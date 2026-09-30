from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from textual.widgets import DataTable, Static

from skill_atlas import aggregate, store
from skill_atlas.model import Snapshot, Stats
from skill_atlas.tui import (
    AtlasApp,
    BrowserApp,
    ReposScreen,
    SnapshotScreen,
    body_view,
    overview,
    scan_view,
)
from skill_atlas.views import dedupe, permalink
from tests.conftest import make_tree, scan_path

EVIL = "\x1b]8;;https://evil.example\x1b\\click\x1b]8;;\x1b\\ \x1b[2J‮"


def evil_snapshot(tmp_path: Path) -> Snapshot:
    root = make_tree(
        tmp_path / "repo",
        {
            "skills/evil/SKILL.md": (
                f'---\nname: evil\ndescription: "{EVIL.encode("unicode_escape").decode()}"\n---\n'
                f"# Title\n\n[link](https://evil.example) {EVIL}\n"
            ),
            "skills/good/SKILL.md": "---\nname: good\ndescription: fine\n---\nok\n",
            ".claude/agents/a.md": "agent\n",
            "skills/broken/SKILL.md": b"\x00",
        },
    )
    return scan_path(root)


def render(renderable: object) -> str:
    console = Console(width=120, record=True, color_system="truecolor", force_terminal=True)
    console.print(renderable)
    return console.export_text(styles=True)


def test_rendered_views_have_no_injected_sequences(tmp_path: Path) -> None:
    snap = evil_snapshot(tmp_path)
    evil = next(s for s in snap.skills if s.name == "evil")
    assert "\x1b]8" in (evil.description or "")  # the snapshot keeps the original
    for renderable in (overview(snap, evil), body_view(evil)):
        out = render(renderable)
        assert "\x1b]" not in out  # no OSC, so no hyperlinks and no title changes
        assert "\x1b[2J" not in out
        assert "‮" not in out
    assert "https://evil.example" in render(body_view(evil))  # link shown as text


def test_permalink_only_for_github(tmp_path: Path) -> None:
    snap = evil_snapshot(tmp_path)
    assert permalink(snap, snap.skills[0]) is None
    github = snap.model_copy(
        update={
            "source": snap.source.model_copy(
                update={
                    "kind": "github",
                    "host": "github.com",
                    "owner": "o",
                    "name": "r",
                    "commit_sha": "a" * 40,
                }
            )
        }
    )
    assert permalink(github, github.skills[0]) == (
        f"https://github.com/o/r/blob/{'a' * 40}/{github.skills[0].path}"
    )


def test_app_filters_search_and_export(tmp_path: Path) -> None:
    snap = evil_snapshot(tmp_path)
    export = tmp_path / "export.json"

    async def scenario() -> None:
        app = AtlasApp(snap, None)
        async with app.run_test(size=(140, 40)) as pilot:
            screen = app.screen
            assert isinstance(screen, SnapshotScreen)
            table = screen.query_one("#table", DataTable)
            assert table.row_count == 3  # kind=skill by default
            await pilot.press("f")
            assert table.row_count == 1  # agents
            await pilot.press("f")
            assert table.row_count == 4  # all
            await pilot.press("c")
            assert table.row_count == 2  # compliant
            await pilot.press("c", "c", "c")
            await pilot.press("f")
            await pilot.press("slash", "g", "o", "o", "d", "enter")
            assert table.row_count == 1
            assert screen.current() is not None and screen.current().name == "good"  # type: ignore[union-attr]
            await pilot.press("escape")
            await pilot.press("escape")  # a root screen has nowhere to go back to
            assert app.screen is screen
            await pilot.press("3")
            body = screen.query_one("#body-view", Static)
            assert body.visual is not None
            await pilot.press("e")
            await pilot.press(*str(export))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(scenario())
    assert export.exists()
    assert '"schema_version": 1' in export.read_text()


def test_identical_copies_are_grouped(tmp_path: Path) -> None:
    same = "---\nname: pdf\ndescription: d\n---\nbody\n"
    root = make_tree(
        tmp_path / "repo",
        {
            ".claude/skills/pdf/SKILL.md": same,
            ".agents/skills/pdf/SKILL.md": same,
            "skills/pdf/SKILL.md": same.replace("body", "changed"),
        },
    )
    snap = scan_path(root)
    assert len(snap.skills) == 3  # the snapshot keeps every entry
    groups = dedupe(snap.skills)
    assert sorted(len(copies) for _, copies in groups) == [0, 1]

    async def scenario() -> None:
        app = AtlasApp(snap, None)
        async with app.run_test(size=(140, 40)) as pilot:
            screen = app.screen
            assert isinstance(screen, SnapshotScreen)
            table = screen.query_one("#table", DataTable)
            assert table.row_count == 2
            await pilot.press("d")
            assert table.row_count == 3
            await pilot.press("q")

    asyncio.run(scenario())
    first, copies = next(g for g in groups if g[1])
    assert "identical copy" in render(overview(snap, first, copies))


def test_scan_view_shows_how_the_snapshot_was_made(tmp_path: Path) -> None:
    snap = evil_snapshot(tmp_path)
    out = render(scan_view(snap, tmp_path / "snap.json"))
    for expected in ("scanned at", snap.scan.scanned_at, "fs", "excluded candidates", "snap.json"):
        assert expected in out


def saved_store(tmp_path: Path) -> Path:
    scans = tmp_path / "scans"
    snap = evil_snapshot(tmp_path)
    store.save(snap, scans)
    other = snap.model_copy(
        update={
            "source": snap.source.model_copy(update={"repo_key": "github.com/o/other"}),
            "skills": [],
            "stats": Stats(),
        }
    )
    store.save(other, scans)
    return scans


def test_browser_lists_repos_and_opens_snapshot(tmp_path: Path) -> None:
    scans = saved_store(tmp_path)

    async def scenario() -> None:
        app = BrowserApp(aggregate.Store.open(scans), scans)
        async with app.run_test(size=(140, 40)) as pilot:
            repos = app.screen
            assert isinstance(repos, ReposScreen)
            table = repos.query_one("#repos", DataTable)
            assert table.row_count == 2
            await pilot.press("slash", *"other", "enter")
            assert table.row_count == 1
            await pilot.press("escape")
            assert table.row_count == 2
            local = next(i for i, items in enumerate(repos.rows) if items[-1].snapshot.stats.skills)
            table.move_cursor(row=local)
            await pilot.press("enter")
            await pilot.pause()
            snapshot_screen = app.screen
            assert isinstance(snapshot_screen, SnapshotScreen)
            assert snapshot_screen.query_one("#table", DataTable).row_count == 3
            await pilot.press("6")
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is repos
            await pilot.press("q")

    asyncio.run(scenario())


def test_browser_on_empty_store(tmp_path: Path) -> None:
    scans = tmp_path / "scans"

    async def scenario() -> None:
        app = BrowserApp(aggregate.Store.open(scans), scans)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("enter")  # nothing to open, must not crash
            assert isinstance(app.screen, ReposScreen)
            await pilot.press("q")

    asyncio.run(scenario())


def test_browser_scans_a_new_repo(tmp_path: Path) -> None:
    scans = tmp_path / "scans"
    root = make_tree(
        tmp_path / "fresh", {".claude/skills/new/SKILL.md": "---\nname: new\ndescription: d\n---\n"}
    )

    async def scenario() -> None:
        app = BrowserApp(aggregate.Store.open(scans), scans)
        async with app.run_test(size=(140, 40)) as pilot:
            repos = app.screen
            assert isinstance(repos, ReposScreen)
            await pilot.press("n", *str(root), "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not repos.scanning
            snapshot_screen = app.screen
            assert isinstance(snapshot_screen, SnapshotScreen)  # opens the new snapshot
            assert [s.name for s in snapshot_screen.snapshot.skills] == ["new"]
            await pilot.press("escape")
            await pilot.pause()
            assert repos.query_one("#repos", DataTable).row_count == 1
            await pilot.press("q")

    asyncio.run(scenario())
    assert len(list(scans.glob("*.json"))) == 1


def test_browser_scan_failure_keeps_the_list(tmp_path: Path) -> None:
    scans = saved_store(tmp_path)

    async def scenario() -> None:
        app = BrowserApp(aggregate.Store.open(scans), scans)
        async with app.run_test(size=(140, 40)) as pilot:
            repos = app.screen
            assert isinstance(repos, ReposScreen)
            await pilot.press("n", *"not a target", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not repos.scanning
            assert repos.query_one("#repos", DataTable).row_count == 2
            assert app.screen is repos  # last: `is` narrows the type to the base Screen
            await pilot.press("q")

    asyncio.run(scenario())

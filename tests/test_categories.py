from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import DataTable

from skill_atlas import aggregate, store
from skill_atlas.categories import categorize, matches
from skill_atlas.tui import AtlasApp, SnapshotScreen
from tests.conftest import make_tree, scan_path

SKILL = "---\nname: {name}\ndescription: d\n---\nbody\n"


@pytest.mark.parametrize(
    ("path", "category"),
    [
        (".claude/skills/pdf/SKILL.md", "project"),
        (".agents/skills/cat/review/SKILL.md", "project"),
        # Folder names inside a load root are namespaces, not test data.
        (".claude/skills/tests/unit/SKILL.md", "project"),
        (".claude/skills/test-runner/SKILL.md", "project"),
        ("services/api/.claude/skills/db/SKILL.md", "subproject"),
        # koog: Gradle test source set with test resources.
        ("integration-tests/src/jvmTest/resources/skills/weather/SKILL.md", "test"),
        ("core/src/commonTest/resources/skills/x/SKILL.md", "test"),
        ("tests/fixtures/repo/.claude/skills/fake/SKILL.md", "test"),
        ("pkg/testdata/skills/x/SKILL.md", "test"),
        ("pkg/__tests__/skills/x/SKILL.md", "test"),
        ("e2e-tests/skills/x/SKILL.md", "test"),
        ("examples/basic/.claude/skills/x/SKILL.md", "example"),
        ("samples/skills/x/SKILL.md", "example"),
        ("templates/skills/x/SKILL.md", "template"),
        ("template/SKILL.md", "template"),  # anthropics/skills keeps its template here
        ("docs/guide/.claude/skills/x/SKILL.md", "docs"),
        # MPS: skills packaged into a product.
        ("plugins/mcp-tools/resources/jetbrains/mps/agents/mcp/skills/x/SKILL.md", "bundled"),
        ("app/src/main/resources/skills/x/SKILL.md", "bundled"),
        ("skills/pdf/SKILL.md", "catalog"),
        # Near misses: these words are not test markers.
        ("testing/skills/x/SKILL.md", "catalog"),
        ("latest/skills/x/SKILL.md", "catalog"),
        ("contest/skills/x/SKILL.md", "catalog"),
        ("spec/skills/x/SKILL.md", "catalog"),
    ],
)
def test_skill_paths(path: str, category: str) -> None:
    skill_dir = path.rsplit("/", 1)[0]
    container, _, own = skill_dir.rpartition("/")
    assert categorize(container, own_dir=own).category == category


def test_commands_and_plugins() -> None:
    assert categorize(".claude/commands/git").category == "project"
    assert categorize("fixtures/x/.claude/commands").category == "test"
    plugin = categorize("plugins/tools/skills", plugin_root="plugins/tools", plugin_id="tools")
    assert (plugin.category, plugin.reason) == ("plugin", "component of plugin tools")
    in_tests = categorize("tests/p/skills", plugin_root="tests/p", plugin_id="p")
    assert in_tests.category == "test"


def test_reason_names_the_matching_segment() -> None:
    result = categorize("integration-tests/src/jvmTest/resources/skills", own_dir="w")
    assert result.reason == 'path segment "integration-tests"'


def test_selectors() -> None:
    assert matches("project", "relevant") and not matches("test", "relevant")
    assert matches("test", "auxiliary") and not matches("plugin", "auxiliary")
    assert matches(None, "relevant")  # snapshots from detectors v1
    assert matches("docs", "docs") and matches("docs", "all")


def _repo(tmp_path: Path) -> Path:
    return make_tree(
        tmp_path / "repo",
        {
            ".claude/skills/real/SKILL.md": SKILL.format(name="real"),
            "src/jvmTest/resources/skills/fake/SKILL.md": SKILL.format(name="fake"),
            "examples/demo/SKILL.md": SKILL.format(name="demo"),
        },
    )


def test_tui_hides_auxiliary_by_default(tmp_path: Path) -> None:
    snap = scan_path(_repo(tmp_path))
    assert snap.stats.skills == 3
    assert snap.stats.by_category == {"example": 1, "project": 1, "test": 1}

    async def scenario() -> None:
        app = AtlasApp(snap, None)
        async with app.run_test(size=(160, 40)) as pilot:
            screen = app.screen
            assert isinstance(screen, SnapshotScreen)
            table = screen.query_one("#table", DataTable)
            assert table.row_count == 1
            assert screen.current() is not None and screen.current().name == "real"  # type: ignore[union-attr]
            await pilot.press("g")
            assert table.row_count == 2  # auxiliary
            await pilot.press("g")
            assert table.row_count == 3  # all
            await pilot.press("q")

    asyncio.run(scenario())


def test_old_snapshots_load_and_count_as_relevant(tmp_path: Path) -> None:
    snap = scan_path(_repo(tmp_path))
    data = json.loads(store.dumps(snap))
    data["scan"]["detectors_version"] = 1
    data["scan"]["options"]["include_fixtures"] = False
    for s in data["skills"]:
        del s["category"], s["category_reason"]
    del data["stats"]["by_category"], data["stats"]["external"]
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data))
    old = store.load(path)
    assert all(s.category is None for s in old.skills)
    rows = aggregate.skill_rows(aggregate.Store([store.Loaded(path, old)], []), category="relevant")
    assert len(rows) == 3


def test_old_detectors_version_is_not_a_cache_hit(tmp_path: Path) -> None:
    scans = tmp_path / "scans"
    snap = scan_path(_repo(tmp_path))
    src = snap.source.model_copy(update={"commit_sha": "a" * 40, "dirty": False})
    old = snap.model_copy(
        update={"source": src, "scan": snap.scan.model_copy(update={"detectors_version": 1})}
    )
    store.save(old, scans)
    assert store.find_cached(scans, src.repo_key, "a" * 40, 2, snap.scan.options) is None
    assert store.find_cached(scans, src.repo_key, "a" * 40, 1, snap.scan.options) is not None

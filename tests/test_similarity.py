from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest
from textual.widgets import Static

from skill_atlas import similarity
from skill_atlas.model import Snapshot
from skill_atlas.similarity import Index, terms, words
from skill_atlas.tui import AtlasApp, SnapshotScreen, similar_view
from tests.conftest import make_tree, scan_path
from tests.test_tui import render

GRADLE = """# Bump the Gradle version

1. Open `gradle/wrapper/gradle-wrapper.properties` and set `distributionUrl` to the new
   release of the Gradle wrapper.
2. Run `./gradlew wrapper --gradle-version <version>` twice so the wrapper jar updates.
3. Check the Kotlin Gradle plugin compatibility table before you raise the minimum
   supported Gradle version in the build logic.
4. Run the integration tests of the Gradle plugin with `./gradlew :plugin:functionalTest`.
"""
EXTRA = """
## Gradle API

Update the Gradle API dependency in `libs.versions.toml` and fix deprecation warnings
that the new API reports in the plugin sources.
"""
PDF = """# Work with PDF files

Use `pypdf` to merge pages, split documents, rotate pages and extract text from forms.
For scanned documents, run OCR with `pytesseract` first, then read the text layer.
Fill form fields with the `update_page_form_field_values` method and flatten the result.
"""
DOCKER = """# Build the Docker image

Build the service image with `docker build -t service .` from the repository root, tag it
with the commit hash and push it to the registry. Never push images from a dirty tree.
"""


def skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def snapshot(tmp_path: Path, files: dict[str, str]) -> Snapshot:
    return scan_path(make_tree(tmp_path / "repo", files))


@pytest.fixture
def snap(tmp_path: Path) -> Snapshot:
    return snapshot(
        tmp_path,
        {
            ".claude/skills/bump-gradle/SKILL.md": skill(
                "bump-gradle", "Bumps the Gradle wrapper version.", GRADLE
            ),
            # A copy that grew a section: a partial duplicate.
            ".claude/skills/bump-gradle-api/SKILL.md": skill(
                "bump-gradle-api", "Bumps the Gradle version and the Gradle API.", GRADLE + EXTRA
            ),
            ".claude/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
            ".agents/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
            ".claude/skills/docker/SKILL.md": skill("docker", "Builds Docker images.", DOCKER),
        },
    )


def test_words_split_identifiers_and_keep_acronym_plurals() -> None:
    assert words("GradleBuild snake_case APIs HTTPServer v2") == [
        "gradle",
        "build",
        "snake",
        "case",
        "apis",
        "http",
        "server",
        "v2",
    ]
    assert [stem for stem, _ in terms("the running tests")] == ["runn", "test"]


def test_partial_duplicate_is_found_with_direction(snap: Snapshot) -> None:
    index = Index(snap)
    base = next(s for s in snap.skills if s.name == "bump-gradle")
    [match] = index.similar(base)
    assert match.skill.name == "bump-gradle-api"
    assert match.level == "near-identical" and match.score >= 0.9
    # All of bump-gradle is inside bump-gradle-api, not the other way round.
    assert match.overlap_here == 1.0 and match.overlap_there < 0.9
    assert "gradle" in match.shared_terms


def test_score_is_symmetric_and_independent_of_order(snap: Snapshot) -> None:
    a = next(s for s in snap.skills if s.name == "bump-gradle")
    b = next(s for s in snap.skills if s.name == "bump-gradle-api")
    forward = Index(snap).similar(a)[0]
    backward = Index(snap).similar(b)[0]
    assert forward.score == backward.score and forward.topic == backward.topic
    shuffled = list(snap.skills)
    random.Random(1).shuffle(shuffled)
    again = Index(snap.model_copy(update={"skills": shuffled})).similar(a)[0]
    assert again.score == forward.score and again.shared_terms == forward.shared_terms


def test_unrelated_skills_and_identical_copies_are_not_listed(snap: Snapshot) -> None:
    index = Index(snap)
    pdf = next(s for s in snap.skills if s.name == "pdf")
    docker = next(s for s in snap.skills if s.name == "docker")
    assert index.similar(pdf) == []  # its identical copy is grouped, not "similar"
    assert index.similar(docker) == []
    everything = index.similar(docker, threshold=0.0)
    assert [m.skill.name for m in everything].count("pdf") == 1
    assert next(m for m in everything if m.skill.name == "pdf").copies == 1
    assert all(m.score < similarity.THRESHOLD for m in everything)


def test_same_topic_in_other_words_scores_by_vocabulary(tmp_path: Path) -> None:
    snap = snapshot(
        tmp_path,
        {
            "skills/a/SKILL.md": skill(
                "gradle-wrapper", "Update the Gradle wrapper.", "Set the Gradle wrapper version."
            ),
            "skills/b/SKILL.md": skill(
                "wrapper-upgrade", "Upgrade Gradle.", "Raise the wrapper to a new Gradle release."
            ),
            "skills/c/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
            "skills/d/SKILL.md": skill("docker", "Builds Docker images.", DOCKER),
        },
    )
    [match] = Index(snap).similar(next(s for s in snap.skills if s.name == "gradle-wrapper"))
    assert match.skill.name == "wrapper-upgrade"
    assert match.overlap_here == 0.0  # too short to compare passages
    assert match.score == match.topic


def test_percent_rounds_down() -> None:
    assert similarity.percent(0.996) == "99%" and similarity.percent(1.0) == "100%"
    assert similarity.percent(0.4) == "40%" and similarity.percent(0.29) == "29%"


def test_same_name_copy_that_drifted_is_marked(tmp_path: Path) -> None:
    snap = snapshot(
        tmp_path,
        {
            ".claude/skills/bump/SKILL.md": skill("bump", "Bumps Gradle.", GRADLE),
            ".agents/skills/bump/SKILL.md": skill("bump", "Bumps Gradle.", GRADLE + "Done.\n"),
            "skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
        },
    )
    first = next(s for s in snap.skills if s.path == ".agents/skills/bump/SKILL.md")
    out = render(similar_view(Index(snap), first))
    assert ".claude/skills/bump/SKILL.md" in out and "same name, different content" in out


def test_external_plugins_are_not_compared(tmp_path: Path) -> None:
    snap = snapshot(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": (
                '{"name": "m", "plugins": '
                '[{"name": "remote", "source": {"source": "github", "repo": "o/r"}}]}'
            ),
            "skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
        },
    )
    external = next(s for s in snap.skills if s.category == "external")
    index = Index(snap)
    assert index.similar(external) == []
    assert all(m.skill.category != "external" for m in index.similar(snap.skills[0], 0.0))


def test_tui_similar_tab(snap: Snapshot) -> None:
    base = next(s for s in snap.skills if s.name == "bump-gradle")
    out = render(similar_view(Index(snap), base))
    assert (
        "bump-gradle-api" in out
        and "near-identical" in out
        and "100% of this skill is in that one" in out
    )

    async def scenario() -> None:
        app = AtlasApp(snap, None)
        async with app.run_test(size=(160, 40)) as pilot:
            screen = app.screen
            assert isinstance(screen, SnapshotScreen)
            assert screen._index is None  # not built until the tab opens
            await pilot.press("7")
            await pilot.pause()
            assert screen._index is not None
            assert screen.query_one("#similar-view", Static).visual is not None
            await pilot.press("q")

    asyncio.run(scenario())

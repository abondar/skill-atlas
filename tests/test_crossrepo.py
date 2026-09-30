from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Static

from skill_atlas import aggregate, store
from skill_atlas.crossrepo import CrossIndex
from skill_atlas.model import Skill
from skill_atlas.similarity import THRESHOLD
from skill_atlas.tui import AtlasApp, SnapshotScreen, crossrepo_view
from skill_atlas.views import dup_key
from tests.conftest import make_tree, scan_path
from tests.test_similarity import DOCKER, GRADLE, PDF, skill
from tests.test_tui import render

REVIEW = """# Review a pull request

Read the diff of the pull request, check that every changed function has a test, look for
unhandled errors and leave one comment per finding with the file and the line number.
"""


@pytest.fixture
def scans(tmp_path: Path) -> Path:
    repos = {
        "upstream": {
            ".claude/skills/bump/SKILL.md": skill("bump", "Bumps Gradle.", GRADLE),
            ".claude/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
            ".claude/skills/code-review/SKILL.md": skill("code-review", "Reviews PRs.", REVIEW),
        },
        "fork": {
            # Copied from upstream, then edited.
            ".claude/skills/bump/SKILL.md": skill("bump", "Bumps Gradle.", GRADLE + "Done.\n"),
            ".claude/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files.", PDF),
            # Same name as in upstream, a different skill.
            ".claude/skills/code-review/SKILL.md": skill("code-review", "Docker.", DOCKER),
        },
        "third": {
            ".agents/skills/docs-pdf/SKILL.md": skill("docs-pdf", "PDF documents.", PDF),
        },
    }
    directory = tmp_path / "scans"
    for name, files in repos.items():
        store.save(scan_path(make_tree(tmp_path / name, files)), directory)
    return directory


def open_index(scans: Path) -> tuple[aggregate.Store, CrossIndex]:
    db = aggregate.Store.open(scans)
    return db, CrossIndex([(db.identity(i), i) for i in db.latest()])


def short(repo_key: str) -> str:
    """local/upstream-d5e49df2 -> upstream"""
    return repo_key.rsplit("/", 1)[1].rsplit("-", 1)[0]


def find(db: aggregate.Store, repo: str, name: str) -> tuple[Skill, set[str]]:
    for item in db.latest():
        if short(item.snapshot.source.repo_key) == repo:
            s = next(s for s in item.snapshot.skills if s.name == name)
            return s, {db.identity(item)}
    raise AssertionError(repo)


def test_fork_identical_copy_and_renamed_copy(scans: Path) -> None:
    db, index = open_index(scans)
    bump, own = find(db, "upstream", "bump")
    [fork] = index.matches(bump, own)
    assert short(fork.repo.repo_key) == "fork" and fork.status == "fork"
    assert fork.match.overlap_here == 1.0 and fork.match.overlap_there < 1.0

    pdf, own = find(db, "upstream", "pdf")
    found = {
        (short(c.repo.repo_key), c.match.skill.name, c.status) for c in index.matches(pdf, own)
    }
    assert found == {("fork", "pdf", "identical"), ("third", "docs-pdf", "fork")}


def test_same_name_is_not_the_same_skill(scans: Path) -> None:
    db, index = open_index(scans)
    review, own = find(db, "upstream", "code-review")
    assert index.matches(review, own) == []
    families = index.families()
    ours, theirs = find(db, "upstream", "code-review")[0], find(db, "fork", "code-review")[0]
    assert families[dup_key(ours)] != families[dup_key(theirs)]


def test_families_link_copies_across_repositories(scans: Path) -> None:
    db, index = open_index(scans)
    families = index.families()

    def family(repo: str, name: str) -> object:
        return families[dup_key(find(db, repo, name)[0])]

    assert family("upstream", "bump") == family("fork", "bump")
    assert family("upstream", "pdf") == family("fork", "pdf") == family("third", "docs-pdf")
    assert family("upstream", "pdf") != family("upstream", "bump")


def test_candidates_find_every_match_that_brute_force_finds(scans: Path) -> None:
    _, index = open_index(scans)
    corpus = index.corpus
    for key in corpus.groups:
        found = set(index.candidates(corpus.vectors[key], corpus.shingles[key]))
        for other in corpus.groups:
            if other != key and corpus.compare(key, other).score >= THRESHOLD:
                assert other in found, (key, other)


def test_skill_from_an_older_snapshot_is_compared_against_the_latest(scans: Path) -> None:
    db, index = open_index(scans)
    bump, own = find(db, "upstream", "bump")
    older = bump.model_copy(update={"content_sha256": "0" * 64, "body": GRADLE + "Old step.\n"})
    [fork] = index.matches(older, own)
    assert fork.status == "fork" and fork.match.score >= 0.9


def test_tui_other_repos_tab(scans: Path) -> None:
    db, index = open_index(scans)
    pdf, own = find(db, "upstream", "pdf")
    out = render(crossrepo_view(index, pdf, own))
    assert "identical" in out and "copied text" in out and "other name" in out
    assert "not in a store" in render(crossrepo_view(None, pdf, set()))
    item = next(i for i in db.latest() if short(i.snapshot.source.repo_key) == "upstream")

    async def scenario() -> None:
        app = AtlasApp(item.snapshot, item.path, scans)
        async with app.run_test(size=(160, 40)) as pilot:
            screen = app.screen
            assert isinstance(screen, SnapshotScreen)
            assert screen._cross is None  # not built until the tab opens
            await pilot.press("8")
            await pilot.pause()
            assert screen._cross is not None and screen._cross[1] == own
            assert screen.query_one("#repos-view", Static).visual is not None
            await pilot.press("q")

    asyncio.run(scenario())

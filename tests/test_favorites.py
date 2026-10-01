from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from skill_atlas import aggregate, favorites, store
from skill_atlas.errors import StoreError
from skill_atlas.favorites import Favorites, RepoRef, SkillRef
from skill_atlas.model import Snapshot
from skill_atlas.views import pin_target, pinned_first
from tests.conftest import make_tree, scan_path


def snapshot(tmp_path: Path, name: str = "repo") -> Snapshot:
    root = make_tree(
        tmp_path / name,
        {
            ".claude/skills/a/SKILL.md": "---\nname: a\ndescription: d\n---\nA\n",
            ".claude/skills/b/SKILL.md": "---\nname: b\ndescription: d\n---\nB\n",
        },
    )
    return scan_path(root)


def renamed(snap: Snapshot, repo_key: str, node_id: str | None) -> Snapshot:
    source = snap.source.model_copy(update={"repo_key": repo_key, "node_id": node_id})
    return snap.model_copy(update={"source": source})


def test_pinned_first_keeps_the_order_within_each_part() -> None:
    items = ["d", "a", "c", "b"]
    assert pinned_first(items, lambda x: x in {"c", "a"}) == ["a", "c", "d", "b"]
    assert pin_target([False, False]) is True
    assert pin_target([False, True]) is False  # a partly pinned row unpins every entry


def test_missing_file_means_no_pins(tmp_path: Path) -> None:
    assert favorites.load(tmp_path / "favorites.json") == Favorites()


def test_round_trip_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "home" / "favorites.json"
    fav = (
        Favorites()
        .with_repo(RepoRef("github.com/o/b", "R_b"), True)
        .with_repo(RepoRef("github.com/o/a"), True)
        .with_skills([SkillRef(RepoRef("local/x"), "agent-skill:s/SKILL.md")], True)
    )
    favorites.save(path, fav)  # creates the directory
    assert favorites.load(path) == fav
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert [r["repo_key"] for r in data["repos"]] == ["github.com/o/a", "github.com/o/b"]
    assert data["skills"] == [
        {"id": "agent-skill:s/SKILL.md", "node_id": None, "repo_key": "local/x"}
    ]
    favorites.save(path, favorites.load(path))
    assert json.loads(path.read_text()) == data
    assert [p.name for p in path.parent.iterdir()] == ["favorites.json"]  # no temporary files


def test_write_replaces_the_file_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "favorites.json"
    favorites.save(path, Favorites().with_repo(RepoRef("a"), True))

    def fail(self: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(StoreError, match="disk full"):
        favorites.save(path, Favorites())
    monkeypatch.undo()
    assert favorites.load(path).repos == {RepoRef("a")}  # the old file is intact
    assert [p.name for p in tmp_path.iterdir()] == ["favorites.json"]


def test_broken_file_is_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "favorites.json"
    path.write_text("{not json")
    warnings: list[str] = []
    assert favorites.load_or_empty(path, warnings) == Favorites()  # display: no pins
    assert warnings and "invalid favorites file" in warnings[0]
    with pytest.raises(StoreError):
        favorites.update(path, lambda f: f.with_repo(RepoRef("a"), True))
    assert path.read_text() == "{not json"
    path.write_text(json.dumps({"version": 99}))
    with pytest.raises(StoreError, match="unsupported favorites version"):
        favorites.load(path)


def test_unreadable_file_is_a_store_error(tmp_path: Path) -> None:
    path = tmp_path / "favorites.json"
    path.mkdir()  # reading a directory fails on every platform, also as root
    with pytest.raises(StoreError, match="cannot read"):
        favorites.load(path)


def test_concurrent_updates_keep_every_pin(tmp_path: Path) -> None:
    path = tmp_path / "favorites.json"
    threads = [
        threading.Thread(
            target=favorites.update, args=(path, lambda f, i=i: f.with_repo(RepoRef(f"r{i}"), True))
        )
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(favorites.load(path).repos) == 20


def test_repo_pin_follows_a_rename_through_node_id(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    old = renamed(snap, "github.com/o/old", "R_1")
    new = renamed(snap, "github.com/o/new", "R_1")
    fav = Favorites().with_repo(RepoRef.of(old.source), True)
    assert fav.repo_pinned(new.source)
    assert not fav.repo_pinned(renamed(snap, "github.com/o/other", "R_2").source)
    skill = snap.skills[0].id
    fav = fav.with_skills([SkillRef(RepoRef.of(old.source), skill)], True)
    assert fav.skill_pinned(new.source, skill)
    assert not fav.skill_pinned(new.source, snap.skills[1].id)
    # Unpinning under the new name removes the pin made under the old one.
    fav = fav.with_repo(RepoRef.of(new.source), False)
    fav = fav.with_skills([SkillRef(RepoRef.of(new.source), skill)], False)
    assert fav == Favorites()


def test_moved_skill_loses_its_pin(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    a = next(s for s in snap.skills if s.name == "a")
    fav = Favorites().with_skills([SkillRef(RepoRef.of(snap.source), a.id)], True)
    moved = a.id.replace(".claude/skills", ".agents/skills")
    assert fav.skill_pinned(snap.source, a.id)
    assert not fav.skill_pinned(snap.source, moved)


def test_aggregation_puts_pins_first(tmp_path: Path) -> None:
    scans = tmp_path / "scans"
    first = snapshot(tmp_path, "first")
    store.save(first, scans)
    second = scan_path(make_tree(tmp_path / "second", {"skills/z/SKILL.md": "---\nname: z\n---\n"}))
    store.save(second, scans)
    db = aggregate.Store.open(scans)
    keys = [r.repo_key for r in aggregate.repos(db)]
    assert keys == [second.source.repo_key, first.source.repo_key]  # latest scan first

    b = next(s for s in first.skills if s.name == "b")
    fav = (
        Favorites()
        .with_repo(RepoRef.of(first.source), True)
        .with_skills([SkillRef(RepoRef.of(first.source), b.id)], True)
    )
    rows = aggregate.repos(db, favorites=fav)
    assert [(r.repo_key, r.pinned) for r in rows] == [
        (first.source.repo_key, True),
        (second.source.repo_key, False),
    ]
    assert aggregate.repos(db, "name", fav)[0].pinned
    skills = aggregate.skill_rows(db, category="all", favorites=fav)
    assert (skills[0].skill.name, skills[0].pinned) == ("b", True)
    assert sum(r.pinned for r in skills) == 1
    groups = aggregate.group(skills, "name")
    assert groups[0].key == "b" and groups[0].pinned

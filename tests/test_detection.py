from __future__ import annotations

from pathlib import Path

import pytest

from skill_atlas.model import Skill, Snapshot
from tests.cases import CASES
from tests.conftest import Link, assert_golden, make_tree, normalized, scan_path


def _scan(tmp_path: Path, case: str, **kwargs: object) -> Snapshot:
    root = make_tree(tmp_path / case, CASES[case])
    return scan_path(root, **kwargs)  # type: ignore[arg-type]


def _by_path(snap: Snapshot) -> dict[str, Skill]:
    return {s.path or s.id: s for s in snap.skills}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden(tmp_path: Path, case: str) -> None:
    assert_golden(case, _scan(tmp_path, case))


def test_scan_is_deterministic(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "r", CASES["d3_d4_plugin"])
    assert normalized(scan_path(root)) == normalized(scan_path(root))


def test_agent_skill_scope_and_resources(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "d1_agent_skill"))
    pdf = skills[".claude/skills/pdf/SKILL.md"]
    assert pdf.scope == ".claude/skills"
    assert pdf.compliance.status == "compliant"
    assert [r.path for r in pdf.resources] == [
        ".claude/skills/pdf/references/REFERENCE.md",
        ".claude/skills/pdf/scripts/extract.py",
    ]
    assert all(r.git_blob_sha for r in pdf.resources)
    assert skills[".agents/skills/cat/review/SKILL.md"].scope == ".agents/skills"
    assert skills["skills/unscoped/SKILL.md"].scope == "unscoped"
    assert "README.md" not in skills


def test_claude_commands_take_name_from_path(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "d2_claude_command"))
    deploy = skills[".claude/commands/deploy.md"]
    assert (deploy.name, deploy.description, deploy.description_source) == (
        "deploy",
        "Deploy the app",
        "frontmatter",
    )
    commit = skills[".claude/commands/git/commit.md"]
    assert (commit.name, commit.description_source) == ("git:commit", "body")


def test_plugin_manifest_paths(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "d3_d4_plugin")
    skills = _by_path(snap)
    status = skills["plugins/tools/cmds/status.md"]
    assert (status.type, status.name, status.plugin_id) == (
        "plugin-command",
        "status",
        "plugins/tools",
    )
    assert "plugins/tools/commands/ignored.md" not in skills
    inline = next(s for s in snap.skills if s.type == "plugin-command-inline")
    assert inline.path is None
    assert inline.body == "Explain the plugin."
    assert inline.source_pointer == "plugins/tools/.claude-plugin/plugin.json#/commands/about"
    assert skills["plugins/tools/agents/reviewer.md"].kind == "agent"
    assert skills["plugins/tools/skills/lint/SKILL.md"].scope == "plugin"
    assert skills["plugins/tools/extra/fmt/SKILL.md"].plugin_id == "plugins/tools"


def test_marketplace_entries_are_separate_plugins(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "marketplace")
    ids = [p.id for p in snap.plugins]
    assert ids == [".#art", ".#docs", ".claude-plugin/marketplace.json#remote", "plugins/local"]
    remote = snap.plugins[2]
    assert remote.remote_source == {"source": "github", "repo": "o/r"}
    skills = _by_path(snap)
    assert skills["skills/docx/SKILL.md"].plugin_id == ".#docs"
    assert skills["skills/art/SKILL.md"].plugin_id == ".#art"
    assert skills["plugins/local/commands/hello.md"].type == "plugin-command"


def test_copilot_files(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "d5_d7_copilot"))
    assert set(skills) == {".github/prompts/review.prompt.md", ".github/agents/planner.agent.md"}
    assert skills[".github/prompts/review.prompt.md"].name == "review"
    assert skills[".github/agents/planner.agent.md"].kind == "agent"


def test_claude_agents(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "d6_claude_agent"))
    assert skills[".claude/agents/reviewer.md"].name == "code-reviewer"
    assert skills[".claude/agents/team/qa.md"].name == "team:qa"


def test_symlinks_dedupe_and_escape(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "symlinks")
    assert len(snap.skills) == 1
    shared = snap.skills[0]
    assert shared.path == ".agents/skills/shared/SKILL.md"
    assert shared.aliases == [".claude/skills/shared/SKILL.md"]
    warnings = " ".join(snap.scan.warnings)
    assert "dangling symlink" in warnings
    # `outside` is not a relevant name, so it is not resolved at all.
    assert "outside:" not in warnings


def test_symlink_outside_repo_is_rejected(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "r", {"skills/x": Link("/etc")})
    snap = scan_path(root)
    assert snap.skills == []
    assert any("outside the repository" in w for w in snap.scan.warnings)


def test_compliance_rules(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "compliance"))

    def codes(path: str) -> list[str]:
        return [v.code for v in skills[path].compliance.violations]

    assert codes("skills/bad-yaml/SKILL.md") == ["frontmatter-invalid"]
    assert codes("skills/no-frontmatter/SKILL.md") == ["frontmatter-missing"]
    nf = skills["skills/no-frontmatter/SKILL.md"]
    assert (nf.name, nf.name_source, nf.description) == (
        "no-frontmatter",
        "path",
        "Just a body line",
    )
    assert codes("skills/mismatch/SKILL.md") == ["name-dir-mismatch"]
    assert codes("skills/Upper/SKILL.md") == ["name-invalid"]
    assert codes("skills/long/SKILL.md") == ["description-too-long"]
    assert codes("skills/meta/SKILL.md") == ["metadata-invalid"]
    assert codes("skills/unterminated/SKILL.md") == ["frontmatter-missing"]
    alias = skills["skills/alias/SKILL.md"]
    assert alias.frontmatter_error == "YAML anchors are not allowed"
    assert all(s.compliance.status == "loadable" for s in skills.values())
    assert "skills/lower/skill.md" not in skills


def test_case_variant_warning(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "compliance")
    assert any("skills/lower/skill.md" in w for w in snap.scan.warnings)


def test_nested_skills_are_separate(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "nested"))
    outer = skills["skills/outer/SKILL.md"]
    assert [r.path for r in outer.resources] == ["skills/outer/ref.md"]
    inner = skills["skills/outer/inner/SKILL.md"]
    assert [r.path for r in inner.resources] == ["skills/outer/inner/data.txt"]


def test_noise_is_excluded(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "noise")
    assert [s.path for s in snap.skills] == ["skills/real/SKILL.md"]
    assert snap.scan.excluded_candidates == 1  # the fixture; node_modules is not counted


def test_include_fixtures_and_globs(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "noise", CASES["noise"])
    with_fixtures = scan_path(root, include_fixtures=True)
    assert len(with_fixtures.skills) == 2
    excluded = scan_path(root, exclude=["skills/*"])
    assert excluded.skills == []
    assert excluded.scan.excluded_candidates == 2
    included = scan_path(root, include=["tests/fixtures/skills/fake"])
    assert len(included.skills) == 2


def test_path_option_limits_scan(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "d1_agent_skill", path=".claude")
    assert [s.path for s in snap.skills] == [".claude/skills/pdf/SKILL.md"]
    assert snap.scan.options.path == ".claude"


def test_broken_files(tmp_path: Path) -> None:
    skills = _by_path(_scan(tmp_path, "broken"))
    codes = {p: s.compliance.violations[0].code for p, s in skills.items()}
    assert codes == {
        "skills/lfs/SKILL.md": "lfs-pointer",
        "skills/big/SKILL.md": "too-large",
        "skills/binary/SKILL.md": "binary",
        "skills/latin1/SKILL.md": "encoding",
    }
    assert all(s.compliance.status == "broken" and s.body is None for s in skills.values())


def test_openai_yaml_extras(tmp_path: Path) -> None:
    ui = _by_path(_scan(tmp_path, "openai_yaml"))["skills/ui/SKILL.md"]
    assert ui.extras == {"openai_yaml": {"interface": {"display_name": "UI Helper"}}}


def test_empty_repo_is_not_an_error(tmp_path: Path) -> None:
    snap = _scan(tmp_path, "empty")
    assert snap.skills == []
    assert snap.stats.skills == 0

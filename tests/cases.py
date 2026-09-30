"""Fixture repositories for detection tests (SPEC section 12, M1)."""

from __future__ import annotations

import json

from tests.conftest import FileSpec, Link


def skill(name: str, description: str = "Does a thing. Use when needed.", extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n# {name}\n\nSteps.\n"


CASES: dict[str, FileSpec] = {
    "d1_agent_skill": {
        ".claude/skills/pdf/SKILL.md": skill("pdf", extra="license: MIT\n"),
        ".claude/skills/pdf/scripts/extract.py": "print('x')\n",
        ".claude/skills/pdf/references/REFERENCE.md": "# Reference\n",
        ".agents/skills/cat/review/SKILL.md": skill("review"),
        "skills/unscoped/SKILL.md": skill("unscoped"),
        "README.md": "# repo\n",
    },
    "d2_claude_command": {
        ".claude/commands/deploy.md": "---\ndescription: Deploy the app\n---\nRun deploy.\n",
        ".claude/commands/git/commit.md": "Commit staged changes\n",
    },
    "d3_d4_plugin": {
        "plugins/tools/.claude-plugin/plugin.json": json.dumps(
            {
                "name": "tools",
                "version": "1.0.0",
                "description": "Tooling",
                "commands": {
                    "status": {"source": "./cmds/status.md", "description": "Show status"},
                    "about": {"content": "Explain the plugin.", "description": "About"},
                },
                "agents": ["./agents/reviewer.md"],
                "skills": ["./extra/"],
            }
        ),
        "plugins/tools/cmds/status.md": "Print the status.\n",
        "plugins/tools/commands/ignored.md": "Default dir is replaced by the manifest.\n",
        "plugins/tools/agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews\n---\nx\n",
        "plugins/tools/skills/lint/SKILL.md": skill("lint"),
        "plugins/tools/extra/fmt/SKILL.md": skill("fmt"),
    },
    "marketplace": {
        ".claude-plugin/marketplace.json": json.dumps(
            {
                "name": "m",
                "plugins": [
                    {"name": "docs", "source": "./", "skills": ["./skills/docx"]},
                    {"name": "art", "source": "./", "skills": ["./skills/art"]},
                    {"name": "local", "source": "./plugins/local"},
                    {"name": "remote", "source": {"source": "github", "repo": "o/r"}},
                ],
            }
        ),
        "skills/docx/SKILL.md": skill("docx"),
        "skills/art/SKILL.md": skill("art"),
        "plugins/local/commands/hello.md": "Say hello.\n",
    },
    "d5_d7_copilot": {
        ".github/prompts/review.prompt.md": "---\ndescription: Review code\n---\nReview.\n",
        ".github/agents/planner.agent.md": "---\ndescription: Plans work\n---\nPlan.\n",
        ".github/copilot-instructions.md": "Always-on, not a skill.\n",
        ".github/instructions/py.instructions.md": "---\napplyTo: '**/*.py'\n---\nx\n",
    },
    "d6_claude_agent": {
        ".claude/agents/reviewer.md": (
            "---\nname: code-reviewer\ndescription: Reviews code\n---\nx\n"
        ),
        ".claude/agents/team/qa.md": "No frontmatter agent\n",
    },
    "symlinks": {
        ".agents/skills/shared/SKILL.md": skill("shared"),
        ".agents/skills/shared/notes.md": "notes\n",
        ".claude/skills": Link("../.agents/skills"),
        "outside": Link("../../etc"),
        "skills/dangling": Link("nowhere"),
    },
    "compliance": {
        "skills/bad-yaml/SKILL.md": "---\nname: [unclosed\n---\nbody\n",
        "skills/no-frontmatter/SKILL.md": "Just a body line\n\nMore.\n",
        "skills/mismatch/SKILL.md": skill("other-name"),
        "skills/Upper/SKILL.md": skill("Upper"),
        "skills/long/SKILL.md": skill("long", description="x" * 1100),
        "skills/meta/SKILL.md": skill("meta", extra="metadata:\n  version: 1.0\n"),
        "skills/unterminated/SKILL.md": "---\nname: unterminated\n",
        "skills/alias/SKILL.md": "---\na: &x 1\nb: *x\n---\nx\n",
        "skills/lower/skill.md": skill("lower"),
    },
    "nested": {
        "skills/outer/SKILL.md": skill("outer"),
        "skills/outer/ref.md": "ref\n",
        "skills/outer/inner/SKILL.md": skill("inner"),
        "skills/outer/inner/data.txt": "data\n",
    },
    "noise": {
        "node_modules/pkg/skills/x/SKILL.md": skill("x"),
        "tests/fixtures/skills/fake/SKILL.md": skill("fake"),
        "AGENTS.md": "# agents\n",
        "CLAUDE.md": "# claude\n",
        ".cursor/rules/style.mdc": "---\nalwaysApply: true\n---\nx\n",
        "docs/guide.md": "# guide\n",
        "skills/real/SKILL.md": skill("real"),
    },
    "broken": {
        "skills/lfs/SKILL.md": (
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
        ),
        "skills/big/SKILL.md": "x" * (1024 * 1024 + 1),
        "skills/binary/SKILL.md": b"---\nname: binary\n---\n\x00\x01",
        "skills/latin1/SKILL.md": b"caf\xe9\n",
    },
    "openai_yaml": {
        "skills/ui/SKILL.md": skill("ui"),
        "skills/ui/agents/openai.yaml": "interface:\n  display_name: UI Helper\n",
    },
    "empty": {
        "README.md": "# nothing here\n",
    },
}

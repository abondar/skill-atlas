"""Skill categories: why an entry is in the repository (SPEC section 5.6).

`type` says which format and loader an entry uses. `category` says what the entry is
for, so a reader can split the skills an agent loads from test data, examples and
docs at a glance. Rules look at the path only; the first matching rule wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from skill_atlas import paths

Category = Literal[
    "project",
    "subproject",
    "plugin",
    "external",
    "bundled",
    "catalog",
    "test",
    "example",
    "template",
    "docs",
]

RELEVANT: tuple[Category, ...] = (
    "project",
    "subproject",
    "plugin",
    "external",
    "bundled",
    "catalog",
)
AUXILIARY: tuple[Category, ...] = ("test", "example", "template", "docs")
SELECTORS = ("relevant", "auxiliary", "all", *RELEVANT, *AUXILIARY)


def matches(category: str | None, selector: str) -> bool:
    """Match a category against `relevant`, `auxiliary`, `all` or one category name.

    Entries from snapshots before detectors version 2 have no category and count as
    relevant: before v2 the scanner dropped the usual fixture directories.
    """
    if selector == "all":
        return True
    if selector == "relevant":
        return category is None or category in RELEVANT
    if selector == "auxiliary":
        return category in AUXILIARY
    return category == selector


# Directory pairs that agents load entries from; entries under them may use any
# folder names, so only segments *before* the pair count for auxiliary rules.
LOAD_ROOTS = frozenset(
    {
        (".agents", "skills"),
        (".claude", "skills"),
        (".cursor", "skills"),
        (".codex", "skills"),
        (".github", "skills"),
        (".opencode", "skills"),
        (".claude", "commands"),
        (".claude", "agents"),
        (".github", "prompts"),
        (".github", "agents"),
    }
)

_TEST_RE = re.compile(
    r"^(?:tests?|__tests__|testdata|test_?data|fixtures|__fixtures__)$"
    r"|^tests?[-_]"  # test-data, test_resources, tests-e2e
    r"|[-_]tests?$"  # integration-tests, e2e_test
    r"|^[a-z][A-Za-z0-9]*Test$"  # Gradle source sets: jvmTest, commonTest, androidUnitTest
)
_EXAMPLE_RE = re.compile(r"^(?:examples?|samples?|demos?|showcase)(?:$|[-_])")
_TEMPLATE_RE = re.compile(r"^(?:templates?|skeletons?|boilerplates?|scaffolds?)$")
_DOCS_RE = re.compile(r"^(?:docs?|documentation)$")
_BUNDLED = frozenset({"resources", "assets"})

_AUX_RULES: tuple[tuple[Category, re.Pattern[str]], ...] = (
    ("test", _TEST_RE),
    ("example", _EXAMPLE_RE),
    ("template", _TEMPLATE_RE),
    ("docs", _DOCS_RE),
)


@dataclass(frozen=True)
class Result:
    category: Category
    reason: str


def load_root(container: str) -> tuple[list[str], str] | None:
    """Split `container` at the first agent load root: (segments before it, root path)."""
    segs = paths.segments(container)
    for i in range(len(segs) - 1):
        if (segs[i], segs[i + 1]) in LOAD_ROOTS:
            return segs[:i], "/".join(segs[: i + 2])
    return None


def _auxiliary(segs: list[str]) -> Result | None:
    for category, pattern in _AUX_RULES:
        for seg in segs:
            if pattern.search(seg):
                return Result(category, f'path segment "{seg}"')
    return None


def categorize(
    container: str,
    *,
    own_dir: str | None = None,
    plugin_root: str | None = None,
    plugin_id: str | None = None,
    external: bool = False,
) -> Result:
    """Categorize one entry.

    `container` is the directory that holds the entry: the parent of a skill
    directory, the directory of a command or agent file, or the directory of the
    manifest for inline commands. `own_dir` is the skill directory name; only the
    template rule looks at it, since a skill named `test-runner` is not test data.
    """
    if external:
        return Result("external", "marketplace entry with a remote source; content not scanned")
    # Auxiliary rules first: a `.claude/skills` inside `tests/fixtures/` is a fixture.
    root = load_root(container)
    if plugin_id is not None:
        scope_segs = paths.segments(plugin_root or "")
    elif root is not None:
        scope_segs = root[0]
    else:
        scope_segs = paths.segments(container)
    if (aux := _auxiliary(scope_segs)) is not None:
        return aux
    if plugin_id is not None:
        return Result("plugin", f"component of plugin {plugin_id}")
    if root is not None:
        before, root_path = root
        if not before:
            return Result("project", f"agent load root {root_path} at the repository root")
        return Result("subproject", f"agent load root {root_path}")
    if own_dir is not None and _TEMPLATE_RE.search(own_dir):
        return Result("template", f'skill directory "{own_dir}"')
    for seg in scope_segs:
        if seg in _BUNDLED:
            return Result("bundled", f'path segment "{seg}"')
    return Result("catalog", "outside agent load roots and plugins")

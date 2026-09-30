# skill-atlas

`skill-atlas` finds AI agent skills in a repository, shows them in a TUI and saves an
immutable JSON snapshot tied to the repository and commit. Snapshots accumulate in a local
store; `repos` and `skills` aggregate them. See [SPEC.md](SPEC.md) for the full specification.

## Install

```bash
uv sync
uv run skill-atlas --help
```

## Scan

```bash
skill-atlas scan anthropics/skills                 # opens the TUI, saves a snapshot
skill-atlas scan https://github.com/o/r/tree/main/plugins
skill-atlas scan . --ref v1.2.0                    # local git revision
skill-atlas scan ./repo --no-tui                   # save and print a summary table
skill-atlas scan o/r --plain                       # plain-text report for scripts and agents
skill-atlas scan o/r --plain --with-body           # ... including skill bodies
skill-atlas scan o/r --no-save --output -          # snapshot JSON to stdout
```

Progress goes to stderr; `--quiet` turns it off. Large repositories where the GitHub
Trees API truncates the listing fall back to a blobless `git fetch` of one commit.

Private repositories need `GITHUB_TOKEN`, `GH_TOKEN` or a logged-in `gh`.
Scanning the same clean commit again reuses the stored snapshot; `--force` writes a new one.
Finding zero skills is a success (exit code 0).

## What counts as a skill

| Detected | Kind |
| - | - |
| `**/SKILL.md` (Agent Skills standard) | skill |
| `.claude/commands/**/*.md`, plugin `commands/`, inline commands in `plugin.json` | skill |
| `.github/prompts/*.prompt.md` | skill |
| `.claude/agents/**/*.md`, plugin `agents/`, `.github/agents/*.agent.md` | agent |

`AGENTS.md`, `CLAUDE.md`, Copilot instructions and Cursor rules are always-on context,
not skills, and are ignored. Marketplace plugins with a remote source appear as
`external` placeholders; their content is not scanned.

Each entry also gets a `category` that says why it is in the repository:

| Relevant | Auxiliary (hidden by default) |
| - | - |
| `project`, `subproject` (agent load roots), `plugin`, `external`, `bundled` (`resources/`, `assets/`), `catalog` | `test` (`tests/`, `fixtures/`, `jvmTest/`, `*-tests/`), `example`, `template`, `docs` |

`category_reason` names the rule that matched. Test data is kept in the snapshot, not
dropped, so `g` in the TUI or `skills --category test` shows it without a new scan.

## Store and aggregation

Snapshots live in `$SKILL_ATLAS_HOME/scans/` (default `~/.local/share/skill-atlas/scans/`),
one flat, time-sortable file per scan.

```bash
skill-atlas                                        # TUI: repositories -> snapshot -> skills; n scans a new repo
skill-atlas repos                                  # known repositories, latest scan each
skill-atlas skills --category test                 # relevant (default), auxiliary, all, or one category
skill-atlas skills --group-by name                 # same skill name across repositories
skill-atlas skills --group-by hash                 # identical copies across repositories
skill-atlas show github.com/anthropics/skills      # TUI on a saved snapshot, offline
skill-atlas show github.com/anthropics/skills --plain
```

## TUI keys

`j/k` move · `/` search · `g` category · `f` kind · `t` type · `c` compliance · `d` group copies ·
`1`–`6` tabs (6 = how the snapshot was made) · `o` open on GitHub · `e` export ·
`Enter` open repository · `n` scan a new repository · `Esc` back · `q` quit

Identical copies of one skill in several directories (same kind, name and content)
show as one row with `+N` in the `copies` column. Snapshots keep every copy.

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
UPDATE_GOLDEN=1 uv run pytest tests/test_detection.py   # regenerate golden snapshots
```

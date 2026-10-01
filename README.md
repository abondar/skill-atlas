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

## Scan an organization

```bash
skill-atlas scan --org anthropics                  # every repository of an organization or user
skill-atlas scan https://github.com/anthropics     # the same
skill-atlas scan --org acme --include-archived --include-forks --match 'skills-*' --limit 50
skill-atlas scan --org acme --wait                 # wait for the API rate limit to reset
skill-atlas scan --org acme --plain                # one line per repository
```

Each repository gets its own snapshot as soon as it is scanned, and a report of the whole
run goes to `$SKILL_ATLAS_HOME/orgs/`. Archived repositories and forks are skipped unless
asked for. A repository whose `pushed_at` matches its latest snapshot costs no API request;
a changed one costs two (commit and tree), since file contents come from
`raw.githubusercontent.com`. A first pass over 549 HashiCorp repositories took 1111
requests; a second pass over an unchanged organization costs only the listing.

A failed repository does not stop the scan: it is listed in the report and the exit code is
6. On a rate limit the scan stops with exit code 4 (or waits with `--wait`); run the same
command again to continue, saved snapshots are reused. `--jobs N` (default 4, at most 8)
scans repositories in parallel.

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

## Web UI

`skill-atlas --web` opens a web app on `127.0.0.1`: a dashboard of repository cards, a
repository page with skill cards, relevance and category filters, grouped copies, a skill
side panel (content, locations, files), scan history, a cross-repository skill search, and a
scan dialog with step-by-step progress. An organization URL in the scan dialog scans all its
repositories; the dashboard then filters by that owner, shows the organization scan with its
failures, and hides repositories without skills until you ask for them. Light and dark themes follow the system. `--web-port N` fixes the port, `--no-browser` only prints
the URL. The URL carries a one-time token that the page exchanges for a `SameSite=Strict`
cookie; the server also checks the `Host` header and sends a strict CSP.

## Similar skills

The Similar tab (TUI key `7`, a tab in the web skill panel) lists skills of the same
snapshot that are at least 40% similar, to find partial duplicates worth merging. The
score is the maximum of two deterministic signals: TF-IDF cosine over name, description
and body words, and the share of 5-word passages one body copies from the other. It is
lexical: paraphrases in different words score low. See SPEC section 5.7.

The Other repos tab (TUI key `8`) finds the same skill in the latest snapshots of the
other repositories in the store: identical copies, edited copies (forks) and rewrites.
It links skills by content, not by name, and the web Skills page groups them into
families. See SPEC section 5.8.

## Store and aggregation

Snapshots live in `$SKILL_ATLAS_HOME/scans/` (default `~/.local/share/skill-atlas/scans/`),
one flat, time-sortable file per scan.

```bash
skill-atlas                                        # TUI: repositories -> snapshot -> skills; n scans a new repo or org
skill-atlas --web                                  # the same in the browser (localhost, token in the URL)
skill-atlas repos                                  # known repositories, latest scan each
skill-atlas skills --category test                 # relevant (default), auxiliary, all, or one category
skill-atlas skills --group-by name                 # same skill name across repositories
skill-atlas skills --group-by hash                 # identical copies across repositories
skill-atlas show github.com/anthropics/skills      # TUI on a saved snapshot, offline
skill-atlas show github.com/anthropics/skills --plain
```

## TUI keys

`j/k` move · `/` search · `g` category · `f` kind · `t` type · `c` compliance · `d` group copies ·
`1`–`8` tabs (6 = how the snapshot was made, 7 = similar skills, 8 = other repositories) · `o` open on GitHub · `e` export ·
`Enter` open repository · `n` scan a new repository or organization · `o` filter by owner ·
`e` show repositories without skills · `x` stop an organization scan · `Esc` back · `q` quit

Identical copies of one skill in several directories (same kind, name and content)
show as one row with `+N` in the `copies` column. Snapshots keep every copy.

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
UPDATE_GOLDEN=1 uv run pytest tests/test_detection.py   # regenerate golden snapshots
(cd e2e && npm ci && npx playwright test)             # web UI tests; screenshots are compared in CI (e2e/README.md)
```

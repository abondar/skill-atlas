# AGENTS.md

`skill-atlas` is a Python CLI and Textual TUI. It finds AI agent skills in a repository and
stores immutable JSON snapshots. [SPEC.md](SPEC.md) is the source of truth for behavior;
update it together with the code.

## Commands

```bash
uv sync                                            # install
uv run skill-atlas --help                          # run
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
UPDATE_GOLDEN=1 uv run pytest tests/test_detection.py   # regenerate golden snapshots
```

All four checks must pass before a commit. Fix failures you find, including pre-existing ones.

## Definition of done: CI must be green

Work that includes a push is not done when the push succeeds. It is done when CI passes
for the pushed commit.

1. Push.
2. Find the run for the pushed commit:
   `gh run list --commit "$(git rev-parse HEAD)" --limit 5`
3. Wait for it to finish: `gh run watch <run-id> --exit-status`
4. If the run fails, read the log (`gh run view <run-id> --log-failed`), fix the cause,
   push again and repeat from step 2.
5. Report the work as done only after every job in the run is green. Report the run URL.

Do not skip or mark tests as expected failures to make CI pass. If CI fails for a reason
outside the change (for example, a runner outage), say so and give the run URL.

## Conventions

- Terminal output: sanitize every string from a repository (`skill_atlas.sanitize`). Pass
  `rich.text.Text` to Textual widgets, never markup strings.
- Web UI (`skill_atlas/web`): the server sanitizes snapshot strings; `app.js` inserts them
  with `textContent` only. `body_html` is the single `innerHTML`, rendered with raw HTML off.
  Keep the CSP strict: no inline scripts or styles.
- Keep the TUI and the web UI at feature parity; shared view logic lives in `views.py`.
- Snapshots are immutable. Never rewrite a file in the store.
- Tests must not use the network. GitHub tests use `respx` fakes (`tests/test_github.py`).

# Web UI end-to-end tests (Playwright)

- `tests/journey.spec.ts` — the main user journey: from an empty store, scan a
  repository in the UI and get its list of skills, open a skill, find similar skills and
  the same skill in a second repository. It saves a screenshot at each key step and
  compares it with the baseline in `tests/__screenshots__/`, to catch UI drift between
  versions.
- `tests/ui.spec.ts` — functional checks of filters, the skill panel, copies, the Skills
  page, rescans, scan errors and an organization scan.

Every test starts its own server over a fixture store (`server.py`) and fails on a console
error, an uncaught exception or a CSP violation. The server fakes GitHub in process
(`FakeGitHub` in `server.py`, the organization `acme`), so no test touches the network. `journey.spec.ts` bypasses the CSP:
Playwright injects inline styles to take screenshots, and the CSP blocks them. The CSP is
checked by `ui.spec.ts`, which takes no screenshots.

## Run

```bash
cd e2e
npm ci
npx playwright install chromium
npx playwright test                       # every functional check; no screenshot comparison
npx playwright test tests/journey.spec.ts # one file; any Playwright arguments work
```

## Screenshots are compared only in CI

Rendering depends on the OS, the fonts and the Chromium build, so a baseline from a Mac
does not match Linux. Baselines are made and compared only in the `e2e` job of
`.github/workflows/ci.yml`: the runner is pinned by version (`ubuntu-24.04`) and Chromium by
the `@playwright/test` version. That job sets `SKILL_ATLAS_VISUAL=1`; without it Playwright
skips the comparison.

Values that change between runs are pinned, not ignored:
- `server.py` pins the scan clock, scan IDs and durations; fixture repositories are git
  repositories with fixed dates, so commit SHAs are stable;
- `fixtures.ts` pins the browser clock, so relative times read "2 hours ago";
- the fixture root is a fixed path per test, so paths in the UI are stable.

Nothing is masked: a mask also hides whatever overlaps it, such as the skill panel. A new
value that differs between runs fails the comparison; pin it in `server.py` or
`fixtures.ts`. `screenshot.css` hides toasts, which expire on a timer.

## After an intended UI change

1. Push the change, then run the `ci` workflow manually on that branch with
   "update_screenshots":
   `gh workflow run ci --ref <branch> -f update_screenshots=true`.
2. Download the baselines into place:
   `gh run download <run-id> -n playwright-baselines -D e2e/tests/__screenshots__`.
3. Look at every changed PNG in the diff before you commit it. A baseline you did not
   review hides the drift this test exists to catch.

When a comparison fails, the `playwright-report` artifact shows the expected, actual and
diff image of each screenshot.

When GitHub retires `ubuntu-24.04`, move the job to the next image and rewrite all
baselines once.

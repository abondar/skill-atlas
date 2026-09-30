import { defineConfig, devices } from "@playwright/test";

// Screenshots depend on the OS, fonts and Chromium. Baselines are made and compared only in
// the CI e2e job (.github/workflows/ci.yml), which sets SKILL_ATLAS_VISUAL=1. Elsewhere the
// tests still run every functional check, but skip the screenshot comparison.
const visual = process.env.SKILL_ATLAS_VISUAL === "1";

export default defineConfig({
  testDir: "./tests",
  // One baseline per screenshot, no platform suffix: there is one platform, the CI runner.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}{ext}",
  ignoreSnapshots: !visual,
  // CI must fail on a missing baseline instead of writing one.
  updateSnapshots: process.env.CI ? "none" : "missing",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 90_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  expect: {
    timeout: 15_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      stylePath: "./screenshot.css",
      // Anti-aliasing noise stays under this; a moved or restyled element does not.
      maxDiffPixelRatio: 0.002,
      threshold: 0.2,
    },
  },
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "UTC",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium" }],
});

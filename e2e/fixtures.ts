import { spawn } from "node:child_process";
import path from "node:path";
import { createInterface } from "node:readline";
import { test as base, expect, type Page } from "@playwright/test";

// Two hours after the fixture scans (server.py pins them to 2026-01-01T10:00Z onwards),
// so relative times read "2 hours ago" in every run.
export const FIXED_BROWSER_TIME = new Date("2026-01-01T12:00:00Z");

const REPO_ROOT = path.resolve(import.meta.dirname, "..");

export type Atlas = { url: string; token: string; repos: Record<string, string> };
type Options = { fixture: "journey" | "demo" };

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
}

export const test = base.extend<Options & { atlas: Atlas }>({
  fixture: ["demo", { option: true }],

  // A fresh server and store per test. The root path is fixed per test so that paths in
  // the UI are the same in every run.
  atlas: async ({ fixture }, use, testInfo) => {
    const root = path.join("/tmp/skill-atlas-e2e", slug(testInfo.titlePath.slice(1).join(" ")));
    const proc = spawn("uv", ["run", "--no-sync", "python", "e2e/server.py", fixture, root], {
      cwd: REPO_ROOT,
      stdio: ["ignore", "pipe", "inherit"],
    });
    const line = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("fixture server did not start")), 60_000);
      proc.once("exit", (code) => reject(new Error(`fixture server exited with ${code}`)));
      createInterface({ input: proc.stdout! }).once("line", (l) => {
        clearTimeout(timer);
        resolve(l);
      });
    });
    try {
      await use(JSON.parse(line) as Atlas);
    } finally {
      proc.kill();
    }
  },

  // Every test fails on a console error, an uncaught exception or a CSP violation.
  page: async ({ page }, use) => {
    const problems: string[] = [];
    page.on("pageerror", (e) => problems.push(`exception: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning") problems.push(`console.${m.type()}: ${m.text()}`);
    });
    await page.clock.setFixedTime(FIXED_BROWSER_TIME);
    await use(page);
    expect(problems, "console errors, exceptions or CSP violations").toEqual([]);
  },
});

export { expect };

export async function login(page: Page, atlas: Atlas): Promise<void> {
  await page.goto(`${atlas.url}/?token=${atlas.token}`);
  await expect(page).toHaveURL(`${atlas.url}/`); // the token moved into a cookie
}

// A screenshot compared with its baseline. Nothing is masked: server.py and the fixed
// browser clock pin every value that would differ between runs, and a mask would also hide
// whatever overlaps it (the skill panel). A new unpinned value fails the comparison.
export async function shot(page: Page, name: string, options: { fullPage?: boolean } = {}): Promise<void> {
  await page.mouse.move(0, 0); // no hover state from the last click
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`, options);
}

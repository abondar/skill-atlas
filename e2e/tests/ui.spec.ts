// Functional checks of the web UI over a store with two scanned repositories (koog-like
// and MPS-like, see server.py). The visual baselines live in journey.spec.ts. This file
// runs with the CSP on and takes no screenshots, so any CSP violation fails it.
import { expect, login, test } from "../fixtures";

test.use({ fixture: "demo" });

const cards = ".grid .card";

test("home: cards, filter, token and keyboard", async ({ page, atlas }) => {
  await login(page, atlas);
  await expect(page.locator("a.card")).toHaveCount(2);
  await expect(page.locator(".stats")).toContainText("Repositories");
  await page.keyboard.press("/");
  await expect(page.getByRole("searchbox", { name: "Filter repositories" })).toBeFocused();
  await page.keyboard.type("koog");
  await expect(page.locator("a.card")).toHaveCount(1);
});

test("repository page: relevance filter, skill panel, state in the URL", async ({ page, atlas }) => {
  await login(page, atlas);
  await page.locator("a.card", { hasText: "koog" }).click();
  await expect(page.locator(".repo-head h1")).toContainText("koog");
  await expect(page.locator(cards)).toHaveCount(2); // test fixtures hidden
  await page.locator(".segmented button", { hasText: "All" }).click();
  await expect(page.locator(cards)).toHaveCount(4);
  await expect(page.locator(".grid .card.aux")).toHaveCount(2);

  await page.locator(cards, { hasText: "weather-retrieval" }).click();
  const drawer = page.locator(".drawer");
  await expect(drawer).toContainText("jvmTest"); // the category reason
  await drawer.locator(".tabs button", { hasText: "Content" }).click();
  await expect(drawer.locator(".markdown")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  const hash = await page.evaluate(() => location.hash);
  expect(hash).toContain("cat=all");
  expect(hash).not.toContain("skill=");

  await page.reload();
  await expect(page.locator(cards)).toHaveCount(4); // filters survive a reload

  await page.locator(".tabs button", { hasText: "Scan details" }).click();
  await expect(page.locator(".panels")).toContainText("Fetch method");
  await page.locator(".tabs button", { hasText: "History" }).click();
  await expect(page.locator(".timeline li")).toHaveCount(1);
});

test("Similar lists partial duplicates, not the skill itself", async ({ page, atlas }) => {
  await login(page, atlas);
  await page.locator("a.card", { hasText: "koog" }).click();
  await page.locator(cards, { hasText: "add-java" }).click();
  const drawer = page.locator(".drawer");
  await expect(drawer.locator(".tabs button", { hasText: "Similar" })).toHaveText("Similar1");
  await drawer.locator(".tabs button", { hasText: "Similar" }).click();
  const item = drawer.locator(".similar-item");
  await expect(item).toContainText("split");
  await expect(item).toContainText("Near-identical");
  expect(await page.evaluate(() => location.hash.split(/[?&]/))).toContain("dtab=similar");
  await item.click();
  await expect(drawer.locator("h2")).toHaveText("split");
  await page.keyboard.press("Escape");

  await page.locator(".segmented button", { hasText: "All" }).click();
  await page.locator(cards, { hasText: "arithmetic" }).click();
  await drawer.locator(".tabs button", { hasText: "Similar" }).click();
  await expect(drawer.locator(".empty")).toContainText("No similar skills");
});

test("copies: identical ones grouped, a drifted one listed as another version", async ({ page, atlas }) => {
  await login(page, atlas);
  await page.locator("a.card", { hasText: "mps" }).click();
  await expect(page.locator(cards)).toHaveCount(3);
  await page.locator(".switch input").click();
  await expect(page.locator(cards)).toHaveCount(4);
  await page.locator(".switch input").click();
  await page.locator(cards, { hasText: "mps-actions" }).first().click();
  const drawer = page.locator(".drawer");
  await expect(drawer.locator(".locations li")).toHaveCount(3);
  await expect(drawer.locator(".locations")).toContainText("different content");
  await expect(drawer.locator(".locations")).toContainText("of this text is there");
  await drawer.locator(".tabs button", { hasText: "Similar" }).click();
  await expect(drawer.locator(".drawer-body")).toContainText("1 other version");
  await expect(drawer.locator(".drawer-body")).not.toContainText("plugins/mcp");
});

test("Skills page: search across repositories", async ({ page, atlas }) => {
  await login(page, atlas);
  await page.locator('.nav a[href="#/skills"]').click();
  await expect(page.getByRole("heading", { name: "Skills", exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search skills" }).fill("pdf");
  await expect(page.locator(".group-list .card")).toHaveCount(1);
  await page.locator(".group-list .card .repos a").first().click();
  await expect(page.locator(".drawer")).toBeVisible();
});

test("scan from the UI, compare across repositories, rescan and errors", async ({ page, atlas }) => {
  await login(page, atlas);
  const dialog = page.locator("dialog[open]");
  await page.locator(".topbar .btn", { hasText: "Scan repository" }).click();
  await dialog.locator('input[name="target"]').fill(atlas.repos.fresh);
  await dialog.locator('button[type="submit"]').click();
  await expect(page.locator(".repo-head h1")).toContainText("fresh", { timeout: 30_000 });
  await expect(page.locator(".grid")).toContainText("hello");
  expect(await page.evaluate(() => document.body.innerText.includes("null"))).toBe(false);
  await expect(page.locator(".scan-pill")).toBeHidden();

  // hello is add-java from koog under another name.
  await page.locator(cards, { hasText: "hello" }).click();
  const drawer = page.locator(".drawer");
  await drawer.locator(".tabs button", { hasText: "Other repos" }).click();
  const match = drawer.locator(".similar-item", { hasText: "add-java" });
  await expect(match).toContainText("koog");
  await expect(match).toContainText("Copied text");
  await match.click();
  await expect(page.locator(".repo-head h1")).toContainText("koog");
  await expect(page.locator(".drawer h2")).toHaveText("add-java");
  await page.keyboard.press("Escape");

  await page.locator('.nav a[href="#/skills"]').click();
  await expect(page.getByRole("heading", { name: "Skills", exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search skills" }).fill("java");
  const family = page.locator(".group-list .card", { hasText: "fresh" });
  await expect(family).toContainText("koog"); // one family, found through either name

  // A git repository with an unchanged commit: the rescan reuses the snapshot.
  await page.locator('.nav a[href="#/"]').click();
  await expect(page.getByRole("heading", { name: "Repositories", exact: true })).toBeVisible();
  await page.locator("a.card", { hasText: "fresh" }).click();
  await page.locator(".repo-head .btn", { hasText: "Rescan" }).click();
  await expect(page.locator(".toast", { hasText: "already up to date" })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".tabs button", { hasText: "History" })).toHaveText("History1");

  await page.locator(".topbar .btn", { hasText: "Scan repository" }).click();
  await dialog.locator('input[name="target"]').fill("not a target");
  await dialog.locator('button[type="submit"]').click();
  await expect(dialog.locator(".callout.bad")).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press("Escape");
});

test("phone width: no horizontal scroll", async ({ page, atlas }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, atlas);
  await expect(page.locator("a.card")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
});

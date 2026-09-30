// The main user journey: from an empty store, analyze a repository in the web UI and get
// the list of its skills, then inspect one skill and compare with a second repository.
// Screenshots at the key steps are compared with the baselines in __screenshots__/ to
// catch UI drift between versions (see e2e/README.md).
import { expect, login, shot, test } from "../fixtures";

test.use({ fixture: "journey" });

test("analyze a repository and get the list of its skills", async ({ page, atlas }) => {
  const dialog = page.locator("dialog[open]");
  const cards = page.locator(".grid .card");
  const drawer = page.locator(".drawer");
  const drawerTab = (name: string) => drawer.locator(".tabs button", { hasText: name });

  async function scan(target: string, repoName: string) {
    await page.locator(".topbar .btn", { hasText: "Scan repository" }).click();
    await expect(dialog).toBeVisible();
    await dialog.locator('input[name="target"]').fill(target);
    await dialog.locator('button[type="submit"]').click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(page.locator(".repo-head h1")).toContainText(repoName);
  }

  await test.step("an empty store invites the first scan", async () => {
    await login(page, atlas);
    await expect(page.getByRole("heading", { name: "No repositories yet" })).toBeVisible();
    await shot(page, "01-empty-store");
  });

  await test.step("the scan dialog", async () => {
    await page.getByRole("button", { name: "Scan your first repository" }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('input[name="target"]')).toBeFocused();
    await shot(page, "02-scan-dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  await test.step("scan a local repository and open its page", async () => {
    await scan(atlas.repos["acme-app"], "acme-app");
    await expect(page.locator(".repo-head .meta")).toContainText("on main");
  });

  await test.step("the repository page lists the relevant skills", async () => {
    // Relevant by default: the test fixture is hidden, the two code-review copies are one card.
    const names = await cards.locator(".card-title").allTextContents();
    expect(names.sort()).toEqual(["api-docs", "code-review", "hotfix", "release", "triage"]);
    await expect(cards.filter({ hasText: "code-review" })).toContainText("2 locations");
    // The counter counts entries; the cards group identical copies.
    await expect(page.locator(".segmented button.active")).toContainText("Relevant6");
    await expect(page.locator(".result-line")).toContainText("5 results · 6 entries");
    await shot(page, "03-repo-skills", { fullPage: true });
  });

  await test.step("All adds the test fixture, dimmed", async () => {
    await page.locator(".segmented button", { hasText: "All" }).click();
    await expect(cards).toHaveCount(6);
    await expect(page.locator(".grid .card.aux")).toHaveCount(1);
    await expect(page.locator(".grid .card.aux")).toContainText("fake");
    await shot(page, "04-repo-all-skills", { fullPage: true });
  });

  await test.step("search narrows the list", async () => {
    await page.getByRole("searchbox", { name: "Search skills" }).fill("release");
    await expect(cards).toHaveCount(2); // release and hotfix ("Ships a hotfix release.")
    await page.getByRole("searchbox", { name: "Search skills" }).fill("");
    await expect(cards).toHaveCount(6);
  });

  await test.step("a skill panel shows where the skill lives", async () => {
    await cards.filter({ hasText: "code-review" }).click();
    await expect(drawer).toBeVisible();
    await expect(drawer.locator(".locations li")).toHaveCount(2);
    await expect(drawer.locator(".locations")).toContainText(".agents/skills/code-review/SKILL.md");
    await shot(page, "05-skill-overview");
    await drawerTab("Content").click();
    await expect(drawer.locator(".markdown h1")).toHaveText("Review a pull request");
    await shot(page, "06-skill-content");
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });

  await test.step("Similar finds the partial duplicate", async () => {
    await cards.filter({ hasText: "Prepares and tags a release." }).click();
    await drawerTab("Similar").click();
    const item = drawer.locator(".similar-item");
    await expect(item).toHaveCount(1);
    await expect(item).toContainText("hotfix");
    await expect(item).toContainText("Near-identical");
    await expect(item).toContainText("100% of this skill is in that one");
    await shot(page, "07-skill-similar");
    await page.keyboard.press("Escape");
  });

  await test.step("a second repository: Other repos finds the copy", async () => {
    await scan(atlas.repos["acme-fork"], "acme-fork");
    await cards.filter({ hasText: "ship" }).click();
    await drawerTab("Other repos").click();
    const release = drawer.locator(".similar-item", { hasText: "release" }).first();
    await expect(release).toContainText("acme-app");
    await expect(release).toContainText("Copied text");
    await expect(release).toContainText("Other name");
    await shot(page, "08-other-repos");
    await page.keyboard.press("Escape");
  });

  await test.step("the Skills page groups copies across repositories", async () => {
    await page.locator('.nav a[href="#/skills"]').click();
    await page.getByRole("searchbox", { name: "Search skills" }).fill("ship");
    const family = page.locator(".group-list .card");
    await expect(family).toHaveCount(1);
    await expect(family).toContainText("2 repositories");
    await shot(page, "09-skills-families");
  });

  await test.step("the home page lists both repositories", async () => {
    await page.locator('.nav a[href="#/"]').click();
    await expect(page.locator("a.card")).toHaveCount(2);
    await expect(page.locator(".stats")).toContainText("Repositories");
    await shot(page, "10-home");
  });

  await test.step("dark theme", async () => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.locator("a.card", { hasText: "acme-app" }).click();
    await expect(cards).toHaveCount(5);
    await shot(page, "11-repo-dark");
    await page.emulateMedia({ colorScheme: "light" });
  });

  await test.step("phone width", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(cards).toHaveCount(5);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow, "no horizontal scroll at 390 px").toBe(false);
    await shot(page, "12-repo-mobile");
  });
});

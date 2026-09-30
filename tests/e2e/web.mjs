// End-to-end check of the skill-atlas web UI in headless Chrome over the DevTools Protocol.
// Run by tests/test_web_e2e.py, which builds the fixture store and starts the server.
// Needs Node 22+ (built-in WebSocket). Env: CHROME, BASE (server URL), OUT (work dir),
// SCAN_TARGET (a local repository to scan), DEBUG_PORT.
import { spawn } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";

const { CHROME, BASE, OUT, SCAN_TARGET } = process.env;
const PORT = process.env.DEBUG_PORT || "9333";
rmSync(`${OUT}/profile`, { recursive: true, force: true });
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
  // Ubuntu runners restrict unprivileged user namespaces, which the sandbox needs.
  ...(process.platform === "linux" ? ["--no-sandbox"] : []),
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${OUT}/profile`, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 150 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    target = list.find((t) => t.type === "page");
  } catch { await sleep(100); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let seq = 0;
const pending = new Map();
const problems = [];
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  } else if (msg.method === "Runtime.exceptionThrown") {
    problems.push("exception: " + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text));
  } else if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type)) {
    problems.push(`console.${msg.params.type}: ` + msg.params.args.map((a) => a.value ?? a.description).join(" "));
  } else if (msg.method === "Log.entryAdded" && ["error", "warning"].includes(msg.params.entry.level)) {
    problems.push("log: " + msg.params.entry.text);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
const js = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`${expr}: ${r.exceptionDetails.exception?.description}`);
  return r.result.value;
};
async function waitFor(expr, what, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await js(`Boolean(${expr})`)) return;
    await sleep(100);
  }
  throw new Error(`timeout waiting for ${what}: ${expr}`);
}
const click = (sel) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) throw new Error("no " + ${JSON.stringify(sel)}); el.click(); return true; })()`);
const clickText = (sel, text) => js(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find(e => e.textContent.includes(${JSON.stringify(text)})); if (!el) throw new Error("no " + ${JSON.stringify(sel)} + " with " + ${JSON.stringify(text)}); el.click(); return true; })()`);
async function type(sel, text) {
  await js(`document.querySelector(${JSON.stringify(sel)}).focus()`);
  await send("Input.insertText", { text });
  await sleep(150);
}
async function key(k, code = k) {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: k === "Escape" ? 27 : k.charCodeAt(0) });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code });
  await sleep(150);
}
async function shot(name, w = 1440, hgt = 900) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: hgt, deviceScaleFactor: 1, mobile: w < 600 });
  await sleep(300);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(data, "base64"));
}
const count = (sel) => js(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
const text = (sel) => js(`document.querySelector(${JSON.stringify(sel)})?.textContent ?? null`);
const check = (cond, what) => { if (!cond) throw new Error("check failed: " + what); console.log("ok  " + what); };

try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

  // Home
  await send("Page.navigate", { url: `${BASE}/?token=e2e` });
  await waitFor(`document.querySelectorAll("a.card").length`, "repo cards");
  const cards = await count("a.card");
  check(cards === 2, `home shows ${cards} repository cards`);
  check((await text(".stats")).includes("Repositories"), "stat tiles render");
  check(!(await js(`location.search.includes("token")`)), "token removed from the address bar");
  await shot("01-home");
  await key("/", "Slash");
  check(await js(`document.activeElement?.type === "search"`), "/ focuses the filter");
  await type('input[type="search"]', "koog");
  check((await count("a.card")) === 1, "filter narrows to one card");
  await click("a.card");
  await waitFor(`document.querySelector(".repo-head")`, "repo page");

  // Repo page: koog, relevance filter, drawer
  check((await text(".repo-head h1")).includes("koog"), "koog page opens");
  check((await text(".grid")).includes("Test data") === false, "test skills hidden by default");
  const relevant = await count(".grid .card");
  check(relevant === 2, `relevant filter shows 2 skills (got ${relevant})`);
  await shot("02-repo-koog");
  await clickText(".segmented button", "All");
  check((await count(".grid .card")) === 4, "All shows 4 skills");
  check((await count(".grid .card.aux")) === 2, "test skills are dimmed");
  await clickText(".grid .card", "weather-retrieval");
  await waitFor(`document.querySelector(".drawer")`, "drawer");
  check((await text(".drawer")).includes("jvmTest"), "drawer shows the category reason");
  await clickText(".drawer .tabs button", "Content");
  await waitFor(`document.querySelector(".drawer .markdown")`, "markdown body");
  await shot("03-drawer-content");
  await key("Escape");
  check((await count(".drawer")) === 0, "Escape closes the drawer");
  const hashBefore = await js("location.hash");
  check(hashBefore.includes("cat=all") && !hashBefore.includes("skill="), "filters live in the URL, closed drawer removed from it");

  // Deep link reload keeps the state
  await send("Page.reload");
  await waitFor(`document.querySelectorAll(".grid .card").length === 4`, "state after reload");
  check(true, "reload restores the filters");

  await clickText(".tabs button", "Scan details");
  check((await text(".panels")).includes("Fetch method"), "scan details tab");
  await clickText(".tabs button", "History");
  check((await count(".timeline li")) === 1, "history tab lists scans");
  await shot("04-history");

  // MPS: grouped copies
  await send("Page.navigate", { url: `${BASE}/#/` });
  await waitFor(`document.querySelectorAll("a.card").length`, "home again");
  await clickText("a.card", "mps");
  await waitFor(`document.querySelector(".grid .card")`, "mps skills");
  const grouped = await count(".grid .card");
  check(grouped === 2, `copies are grouped into 2 cards (got ${grouped})`);
  await shot("05-repo-mps");
  await click(".switch input");
  check((await count(".grid .card")) === 4, "turning grouping off shows 4 cards");
  await click(".switch input");
  await clickText(".grid .card", "mps-actions");
  await waitFor(`document.querySelector(".drawer .locations")`, "locations");
  check((await count(".drawer .locations li")) === 3, "drawer lists 3 locations of a copied skill");
  await shot("06-drawer-overview");
  await click('.drawer button[aria-label="Close"]');

  // Skills across repositories
  await click('.nav a[href="#/skills"]');
  await waitFor(`document.querySelector(".group-list .card")`, "skills page");
  await type('input[type="search"]', "pdf");
  const groups = await count(".group-list .card");
  check(groups === 1, `global search finds ${groups} group for "pdf"`);
  await shot("07-skills");
  await click(".group-list .card .repos a");
  await waitFor(`document.querySelector(".drawer")`, "drawer from global search");
  check(true, "a repository chip opens the skill in its repository");
  await click('.drawer button[aria-label="Close"]');

  // Scan a new local repository
  await clickText(".topbar .btn", "Scan repository");
  await waitFor(`document.querySelector("dialog[open]")`, "scan dialog");
  await shot("08-scan-dialog");
  await type('dialog input[name="target"]', SCAN_TARGET);
  await click('dialog button[type="submit"]');
  await waitFor(`!document.querySelector("dialog[open]") && document.querySelector(".repo-head h1")?.textContent.includes("fresh") && document.querySelector(".grid")?.textContent.includes("hello")`, "scan finishes and opens the repo with its skill", 30000);
  check(true, "new repository page shows the scanned skill");
  check(!(await js(`document.body.innerText.includes("null")`)), "no stray null text");
  check(await js(`document.querySelector(".scan-pill").hidden && getComputedStyle(document.querySelector(".scan-pill")).display === "none"`), "scan pill hidden after the scan");
  await shot("09-after-scan");

  // Rescan: cached snapshot
  await clickText(".repo-head .btn", "Rescan");
  // A plain directory has no commit, so there is no cache: the rescan adds a snapshot.
  await waitFor(`[...document.querySelectorAll(".tabs button")].some(b => b.textContent.startsWith("History") && b.textContent.includes("2"))`, "second snapshot in history", 30000);
  check(true, "rescan adds a second snapshot to the history");

  // Scan error
  await clickText(".topbar .btn", "Scan repository");
  await type('dialog input[name="target"]', "not a target");
  await click('dialog button[type="submit"]');
  await waitFor(`document.querySelector("dialog[open] .callout.bad")`, "error callout", 30000);
  check(true, "an invalid target shows the error inside the dialog");
  await shot("10-scan-error");
  await key("Escape");

  // Mobile layout
  await send("Page.navigate", { url: `${BASE}/#/` });
  await waitFor(`document.querySelectorAll("a.card").length`, "home mobile");
  await shot("11-mobile", 390, 844);
  const overflow = await js("document.documentElement.scrollWidth > window.innerWidth");
  check(!overflow, "no horizontal scroll at 390 px");

  const csp = problems.filter((p) => /Content Security Policy|Refused/.test(p));
  check(csp.length === 0, "no CSP violations");
  check(problems.length === 0, `no console errors or exceptions${problems.length ? ": " + problems.join(" | ") : ""}`);
  console.log("E2E PASSED");
} catch (e) {
  console.log("E2E FAILED: " + e.message);
  if (problems.length) console.log("problems: " + problems.join("\n"));
  await shot("zz-failure").catch(() => {});
  process.exitCode = 1;
} finally {
  ws.close();
  chrome.kill();
}

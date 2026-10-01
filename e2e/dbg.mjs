import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
const proc = spawn("uv", ["run", "--no-sync", "python", "e2e/server.py", "demo", "/tmp/dbg-root"], { cwd: "..", stdio: ["ignore", "pipe", "inherit"] });
const line = await new Promise(r => createInterface({ input: proc.stdout }).once("line", r));
const a = JSON.parse(line); console.log("atlas url", a.url);
const b = await chromium.launch();
const p = await b.newPage();
p.on("console", m => console.log("console", m.type(), m.text()));
p.on("requestfailed", r => console.log("reqfail", r.url(), r.failure()?.errorText));
p.on("response", r => console.log("resp", r.status(), r.url()));
p.on("framenavigated", f => console.log("nav", f.url()));
try { await p.goto(`${a.url}/?token=${a.token}`); } catch (e) { console.log("goto err", e.message.split("\n")[0]); }
await p.waitForTimeout(1500);
console.log("url", JSON.stringify(p.url()), (await p.content()).slice(0, 300));
await b.close(); proc.kill();

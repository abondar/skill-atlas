"use strict";
// skill-atlas web UI.
//
// Safety: every string from the server goes into the DOM as text (textContent or
// attribute values). The one exception is `body_html`, which the server renders
// from Markdown with raw HTML disabled; the page CSP forbids inline scripts on top.

// --- icons (Lucide-style paths, drawn as DOM nodes, no inline markup) -------------

const ICONS = {
  logo: "M12 2 2 7l10 5 10-5-10-5Z M2 17l10 5 10-5 M2 12l10 5 10-5",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z M21 21l-4.3-4.3",
  plus: "M12 5v14 M5 12h14",
  external: "M15 3h6v6 M10 14 21 3 M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",
  copy: "M9 9h11v11H9z M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  x: "M18 6 6 18 M6 6l12 12",
  chevron: "M9 18l6-6-6-6",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M12 6v6l4 2",
  commit: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M2 12h6 M16 12h6",
  star: "M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1L12 2Z",
  scale: "M12 3v18 M5 7h14 M5 7l-3 7a4 4 0 0 0 6 0L5 7Z M19 7l-3 7a4 4 0 0 0 6 0l-3-7Z M8 21h8",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z M12 9v4 M12 17h.01",
  check: "M20 6 9 17l-5-5",
  checkCircle: "M22 11.1V12a10 10 0 1 1-5.9-9.1 M22 4 12 14l-3-3",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M12 16v-4 M12 8h.01",
  refresh: "M3 12a9 9 0 0 1 15-6.7L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-15 6.7L3 16 M3 21v-5h5",
  folder: "M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z M14 2v6h6",
  layers: "M12 2 2 7l10 5 10-5-10-5Z M2 17l10 5 10-5 M2 12l10 5 10-5",
  bot: "M12 8V4H8 M4 8h16v12H4z M2 14h2 M20 14h2 M9 13v2 M15 13v2",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z M5 3v4 M3 5h4 M19 17v4 M17 19h4",
  plug: "M12 22v-5 M9 8V2 M15 8V2 M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z",
  arrowLeft: "M19 12H5 M12 19l-7-7 7-7",
  book: "M4 19.5V4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5 M12 7v5l4 2",
  settings: "M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6",
};
const SVG_NS = "http://www.w3.org/2000/svg";

function icon(name, cls = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `icon ${cls}`.trim());
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

// --- DOM and formatting helpers ----------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const nf = new Intl.NumberFormat("en");
const num = (n) => nf.format(n ?? 0);
const plural = (n, word, many = `${word}s`) => `${num(n)} ${n === 1 ? word : many}`;
const sha8 = (sha) => (sha ? sha.slice(0, 8) : null);
const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const dtf = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

function ago(iso) {
  if (!iso) return "never";
  const seconds = (Date.parse(iso) - Date.now()) / 1000;
  const units = [
    ["year", 31536000], ["month", 2592000], ["week", 604800],
    ["day", 86400], ["hour", 3600], ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

const when = (iso) => (iso ? dtf.format(new Date(iso)) : "—");

function bytes(n) {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function splitKey(key) {
  const parts = key.split("/");
  if (parts[0] === "local") return { host: "local", owner: "local", name: parts.slice(1).join("/") };
  return { host: parts[0], owner: parts.slice(1, -1).join("/"), name: parts[parts.length - 1] };
}

// Human names keep the original case (JetBrains/MPS); repo_key is lowercased.
function repoName(src, key) {
  const fallback = splitKey(key);
  if (!src) return fallback;
  if (src.kind === "local") return { host: "local", owner: "local", name: src.name || fallback.name };
  return { host: src.host || fallback.host, owner: src.owner || fallback.owner, name: src.name || fallback.name };
}

function repoById(id) {
  return cache.repos?.repos.find((r) => r.id === id) || null;
}

function hue(text) {
  let x = 0;
  for (const ch of text) x = (x * 31 + ch.codePointAt(0)) >>> 0;
  return `hue-${x % 8}`;
}

function avatar(names, cls = "") {
  const { owner, name } = names;
  return h("div", { class: `avatar ${hue(owner.toLowerCase())} ${cls}`, "aria-hidden": "true" }, (name[0] || "?").toUpperCase());
}

const CATEGORY_LABEL = {
  project: "Project", subproject: "Subproject", plugin: "Plugin", external: "External",
  bundled: "Bundled", catalog: "Catalog", test: "Test data", example: "Example",
  template: "Template", docs: "Docs",
};
const AUXILIARY = new Set(["test", "example", "template", "docs"]);
const TYPE_LABEL = {
  "agent-skill": "Agent Skill", "claude-command": "Claude command", "plugin-command": "Plugin command",
  "plugin-command-inline": "Inline command", "copilot-prompt": "Copilot prompt",
  "claude-agent": "Claude agent", "copilot-agent": "Copilot agent", "external-plugin": "External plugin",
};

function categoryBadge(cat) {
  return h("span", { class: `badge cat-${cat || "none"}` }, CATEGORY_LABEL[cat] || "Uncategorized");
}

function complianceBadge(status) {
  const map = { compliant: ["ok", "checkCircle", "Compliant"], loadable: ["warn", "alert", "Spec issues"], broken: ["bad", "alert", "Broken"] };
  const [cls, ic, label] = map[status] || ["", "info", status];
  return h("span", { class: `badge ${cls}` }, icon(ic), label);
}

function kindIcon(s) {
  if (s.type === "external-plugin") return "plug";
  return s.kind === "agent" ? "bot" : "sparkles";
}

function categoryBar(byCategory) {
  const entries = Object.entries(byCategory || {}).filter(([, n]) => n > 0);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  const bar = h("div", { class: "catbar", role: "img", "aria-label": "skills by category" });
  for (const [cat, n] of entries) {
    const seg = h("span", { class: `cat-${cat}`, title: `${CATEGORY_LABEL[cat] || cat}: ${n}` });
    seg.style.flexGrow = String(n); // CSSOM, allowed by the CSP (inline style attributes are not)
    bar.append(seg);
  }
  if (!total) bar.append(h("span", { class: "cat-none" }));
  return bar;
}

function legend(byCategory) {
  return h(
    "div",
    { class: "legend" },
    Object.entries(byCategory || {})
      .sort((a, b) => b[1] - a[1])
      .map(([cat, n]) => h("span", {}, h("span", { class: `dot cat-${cat}` }), `${CATEGORY_LABEL[cat] || cat} ${num(n)}`)),
  );
}

// --- API ----------------------------------------------------------------------------

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try {
    data = await r.json();
  } catch (_) {
    /* no JSON body */
  }
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

const cache = { repos: null, snapshots: new Map(), skills: new Map() };

async function loadRepos(force = false) {
  if (force || !cache.repos) cache.repos = await api("/api/repos");
  return cache.repos;
}

async function loadSnapshot(file) {
  if (!cache.snapshots.has(file)) {
    cache.snapshots.set(file, await api(`/api/snapshot?file=${encodeURIComponent(file)}`));
  }
  return cache.snapshots.get(file);
}

function invalidate() {
  cache.repos = null;
  cache.skills.clear();
}

// --- toasts -------------------------------------------------------------------------

const toasts = h("div", { class: "toast-stack", role: "status", "aria-live": "polite" });
document.body.append(toasts);

function toast(message, kind = "ok") {
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "bad" ? "alert" : "checkCircle"), h("div", {}, message));
  toasts.append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 9000 : 4000);
}

// --- routing ------------------------------------------------------------------------
//   #/                     repositories
//   #/r/<repo id>?...      one repository
//   #/skills?...           skills across repositories

function route() {
  const raw = location.hash.slice(1) || "/";
  const q = raw.indexOf("?");
  const path = q >= 0 ? raw.slice(0, q) : raw;
  const params = new URLSearchParams(q >= 0 ? raw.slice(q + 1) : "");
  if (path.startsWith("/r/")) return { page: "repo", id: decodeURIComponent(path.slice(3)), params };
  if (path === "/skills") return { page: "skills", params };
  return { page: "home", params };
}

function href(page, id, params = {}) {
  const base = page === "repo" ? `/r/${encodeURIComponent(id)}` : page === "skills" ? "/skills" : "/";
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""));
  const qs = q.toString();
  return `#${base}${qs ? "?" + qs : ""}`;
}

// Navigate; re-render when the hash does not change (hashchange would not fire).
function go(target) {
  if (location.hash === target) render();
  else location.hash = target;
}

function setParams(update) {
  const r = route();
  for (const [k, v] of Object.entries(update)) {
    if (v === null || v === undefined || v === "") r.params.delete(k);
    else r.params.set(k, v);
  }
  history.replaceState(null, "", href(r.page, r.id, Object.fromEntries(r.params)));
}

// --- shell --------------------------------------------------------------------------

const app = document.getElementById("app");
const shell = {};

function buildShell() {
  shell.nav = {
    home: h("a", { href: "#/" }, "Repositories"),
    skills: h("a", { href: "#/skills" }, "Skills"),
  };
  shell.pill = h("button", { class: "scan-pill", hidden: true, onclick: () => scanDialog.open() });
  shell.main = h("main", { class: "page", id: "main" });
  app.replaceChildren(
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "brand", href: "#/" }, h("span", { class: "brand-mark" }, icon("logo")), "skill-atlas"),
      h("nav", { class: "nav" }, shell.nav.home, shell.nav.skills),
      h("span", { class: "spacer" }),
      shell.pill,
      h("button", { class: "btn primary", onclick: () => scanDialog.open() }, icon("plus"), h("span", { class: "label" }, "Scan repository")),
    ),
    shell.main,
  );
}

let renderToken = 0;
async function render() {
  const r = route();
  const token = ++renderToken;
  shell.nav.home.classList.toggle("active", r.page !== "skills");
  shell.nav.skills.classList.toggle("active", r.page === "skills");
  closeDrawer(false);
  try {
    if (r.page === "repo") await renderRepo(r.id, r.params, token);
    else if (r.page === "skills") await renderSkills(r.params, token);
    else await renderHome(r.params, token);
  } catch (e) {
    if (token !== renderToken) return;
    setMain(emptyState("alert", "Something went wrong", e.message));
  }
}

function setMain(...nodes) {
  shell.main.replaceChildren(...nodes.flat().filter(Boolean)); // null would render as text
}

function emptyState(ic, title, text, action) {
  return h("div", { class: "empty" }, h("div", { class: "icon-wrap" }, icon(ic, "xl")), h("h2", {}, title), text ? h("p", {}, text) : null, action || null);
}

function skeletonGrid(n = 6) {
  return h("div", { class: "grid" }, Array.from({ length: n }, () => h("div", { class: "skeleton" })));
}

// --- home: repositories ------------------------------------------------------------

async function renderHome(params, token) {
  if (!cache.repos) setMain(skeletonGrid());
  const data = await loadRepos();
  if (token !== renderToken) return;
  const repos = data.repos;
  document.title = "skill-atlas";

  if (!repos.length) {
    setMain(
      emptyState(
        "layers",
        "No repositories yet",
        "Scan a GitHub repository or a local path to build your skill atlas.",
        h("button", { class: "btn primary", onclick: () => scanDialog.open() }, icon("plus"), "Scan your first repository"),
      ),
    );
    return;
  }

  const totals = repos.reduce(
    (t, r) => {
      for (const [cat, n] of Object.entries(r.by_category)) {
        if (AUXILIARY.has(cat)) t.aux += n;
      }
      t.skills += r.latest.skills;
      t.agents += r.latest.agents;
      t.external += r.latest.external;
      return t;
    },
    { skills: 0, agents: 0, external: 0, aux: 0 },
  );

  const search = h("input", {
    type: "search",
    placeholder: "Filter repositories",
    value: params.get("q") || "",
    "aria-label": "Filter repositories",
    oninput: (e) => {
      setParams({ q: e.target.value });
      updateGrid();
    },
  });
  const sort = h(
    "select",
    { class: "select", "aria-label": "Sort", onchange: (e) => { setParams({ sort: e.target.value }); updateGrid(); } },
    [["recent", "Recently scanned"], ["skills", "Most skills"], ["name", "Name"]].map(([v, label]) =>
      h("option", { value: v, selected: (params.get("sort") || "recent") === v }, label),
    ),
  );
  const grid = h("div", { class: "grid" });
  const resultLine = h("div", { class: "result-line" });

  function updateGrid() {
    const p = route().params;
    const q = (p.get("q") || "").toLowerCase();
    let rows = repos.filter((r) => `${r.repo_key} ${r.description || ""}`.toLowerCase().includes(q));
    const by = p.get("sort") || "recent";
    if (by === "skills") rows = [...rows].sort((a, b) => b.latest.skills - a.latest.skills);
    if (by === "name") rows = [...rows].sort((a, b) => a.repo_key.localeCompare(b.repo_key));
    resultLine.textContent = q ? `${plural(rows.length, "repository", "repositories")} match` : "";
    grid.replaceChildren(...rows.map(repoCard));
    if (!rows.length) grid.replaceChildren(emptyState("search", "No matches", "Try another name."));
  }

  setMain(
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Repositories"), h("div", { class: "sub" }, `Snapshots in ${data.store}`)),
    ),
    h(
      "div",
      { class: "stats" },
      stat("layers", "Repositories", num(repos.length), `${num(repos.reduce((a, r) => a + r.scans, 0))} scans`),
      stat("sparkles", "Skills", num(totals.skills), totals.aux ? `${num(totals.aux)} test, example or docs` : "latest scans"),
      stat("bot", "Agents", num(totals.agents), "subagent definitions"),
      stat("plug", "External plugins", num(totals.external), "listed, not scanned"),
    ),
    h("div", { class: "toolbar" }, h("label", { class: "field" }, icon("search"), search, h("kbd", {}, "/")), h("span", { class: "spacer" }), sort),
    resultLine,
    grid,
  );
  updateGrid();
}

function stat(ic, label, value, hint) {
  return h("div", { class: "stat" }, h("div", { class: "label" }, icon(ic), label), h("div", { class: "value" }, value), h("div", { class: "hint" }, hint));
}

function repoCard(r) {
  const names = repoName(r.source, r.repo_key);
  const { host, owner, name } = names;
  const meta = r.meta || {};
  return h(
    "a",
    { class: "card", href: href("repo", r.id) },
    h(
      "div",
      { class: "card-head" },
      avatar(names),
      h(
        "div",
        {},
        h("div", { class: "card-title" }, h("span", { class: "owner" }, `${owner} / `), name),
        h("div", { class: "card-sub" }, host === "local" ? "local repository" : host),
      ),
    ),
    h("div", { class: "card-desc" }, r.description || "No description."),
    categoryBar(r.by_category),
    legend(r.by_category),
    h(
      "div",
      { class: "card-foot" },
      h("span", { class: "item" }, icon("sparkles"), plural(r.latest.skills, "skill")),
      r.latest.agents ? h("span", { class: "item" }, icon("bot"), plural(r.latest.agents, "agent")) : null,
      meta.stars !== null && meta.stars !== undefined ? h("span", { class: "item" }, icon("star"), num(meta.stars)) : null,
      h("span", { class: "item", title: when(r.latest.scanned_at) }, icon("clock"), ago(r.latest.scanned_at)),
    ),
  );
}

// --- repository page ----------------------------------------------------------------

function repoFilters(p) {
  return {
    cat: p.get("cat") || "relevant",
    kind: p.get("kind") || "all",
    type: p.get("type") || "all",
    comp: p.get("comp") || "all",
    q: p.get("q") || "",
    group: p.get("group") !== "0",
  };
}

function matchesCategory(s, cat) {
  if (cat === "all") return true;
  if (cat === "relevant") return s.relevant;
  if (cat === "auxiliary") return !s.relevant;
  return s.category === cat;
}

function matchesOthers(s, f) {
  if (f.kind !== "all" && s.kind !== f.kind) return false;
  if (f.type !== "all" && s.type !== f.type) return false;
  if (f.comp !== "all" && s.compliance.status !== f.comp) return false;
  if (f.q) {
    const hay = [s.name, s.description, s.path, s.source_pointer].filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(f.q.toLowerCase())) return false;
  }
  return true;
}

function groupCopies(skills, on) {
  if (!on) return skills.map((s) => ({ skill: s, copies: [] }));
  const byKey = new Map();
  const out = [];
  for (const s of skills) {
    const row = byKey.get(s.dup_key);
    if (row) row.copies.push(s);
    else {
      const fresh = { skill: s, copies: [] };
      byKey.set(s.dup_key, fresh);
      out.push(fresh);
    }
  }
  return out;
}

const page = {}; // state of the repository page

async function renderRepo(id, params, token) {
  const data = await loadRepos();
  const repo = data.repos.find((r) => r.id === id);
  if (token !== renderToken) return;
  if (!repo) {
    setMain(emptyState("search", "Repository not found", "It may have been removed from the store.", h("a", { class: "btn", href: "#/" }, "Back to repositories")));
    return;
  }
  const file = params.get("snap") || repo.latest.file;
  const entry = repo.history.find((x) => x.file === file) || repo.latest;
  setMain(skeletonGrid(4));
  const snap = await loadSnapshot(file);
  if (token !== renderToken) return;

  Object.assign(page, { repo, snap, file, isLatest: file === repo.latest.file, rows: [] });
  const names = repoName(snap.source, repo.repo_key);
  const { owner, name } = names;
  document.title = `${owner}/${name} · skill-atlas`;
  const src = snap.source;
  const meta = snap.repo || {};

  const snapSelect =
    repo.history.length > 1
      ? h(
          "select",
          {
            class: "select",
            "aria-label": "Snapshot",
            onchange: (e) => {
              location.hash = href("repo", repo.id, { snap: e.target.value === repo.latest.file ? null : e.target.value });
            },
          },
          repo.history.map((x, i) =>
            h("option", { value: x.file, selected: x.file === file }, `${i === 0 ? "Latest · " : ""}${when(x.scanned_at)} · ${sha8(x.commit_sha) || "no commit"}`),
          ),
        )
      : null;

  const head = h(
    "div",
    { class: "repo-head" },
    avatar(names, "lg"),
    h(
      "div",
      { class: "info" },
      h("h1", {}, h("span", { class: "owner" }, `${owner} / `), name),
      h("p", {}, meta.description || (src.kind === "local" ? src.local_path : "No description.")),
      h(
        "div",
        { class: "meta" },
        src.commit_sha ? h("span", { class: "item mono", title: src.commit_sha }, icon("commit"), `${sha8(src.commit_sha)}${src.resolved_ref ? " on " + src.resolved_ref : ""}${src.dirty ? " · uncommitted changes" : ""}`) : null,
        h("span", { class: "item", title: when(entry.scanned_at) }, icon("clock"), `Scanned ${ago(snap.scan.scanned_at)}`),
        meta.license ? h("span", { class: "item" }, icon("scale"), meta.license) : null,
        meta.stars !== undefined && meta.stars !== null ? h("span", { class: "item" }, icon("star"), num(meta.stars)) : null,
        src.url ? h("a", { class: "item", href: src.url, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "View on GitHub") : null,
      ),
    ),
    h(
      "div",
      { class: "actions" },
      snapSelect,
      repo.rescan_target
        ? h("button", { class: "btn", onclick: () => scanDialog.open(repo.rescan_target, true) }, icon("refresh"), "Rescan")
        : null,
      h("a", { class: "btn", href: `/api/snapshot/raw?file=${encodeURIComponent(file)}`, download: file }, icon("download"), "Export JSON"),
    ),
  );

  const oldNotice = page.isLatest
    ? null
    : h(
        "div",
        { class: "callout notice" },
        icon("history"),
        h("div", {}, `You are viewing an older snapshot from ${when(entry.scanned_at)}. `, h("a", { href: href("repo", repo.id) }, "Go to the latest")),
      );

  const tab = ["skills", "scan", "history"].includes(params.get("tab")) ? params.get("tab") : "skills";
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const body = h("div");
  const tabDefs = [
    ["skills", "Skills", snap.skills.length],
    ["scan", "Scan details", null],
    ["history", "History", repo.history.length],
  ];
  function showTab(t) {
    setParams({ tab: t === "skills" ? null : t });
    tabs.replaceChildren(
      ...tabDefs.map(([key, label, count]) =>
        h("button", { class: key === t ? "active" : null, role: "tab", onclick: () => showTab(key) }, label, count !== null ? h("span", { class: "count" }, num(count)) : null),
      ),
    );
    if (t === "scan") body.replaceChildren(scanDetails(snap, file));
    else if (t === "history") body.replaceChildren(historyView(repo, file));
    else body.replaceChildren(skillsTab());
  }

  setMain(
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "Repositories"), icon("chevron"), h("span", {}, `${owner}/${name}`)),
    head,
    oldNotice,
    tabs,
    body,
  );
  showTab(tab);
  const skillId = params.get("skill");
  if (skillId) openSkill(skillId, false);
}

function skillsTab() {
  const snap = page.snap;
  const f = repoFilters(route().params);
  const search = h("input", {
    type: "search",
    placeholder: "Search skills by name, description or path",
    value: f.q,
    "aria-label": "Search skills",
    oninput: (e) => {
      setParams({ q: e.target.value });
      update();
    },
  });
  const segmented = h("div", { class: "segmented", role: "radiogroup", "aria-label": "Relevance" });
  const selects = h("div", { class: "toolbar" });
  const catChips = h("div", { class: "badges" });
  const groupToggle = h("input", {
    type: "checkbox",
    checked: f.group,
    onchange: (e) => {
      setParams({ group: e.target.checked ? null : "0" });
      update();
    },
  });
  const line = h("div", { class: "result-line" });
  const grid = h("div", { class: "grid" });

  function select(name, label, options) {
    const current = repoFilters(route().params)[name];
    return h(
      "select",
      {
        class: "select",
        "aria-label": label,
        onchange: (e) => {
          setParams({ [name]: e.target.value === "all" ? null : e.target.value });
          update();
        },
      },
      options.map(([v, text]) => h("option", { value: v, selected: v === current }, text)),
    );
  }

  function update() {
    const f = repoFilters(route().params);
    const others = snap.skills.filter((s) => matchesOthers(s, f));
    const count = (cat) => others.filter((s) => matchesCategory(s, cat)).length;
    segmented.replaceChildren(
      ...[["relevant", "Relevant"], ["auxiliary", "Tests & examples"], ["all", "All"]].map(([v, label]) =>
        h(
          "button",
          {
            class: f.cat === v || (v === "all" && !["relevant", "auxiliary", "all"].includes(f.cat)) ? "active" : null,
            role: "radio",
            onclick: () => {
              setParams({ cat: v === "relevant" ? null : v });
              update();
            },
          },
          label,
          h("span", { class: "count" }, num(count(v))),
        ),
      ),
    );
    const cats = {};
    for (const s of others) cats[s.category || "none"] = (cats[s.category || "none"] || 0) + 1;
    catChips.replaceChildren(
      ...Object.entries(cats)
        .sort((a, b) => b[1] - a[1])
        .map(([cat, n]) =>
          h(
            "button",
            {
              class: `badge cat-${cat}${f.cat === cat ? " active" : ""}`,
              title: f.cat === cat ? "Show all categories" : `Only ${CATEGORY_LABEL[cat] || cat}`,
              onclick: () => {
                setParams({ cat: f.cat === cat ? "all" : cat });
                update();
              },
            },
            `${CATEGORY_LABEL[cat] || "Uncategorized"} ${num(n)}`,
          ),
        ),
    );
    const matching = others.filter((s) => matchesCategory(s, f.cat));
    page.rows = groupCopies(matching, f.group);
    const shown = matching.length;
    const hidden = others.length - shown;
    const parts = [`${plural(page.rows.length, "result")}`];
    if (page.rows.length < shown) parts.push(`${num(shown)} entries, identical copies grouped`);
    if (hidden) parts.push(`${num(hidden)} hidden by the relevance filter`);
    line.textContent = parts.join(" · ");
    grid.replaceChildren(...page.rows.map(skillCard));
    if (!page.rows.length) {
      grid.replaceChildren(
        emptyState(
          "search",
          snap.skills.length ? "No skills match these filters" : "No skills in this snapshot",
          snap.skills.length ? "Clear the search or switch to All." : "The scanner found no agent skills, commands or agents here.",
        ),
      );
    }
  }

  const kinds = [["all", "Skills & agents"], ["skill", "Skills"], ["agent", "Agents"]];
  const types = [["all", "Any type"], ...snap.types.map((t) => [t, TYPE_LABEL[t] || t])];
  const comps = [["all", "Any status"], ["compliant", "Compliant"], ["loadable", "Spec issues"], ["broken", "Broken"]];
  append(selects, [
    h("label", { class: "field" }, icon("search"), search, h("kbd", {}, "/")),
    segmented,
    h("span", { class: "spacer" }),
    select("kind", "Kind", kinds),
    select("type", "Type", types),
    select("comp", "Compliance", comps),
    h("label", { class: "switch" }, groupToggle, "Group copies"),
  ]);
  update();
  return h("div", {}, selects, h("div", { class: "toolbar" }, catChips), line, grid);
}

function skillCard(row) {
  const s = row.skill;
  const aux = !s.relevant;
  return h(
    "button",
    { class: `card${aux ? " aux" : ""}`, onclick: () => openSkill(s.id) },
    h(
      "div",
      { class: "card-head" },
      h("div", { class: `avatar ${hue(s.type)}` }, icon(kindIcon(s))),
      h("div", {}, h("div", { class: "card-title" }, s.name || "(unnamed)"), h("div", { class: "card-sub" }, TYPE_LABEL[s.type] || s.type)),
    ),
    h("div", { class: "card-desc three" }, s.description || "No description."),
    h(
      "div",
      { class: "badges" },
      categoryBadge(s.category),
      complianceBadge(s.compliance.status),
      row.copies.length ? h("span", { class: "badge accent" }, icon("layers"), `${row.copies.length + 1} locations`) : null,
    ),
    h("div", { class: "card-path" }, s.path || s.source_pointer),
  );
}

function scanDetails(snap, file) {
  const scan = snap.scan;
  const src = snap.source;
  const o = scan.options;
  const props = (pairs) =>
    h("dl", { class: "props" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]));
  const panel = (title, content) => h("div", { class: "panel" }, h("div", { class: "panel-head" }, title), h("div", { class: "panel-body" }, content));
  return h(
    "div",
    {},
    h(
      "div",
      { class: "panels" },
      panel(
        "When and how",
        props([
          ["Scanned", `${when(scan.scanned_at)} (${ago(scan.scanned_at)})`],
          ["Duration", `${(scan.duration_ms / 1000).toFixed(1)} s`],
          ["Fetch method", { api: "GitHub API", clone: "git clone (large repository)", fs: "working tree on disk", git: "local git revision" }[scan.fetch_method] || scan.fetch_method],
          ["Tool", `skill-atlas ${scan.tool_version}, detectors v${scan.detectors_version}`],
          ["Scan ID", scan.id],
          ["Snapshot file", file],
        ]),
      ),
      panel(
        "Source",
        props([
          ["Kind", src.kind === "github" ? "GitHub" : "Local"],
          ["Location", src.url || src.local_path],
          ["Requested ref", src.requested_ref],
          ["Resolved ref", src.resolved_ref],
          ["Commit", src.commit_sha],
          ["Commit date", src.commit_date ? when(src.commit_date) : null],
          ["Working tree", src.dirty === null ? null : src.dirty ? "uncommitted changes" : "clean"],
        ]),
      ),
      panel(
        "Options and result",
        props([
          ["Subdirectory", o.path || "whole repository"],
          ["Include", o.include.join(", ") || "—"],
          ["Exclude", o.exclude.join(", ") || "—"],
          ["Excluded candidates", num(scan.excluded_candidates)],
          ["Entries", `${plural(snap.stats.skills, "skill")}, ${plural(snap.stats.agents, "agent")}, ${num(snap.stats.external)} external`],
          ["Plugins", num(snap.plugins.length)],
        ]),
      ),
    ),
    scan.warnings.length
      ? h("div", { class: "panel", id: "scan-warnings" }, h("div", { class: "panel-head" }, `Scan warnings (${scan.warnings.length})`), h("div", { class: "panel-body" }, scan.warnings.map((w) => h("div", { class: "callout warn" }, icon("alert"), h("div", {}, w)))))
      : null,
  );
}

function historyView(repo, file) {
  const items = repo.history;
  return h(
    "div",
    { class: "panel" },
    h(
      "ul",
      { class: "timeline" },
      items.map((x, i) => {
        const prev = items[i + 1];
        const delta = prev ? x.skills - prev.skills : null;
        return h(
          "li",
          { class: x.file === file ? "current" : null },
          h("span", { class: "when" }, when(x.scanned_at)),
          h("span", { class: "mono muted" }, sha8(x.commit_sha) || "no commit", x.dirty ? " · dirty" : ""),
          h("span", {}, plural(x.skills, "skill")),
          delta ? h("span", { class: `delta ${delta > 0 ? "up" : "down"}` }, `${delta > 0 ? "+" : ""}${delta}`) : null,
          h("span", { class: "badge outline" }, x.fetch_method),
          i === 0 ? h("span", { class: "badge accent" }, "latest") : null,
          h("span", { class: "spacer" }),
          x.file === file
            ? h("span", { class: "muted" }, "viewing")
            : h("a", { class: "btn sm", href: href("repo", repo.id, { snap: i === 0 ? null : x.file }) }, "View"),
        );
      }),
    ),
  );
}

// --- skill drawer -------------------------------------------------------------------

let drawer = null;

function closeDrawer(updateUrl = true) {
  if (!drawer) return;
  drawer.scrim.remove();
  drawer.panel.remove();
  drawer = null;
  if (updateUrl) setParams({ skill: null, dtab: null });
}

function openSkill(id, updateUrl = true) {
  const snap = page.snap;
  const s = snap && snap.skills.find((x) => x.id === id);
  if (!s) return;
  closeDrawer(false);
  if (updateUrl) setParams({ skill: id });
  const row = page.rows.find((r) => r.skill.id === id);
  const copies = row ? row.copies : snap.skills.filter((x) => x.id !== id && x.dup_key === s.dup_key);

  const tabsEl = h("div", { class: "tabs", role: "tablist" });
  const body = h("div", { class: "drawer-body" });
  const sim = { data: null, error: null };
  let current = "overview";
  function defs() {
    const n = sim.data ? sim.data.similar.length : null;
    return [
      ["overview", "Overview", null],
      ["content", "Content", null],
      ["files", "Files", s.resources.length || null],
      ["similar", "Similar", n],
    ];
  }
  function drawTabs() {
    tabsEl.replaceChildren(
      ...defs().map(([k, label, count]) =>
        h("button", { class: k === current ? "active" : null, role: "tab", onclick: () => show(k) }, label, count !== null ? h("span", { class: "count" }, num(count)) : null),
      ),
    );
  }
  function show(t) {
    current = t;
    setParams({ dtab: t === "overview" ? null : t });
    drawTabs();
    const views = { content: () => contentTab(s), files: () => filesTab(s), similar: () => similarTab(s, sim), overview: () => overviewTab(s, copies) };
    body.replaceChildren(...views[t]().filter(Boolean));
    body.scrollTop = 0;
  }

  const panel = h(
    "aside",
    { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": s.name || "skill" },
    h(
      "div",
      { class: "drawer-head" },
      h(
        "div",
        { class: "top" },
        h("div", { class: `avatar ${hue(s.type)}` }, icon(kindIcon(s))),
        h("h2", {}, s.name || "(unnamed)"),
        s.permalink ? h("a", { class: "btn sm", href: s.permalink, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "GitHub") : null,
        h("button", { class: "btn ghost icon-only", "aria-label": "Close", onclick: () => closeDrawer() }, icon("x")),
      ),
      h("div", { class: "badges" }, h("span", { class: "badge outline" }, TYPE_LABEL[s.type] || s.type), categoryBadge(s.category), complianceBadge(s.compliance.status)),
      tabsEl,
    ),
    body,
  );
  const scrim = h("div", { class: "scrim", onclick: () => closeDrawer() });
  document.body.append(scrim, panel);
  drawer = { scrim, panel };
  const dtab = route().params.get("dtab");
  show(["content", "files", "similar"].includes(dtab) ? dtab : "overview");
  panel.querySelector(".btn.ghost").focus();
  loadSimilar(page.file, s.id).then(
    (data) => (sim.data = data),
    (err) => (sim.error = err.message),
  ).then(() => {
    if (drawer?.panel !== panel) return; // closed or replaced meanwhile
    if (current === "similar") show("similar");
    else drawTabs();
  });
}

const similarCache = new Map();

function loadSimilar(file, id) {
  const key = `${file}\n${id}`;
  if (!similarCache.has(key)) {
    const request = api(`/api/similar?file=${encodeURIComponent(file)}&id=${encodeURIComponent(id)}`);
    similarCache.set(key, request);
    request.catch(() => similarCache.delete(key));
  }
  return similarCache.get(key);
}

// Rounded down, so a 99.6% match never reads as 100%.
const pct = (x) => `${Math.floor(x * 100 + 1e-9)}%`;
const LEVEL_LABEL = { "near-identical": "Near-identical", strong: "Strong overlap", related: "Related" };

function similarTab(s, sim) {
  if (sim.error) return [h("div", { class: "callout bad" }, icon("alert"), h("div", {}, `Could not compare: ${sim.error}`))];
  if (!sim.data) return [h("div", { class: "loading" }, h("span", { class: "spinner" }), "Comparing with the other skills…")];
  const { threshold, similar } = sim.data;
  const intro = h(
    "p",
    { class: "muted" },
    `Skills in this snapshot that are at least ${pct(threshold)} similar, by shared vocabulary or copied text. Identical copies are under Overview → Locations.`,
  );
  if (!similar.length) return [intro, emptyState("layers", "No similar skills", `No other skill in this snapshot reaches ${pct(threshold)}.`)];
  return [
    intro,
    h(
      "ul",
      { class: "similar" },
      similar.map((m) =>
        h(
          "li",
          {},
          h(
            "button",
            {
              class: "similar-item",
              onclick: () => {
                setParams({ dtab: null });
                openSkill(m.id);
              },
            },
            h(
              "div",
              { class: "similar-head" },
              h("span", { class: `score ${m.level}`, title: LEVEL_LABEL[m.level] }, pct(m.score)),
              h("div", { class: "grow" }, h("div", { class: "card-title" }, m.name || "(unnamed)"), h("div", { class: m.same_name ? "card-path strong" : "card-path" }, m.path)),
              h("span", { class: `badge level-${m.level}` }, LEVEL_LABEL[m.level]),
            ),
            m.description ? h("div", { class: "card-desc" }, m.description) : null,
            h(
              "dl",
              { class: "props compact" },
              h("dt", {}, "Vocabulary"),
              h("dd", {}, meter(m.topic), pct(m.topic)),
              h("dt", {}, "Shared text"),
              h("dd", {}, `${pct(m.overlap_here)} of this skill is in that one · ${pct(m.overlap_there)} of that one is in this skill`),
            ),
            h(
              "div",
              { class: "badges" },
              m.same_name ? h("span", { class: "badge warn", title: "Same name in another location, but the files differ" }, icon("layers"), "Same name, different content") : null,
              categoryBadge(m.category),
              h("span", { class: "badge outline" }, TYPE_LABEL[m.type] || m.type),
              m.copies ? h("span", { class: "badge accent" }, icon("layers"), `${m.copies + 1} locations`) : null,
            ),
            m.shared_terms.length ? h("div", { class: "terms" }, h("span", { class: "muted" }, "Shared terms"), m.shared_terms.map((t) => h("code", {}, t))) : null,
          ),
        ),
      ),
    ),
  ];
}

function meter(x) {
  const bar = h("span", { class: "meter", role: "img", "aria-label": pct(x) }, h("span", {}));
  bar.firstChild.style.width = pct(x); // CSSOM, allowed by the CSP
  return bar;
}

function copyButton(text) {
  return h(
    "button",
    {
      class: "btn ghost icon-only sm",
      title: "Copy path",
      "aria-label": "Copy path",
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Path copied");
        } catch (_) {
          toast("Clipboard is not available", "bad");
        }
      },
    },
    icon("copy"),
  );
}

function locationRow(path, label, permalinkUrl) {
  return h(
    "li",
    {},
    h("span", { class: "path" }, path),
    label ? h("span", { class: "badge" }, label) : null,
    copyButton(path),
    permalinkUrl ? h("a", { class: "btn ghost icon-only sm", href: permalinkUrl, target: "_blank", rel: "noopener noreferrer", title: "Open on GitHub", "aria-label": "Open on GitHub" }, icon("external")) : null,
  );
}

function overviewTab(s, copies) {
  const violations = s.compliance.violations;
  const where = s.path || s.source_pointer;
  const details = [
    ["Category", s.category ? `${CATEGORY_LABEL[s.category] || s.category} — ${s.category_reason}` : "Uncategorized (older snapshot)"],
    ["Kind", s.kind === "agent" ? "Agent" : "Skill"],
    ["Format", `${TYPE_LABEL[s.type] || s.type} (detector ${s.detector})`],
    ["Scope", s.scope],
    ["Plugin", s.plugin_id],
    ["Name from", s.name_source],
    ["Description from", s.description_source],
    ["Size", s.body_bytes === null ? null : `${bytes(s.body_bytes)}, ${plural(s.body_lines, "line")}`],
    ["SHA-256", s.content_sha256],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  return [
    h("div", { class: "section" }, h("h3", {}, "Description"), h("p", { class: "desc" }, s.description || "No description.")),
    violations.length
      ? h(
          "div",
          { class: "section" },
          h("h3", {}, s.compliance.status === "broken" ? "Why it is broken" : "Spec issues"),
          violations.map((v) => h("div", { class: `callout ${s.compliance.status === "broken" ? "bad" : "warn"}` }, icon("alert"), h("div", {}, h("span", { class: "code" }, v.code), ` — ${v.message}`))),
        )
      : null,
    s.warnings.length ? h("div", { class: "section" }, h("h3", {}, "Warnings"), s.warnings.map((w) => h("div", { class: "callout warn" }, icon("alert"), h("div", {}, w)))) : null,
    h(
      "div",
      { class: "section" },
      h("h3", {}, copies.length ? `Locations (${copies.length + 1})` : "Location"),
      h(
        "ul",
        { class: "locations" },
        locationRow(where, copies.length ? "shown" : null, s.permalink),
        copies.map((c) => locationRow(c.path || c.source_pointer, "identical copy", c.permalink)),
        s.aliases.map((a) => locationRow(a, "symlink", null)),
      ),
    ),
    h("div", { class: "section" }, h("h3", {}, "Details"), h("dl", { class: "props" }, details.map(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]))),
  ];
}

function contentTab(s) {
  const out = [];
  if (s.frontmatter_raw !== null) {
    out.push(h("details", { class: "fm" }, h("summary", {}, "Frontmatter"), h("pre", {}, s.frontmatter_raw)));
  }
  if (s.frontmatter_error) out.push(h("div", { class: "callout warn" }, icon("alert"), h("div", {}, s.frontmatter_error)));
  if (s.body_html === null) {
    out.push(emptyState("file", "No content", s.type === "external-plugin" ? "This plugin lives in another repository and was not scanned." : "The file could not be read as text."));
    return out;
  }
  const md = h("div", { class: "markdown" });
  md.innerHTML = s.body_html; // server-rendered with raw HTML disabled; see the file header
  out.push(md);
  return out;
}

function filesTab(s) {
  if (!s.resources.length) return [emptyState("folder", "No bundled files", "This skill has no scripts, references or assets next to it.")];
  const total = s.resources.reduce((a, r) => a + (r.bytes || 0), 0);
  return [
    h("p", { class: "muted" }, `${plural(s.resources.length, "file")}${total ? ", " + bytes(total) : ""}`),
    h("ul", { class: "files" }, s.resources.map((r) => h("li", {}, icon("file"), h("span", { class: "path" }, r.path), h("span", { class: "size" }, bytes(r.bytes))))),
  ];
}

// --- skills across repositories -----------------------------------------------------

async function renderSkills(params, token) {
  document.title = "Skills · skill-atlas";
  const cat = ["relevant", "auxiliary", "all"].includes(params.get("cat")) ? params.get("cat") : "relevant";
  if (!cache.skills.has(cat)) setMain(skeletonGrid(4));
  if (!cache.skills.has(cat)) cache.skills.set(cat, (await api(`/api/skills?category=${cat}`)).skills);
  const [skills, data] = [cache.skills.get(cat), await loadRepos()];
  if (token !== renderToken) return;

  const search = h("input", {
    type: "search",
    placeholder: "Search skills in every repository",
    value: params.get("q") || "",
    "aria-label": "Search skills",
    oninput: (e) => {
      setParams({ q: e.target.value });
      update();
    },
  });
  const segmented = h(
    "div",
    { class: "segmented", role: "radiogroup", "aria-label": "Relevance" },
    [["relevant", "Relevant"], ["auxiliary", "Tests & examples"], ["all", "All"]].map(([v, label]) =>
      h("button", { class: v === cat ? "active" : null, role: "radio", onclick: () => (location.hash = href("skills", null, { cat: v === "relevant" ? null : v, q: route().params.get("q") })) }, label),
    ),
  );
  const line = h("div", { class: "result-line" });
  const list = h("div", { class: "group-list" });

  function update() {
    const q = (route().params.get("q") || "").toLowerCase();
    const groups = new Map();
    for (const s of skills) {
      if (q && !`${s.name || ""} ${s.description} ${s.repo_key}`.toLowerCase().includes(q)) continue;
      const key = (s.name || "(unnamed)").toLowerCase();
      if (!groups.has(key)) groups.set(key, { name: s.name || "(unnamed)", items: [] });
      groups.get(key).items.push(s);
    }
    const sorted = [...groups.values()].sort((a, b) => new Set(b.items.map((i) => i.repo_id)).size - new Set(a.items.map((i) => i.repo_id)).size || a.name.localeCompare(b.name));
    const shown = sorted.slice(0, 300);
    line.textContent = `${plural(sorted.length, "skill name")} across ${plural(data.repos.length, "repository", "repositories")}${sorted.length > shown.length ? ` · showing the first ${shown.length}` : ""}`;
    list.replaceChildren(...shown.map(groupCard));
    if (!shown.length) list.replaceChildren(emptyState("search", "No skills found", q ? "Try another search." : "Scan a repository first."));
  }

  setMain(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Skills"), h("div", { class: "sub" }, "Every skill from the latest snapshot of each repository, grouped by name."))),
    h("div", { class: "toolbar" }, h("label", { class: "field big" }, icon("search"), search, h("kbd", {}, "/")), segmented),
    line,
    list,
  );
  update();
}

function groupCard(g) {
  const repos = new Map();
  for (const s of g.items) if (!repos.has(s.repo_id)) repos.set(s.repo_id, s);
  const variants = new Set(g.items.map((s) => s.content_sha256 || s.id)).size;
  const first = g.items[0];
  return h(
    "div",
    { class: "card group" },
    h(
      "div",
      { class: "group-head" },
      h("span", { class: "name" }, g.name),
      h("span", { class: "badge outline" }, TYPE_LABEL[first.type] || first.type),
      h("span", { class: "badge accent" }, plural(repos.size, "repository", "repositories")),
      variants > 1 ? h("span", { class: "badge warn", title: "Different file contents under the same name" }, `${variants} variants`) : null,
    ),
    h("div", { class: "card-desc three" }, first.description || "No description."),
    h(
      "div",
      { class: "repos" },
      [...repos.values()].map((s) => {
        const { owner, name } = repoName(repoById(s.repo_id)?.source, s.repo_key);
        return h("a", { class: `badge cat-${s.category || "none"}`, href: href("repo", s.repo_id, { skill: s.id, cat: "all" }), title: s.path }, `${owner}/${name}`);
      }),
    ),
  );
}

// --- scan dialog ------------------------------------------------------------------

const scanDialog = (() => {
  const dialog = h("dialog", { class: "modal", "aria-labelledby": "scan-title" });
  const input = h("input", { type: "text", name: "target", placeholder: "owner/repo, GitHub URL or local path", autocomplete: "off", spellcheck: "false", "aria-label": "Repository" });
  const content = h("div", { class: "modal-body" });
  let job = null;
  let timer = null;
  document.body.append(dialog);
  dialog.addEventListener("close", () => updatePill());

  function example(text) {
    return h("button", { class: "badge outline", type: "button", onclick: () => { input.value = text; input.focus(); } }, text);
  }

  function renderForm(error) {
    content.replaceChildren(
      h(
        "form",
        {
          onsubmit: (e) => {
            e.preventDefault();
            start(input.value);
          },
        },
        h("label", { class: "field big" }, icon("search"), input),
        h("p", { class: "hint" }, "The scan saves a snapshot and reuses it when the commit has not changed."),
        h("div", { class: "examples" }, example("anthropics/skills"), example("https://github.com/JetBrains/koog")),
        error ? h("div", { class: "callout bad" }, icon("alert"), h("div", {}, error)) : null,
        h(
          "div",
          { class: "modal-foot" },
          h("button", { class: "btn", type: "button", onclick: () => dialog.close() }, "Cancel"),
          h("button", { class: "btn primary", type: "submit" }, "Scan"),
        ),
      ),
    );
  }

  function renderProgress() {
    const steps = job.stages.map(([name, secs]) => h("li", {}, h("span", { class: "done" }, icon("check")), name, h("span", { class: "time" }, `${secs.toFixed(1)} s`)));
    if (job.state === "running") {
      const [stage, ...rest] = job.status.split(" · ");
      steps.push(h("li", { class: "current" }, h("span", { class: "spinner" }), h("span", {}, stage, rest.length ? h("span", { class: "detail" }, ` · ${rest.join(" · ")}`) : null), h("span", { class: "time" }, `${job.elapsed.toFixed(1)} s`)));
    }
    content.replaceChildren(
      h("div", { class: "target-line" }, job.target),
      h("ul", { class: "steps" }, steps),
      h(
        "div",
        { class: "modal-foot" },
        h("button", { class: "btn", type: "button", onclick: () => dialog.close() }, "Run in background"),
      ),
    );
  }

  async function start(target) {
    target = target.trim();
    if (!target) {
      input.focus();
      return;
    }
    try {
      job = await api("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Skill-Atlas": "1" },
        body: JSON.stringify({ target }),
      });
    } catch (e) {
      renderForm(e.message);
      return;
    }
    renderProgress();
    updatePill();
    poll();
  }

  async function poll() {
    try {
      job = await api(`/api/scan/${job.id}`);
    } catch (e) {
      finish({ state: "error", error: e.message });
      return;
    }
    if (job.state === "running") {
      if (dialog.open) renderProgress();
      updatePill();
      timer = setTimeout(poll, 400);
      return;
    }
    finish(job);
  }

  async function finish(done) {
    clearTimeout(timer);
    job = null;
    updatePill();
    if (done.state === "error") {
      // The open dialog shows the error itself; a toast covers the background case.
      if (!dialog.open) toast(`Scan failed: ${done.error}`, "bad");
      input.value = done.target || input.value;
      renderForm(done.error);
      return;
    }
    invalidate();
    cache.snapshots.delete(done.file);
    dialog.close();
    const repos = await loadRepos(true);
    const repo = repos.repos.find((r) => r.latest.file === done.file) || repos.repos.find((r) => r.repo_key === done.repo_key);
    const { owner, name } = repoName(repo?.source, done.repo_key);
    toast(`${owner}/${name}: ${done.cache_hit ? "already up to date, opened the saved snapshot" : "scan complete"}`);
    if (repo) go(href("repo", repo.id));
    else render();
  }

  function updatePill() {
    const running = Boolean(job);
    shell.pill.hidden = !running || dialog.open;
    if (running) shell.pill.replaceChildren(h("span", { class: "spinner" }), `Scanning ${job.target} · ${job.elapsed.toFixed(0)} s`);
  }

  return {
    open(target = "", autostart = false) {
      if (job) {
        renderProgress();
      } else {
        input.value = target;
        renderForm();
      }
      dialog.replaceChildren(
        h("div", { class: "modal-head" }, h("h2", { id: "scan-title" }, "Scan a repository"), h("button", { class: "btn ghost icon-only", "aria-label": "Close", onclick: () => dialog.close() }, icon("x"))),
        content,
      );
      if (!dialog.open) dialog.showModal();
      updatePill();
      if (!job) {
        if (autostart && target) start(target);
        else input.focus();
      }
    },
  };
})();

// --- keyboard -----------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) && !["checkbox", "radio"].includes(e.target.type);
  if (e.key === "Escape") {
    if (document.querySelector("dialog[open]")) return; // the dialog closes itself
    if (drawer) {
      e.preventDefault();
      closeDrawer();
      return;
    }
    if (typing) e.target.blur();
    return;
  }
  if (typing) return;
  if (e.key === "/") {
    const field = shell.main.querySelector('input[type="search"]');
    if (field && !drawer) {
      e.preventDefault();
      field.focus();
    }
  }
});

window.addEventListener("hashchange", render);
buildShell();
render();

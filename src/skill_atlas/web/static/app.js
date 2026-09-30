"use strict";
// skill-atlas web UI. Mirrors the TUI: repositories -> snapshot -> skills.
// Every string from the server is inserted as text. The one exception is
// `body_html`, which the server renders from Markdown with raw HTML disabled;
// the page CSP forbids inline scripts on top of that.

const TABS = ["overview", "frontmatter", "body", "resources", "warnings", "scan"];
const CATEGORY = ["relevant", "auxiliary", "all"];
const KIND = ["skill", "agent", "all"];
const COMPLIANCE = ["all", "compliant", "loadable", "broken"];

const root = document.getElementById("app");
const state = {
  repos: null, // /api/repos payload
  repoRows: [],
  snap: null, // /api/snapshot payload
  file: null,
  rows: [], // [{skill, copies}]
  sel: 0,
  job: null,
};
let refs = {};

// --- helpers -----------------------------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const dash = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));
const sha8 = (sha) => (sha ? sha.slice(0, 8) : "—");
const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");

function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) dl.append(h("dt", {}, k), h("dd", {}, dash(v)));
  return dl;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try {
    data = await r.json();
  } catch (_) {
    /* empty body */
  }
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

let toastTimer = null;
function toast(message, isError = false) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const el = h("div", { class: isError ? "toast error" : "toast", role: "status" }, message);
  document.body.append(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), isError ? 10000 : 4000);
}

// --- routing -----------------------------------------------------------------------
// #/?q=..&repo=..             repositories
// #/s/<file>?cat=..&tab=..    snapshot

function route() {
  const raw = location.hash.slice(1) || "/";
  const [path, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  if (path.startsWith("/s/")) return { view: "snap", file: decodeURIComponent(path.slice(3)), params };
  return { view: "repos", params };
}

function replaceParams(update) {
  const r = route();
  for (const [k, v] of Object.entries(update)) {
    if (v === null || v === undefined || v === "") r.params.delete(k);
    else r.params.set(k, v);
  }
  const base = r.view === "snap" ? `/s/${encodeURIComponent(r.file)}` : "/";
  const q = r.params.toString();
  history.replaceState(null, "", `#${base}${q ? "?" + q : ""}`);
}

function openSnapshot(file) {
  location.hash = `#/s/${encodeURIComponent(file)}`;
}

window.addEventListener("hashchange", render);

async function render() {
  const r = route();
  try {
    if (r.view === "snap") await renderSnapshot(r.file, r.params);
    else await renderRepos(r.params);
  } catch (e) {
    root.replaceChildren(h("p", { class: "pad error" }, e.message));
  }
}

// --- scanning ----------------------------------------------------------------------

async function startScan(target) {
  target = target.trim();
  if (!target) return;
  if (state.job) {
    toast("A scan is already running.", true);
    return;
  }
  try {
    state.job = await api("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Skill-Atlas": "1" },
      body: JSON.stringify({ target }),
    });
  } catch (e) {
    toast(`Scan failed: ${e.message}`, true);
    return;
  }
  showJob();
  pollJob();
}

async function pollJob() {
  const job = state.job;
  if (!job) return;
  try {
    state.job = await api(`/api/scan/${job.id}`);
  } catch (e) {
    state.job = null;
    toast(`Scan failed: ${e.message}`, true);
    showJob();
    return;
  }
  showJob();
  if (state.job.state === "running") {
    setTimeout(pollJob, 400);
    return;
  }
  const done = state.job;
  state.job = null;
  showJob();
  if (done.state === "error") {
    toast(`Scan failed: ${done.error}`, true);
    return;
  }
  toast(`${done.repo_key}: ${done.cache_hit ? "cached snapshot" : "new snapshot"}`);
  state.repos = null; // reload the store
  if (done.file) openSnapshot(done.file);
  else render();
}

function showJob() {
  const job = state.job;
  if (refs.scanStatus) {
    refs.scanStatus.textContent = job ? `scanning ${job.target} · ${job.status} (${job.elapsed}s)` : "";
  }
  if (refs.scanButton) refs.scanButton.disabled = Boolean(job);
}

// --- repositories view -------------------------------------------------------------

async function renderRepos(params) {
  state.snap = null;
  if (!state.repos) state.repos = await api("/api/repos");
  const data = state.repos;
  for (const w of data.warnings) console.warn(w);

  const search = h("input", {
    type: "search",
    placeholder: "search repository  ( / )",
    value: params.get("q") || "",
    oninput: (e) => {
      replaceParams({ q: e.target.value, repo: null });
      updateRepoList();
    },
  });
  const scanInput = h("input", {
    type: "text",
    placeholder: "GitHub URL, owner/repo or local path  ( n )",
    "aria-label": "Repository to scan",
  });
  const scanButton = h("button", { class: "primary", type: "submit" }, "Scan");
  const scanForm = h(
    "form",
    {
      class: "toolbar",
      onsubmit: (e) => {
        e.preventDefault();
        startScan(scanInput.value);
        scanInput.value = "";
        scanInput.blur();
      },
    },
    scanInput,
    scanButton,
    h("span", { class: "muted" }),
  );
  refs = {
    search,
    scanInput,
    scanButton,
    scanStatus: scanForm.lastChild,
    tbody: h("tbody"),
    detail: h("section", { class: "detail" }),
    status: h("span"),
  };
  root.replaceChildren(
    h(
      "header",
      { class: "bar" },
      h("h1", {}, "skill-atlas"),
      h("span", { class: "muted" }, `${data.repos.length} repositories · ${data.store}`),
      h("span", { class: "spacer" }),
      search,
    ),
    scanForm,
    h(
      "main",
      { class: "split" },
      h(
        "section",
        { class: "list" },
        h(
          "table",
          { class: "grid" },
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, "repo"),
              h("th", { class: "num" }, "skills"),
              h("th", { class: "num" }, "agents"),
              h("th", { class: "num" }, "scans"),
              h("th", {}, "last scan"),
              h("th", {}, "commit"),
            ),
          ),
          refs.tbody,
        ),
      ),
      refs.detail,
    ),
    h(
      "footer",
      { class: "status" },
      refs.status,
      h(
        "span",
        { class: "keys" },
        h("kbd", {}, "j/k"), " move ", h("kbd", {}, "Enter"), " open ",
        h("kbd", {}, "/"), " search ", h("kbd", {}, "n"), " scan",
      ),
    ),
  );
  showJob();
  updateRepoList();
}

function updateRepoList() {
  const data = state.repos;
  const q = (route().params.get("q") || "").toLowerCase();
  state.repoRows = data.repos.filter((r) => r.repo_key.toLowerCase().includes(q));
  const wanted = route().params.get("repo");
  const idx = state.repoRows.findIndex((r) => r.id === wanted);
  state.sel = idx >= 0 ? idx : 0;
  refs.tbody.replaceChildren(
    ...state.repoRows.map((r, i) =>
      h(
        "tr",
        { onclick: () => selectRepo(i), ondblclick: () => openRepo(i) },
        h("td", {}, r.repo_key),
        h("td", { class: "num" }, r.latest.skills),
        h("td", { class: "num" }, r.latest.agents),
        h("td", { class: "num" }, r.scans),
        h("td", { class: "mono" }, when(r.latest.scanned_at)),
        h("td", { class: "mono" }, sha8(r.latest.commit_sha) + (r.latest.dirty ? " dirty" : "")),
      ),
    ),
  );
  refs.status.textContent = `${state.repoRows.length}/${data.repos.length} shown · Enter opens the latest snapshot`;
  selectRepo(state.sel, false);
}

function selectRepo(i, remember = true) {
  const rows = state.repoRows;
  if (!rows.length) {
    refs.detail.replaceChildren(
      h(
        "p",
        { class: "muted" },
        state.repos.repos.length
          ? "No repositories match the search."
          : `No snapshots in ${state.repos.store}. Scan a repository above.`,
      ),
    );
    return;
  }
  state.sel = Math.max(0, Math.min(i, rows.length - 1));
  markSelected();
  if (remember) replaceParams({ repo: rows[state.sel].id });
  refs.detail.replaceChildren(...repoDetail(rows[state.sel]).filter(Boolean)); // null would render as text
}

function repoDetail(r) {
  const cats = Object.entries(r.by_category)
    .sort()
    .map(([k, v]) => `${k} ${v}`)
    .join(", ");
  const counts = `${r.latest.skills} skills, ${r.latest.agents} agents, ${r.latest.external} external`;
  const unique = r.unique < r.total ? ` · ${r.unique} unique after grouping identical copies` : "";
  const history = h(
    "table",
    { class: "grid" },
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", {}, "scanned at"),
        h("th", {}, "commit"),
        h("th", { class: "num" }, "skills"),
        h("th", { class: "num" }, "agents"),
        h("th", {}, "method"),
      ),
    ),
    h(
      "tbody",
      {},
      r.history.map((s) =>
        h(
          "tr",
          { onclick: () => openSnapshot(s.file), title: "Open this snapshot" },
          h("td", { class: "mono" }, s.scanned_at),
          h("td", { class: "mono" }, sha8(s.commit_sha) + (s.dirty ? " dirty" : "")),
          h("td", { class: "num" }, s.skills),
          h("td", { class: "num" }, s.agents),
          h("td", {}, s.fetch_method),
        ),
      ),
    ),
  );
  return [
    h("h2", {}, r.repo_key),
    r.description ? h("p", {}, r.description) : null,
    h("p", {}, counts + unique),
    cats ? h("p", { class: "muted" }, `by category: ${cats}`) : null,
    h("p", {}, h("button", { onclick: () => openSnapshot(r.latest.file) }, "Open latest snapshot")),
    h("h2", {}, "latest scan"),
    scanKV(r.latest_scan, r.source, r.latest.file),
    h("h2", {}, `history (${r.scans} scans) — click a row to open that snapshot`),
    history,
  ];
}

function openRepo(i) {
  const r = state.repoRows[i];
  if (r) openSnapshot(r.latest.file);
}

function scanKV(scan, src, file) {
  const o = scan.options;
  return kv([
    ["scanned at", scan.scanned_at],
    ["duration", `${(scan.duration_ms / 1000).toFixed(1)}s`],
    ["fetch method", scan.fetch_method],
    ["tool version", `${scan.tool_version} (detectors v${scan.detectors_version})`],
    ["scan id", scan.id],
    ["source", src.kind],
    ["location", src.url || src.local_path],
    ["requested ref", src.requested_ref],
    ["resolved ref", src.resolved_ref],
    ["commit", src.commit_sha],
    ["commit date", src.commit_date],
    ["dirty", src.dirty === null ? null : src.dirty ? "yes" : "no"],
    ["subdirectory", o.path],
    ["include", o.include.join(", ")],
    ["exclude", o.exclude.join(", ")],
    ["excluded candidates", scan.excluded_candidates],
    ["scan warnings", scan.warnings.length],
    ["snapshot file", file],
  ]);
}

// --- snapshot view -----------------------------------------------------------------

function filters() {
  const p = route().params;
  return {
    cat: p.get("cat") || "relevant",
    kind: p.get("kind") || "skill",
    type: p.get("type") || "all",
    comp: p.get("comp") || "all",
    q: p.get("q") || "",
    dedupe: p.get("dedupe") !== "0",
    tab: TABS.includes(p.get("tab")) ? p.get("tab") : "overview",
    sel: p.get("sel"),
  };
}

function select(name, options, value, label, key) {
  return h(
    "label",
    {},
    h("span", {}, label, " ", h("kbd", {}, key)),
    h(
      "select",
      {
        name,
        onchange: (e) => {
          replaceParams({ [name]: e.target.value, sel: null });
          updateSkillList();
        },
      },
      options.map((o) => h("option", { value: o, selected: o === value }, o)),
    ),
  );
}

async function renderSnapshot(file, params) {
  if (!state.snap || state.file !== file) {
    state.snap = await api(`/api/snapshot?file=${encodeURIComponent(file)}`);
    state.file = file;
  }
  const snap = state.snap;
  const f = filters();
  const src = snap.source;
  const head = [`${src.repo_key}@${sha8(src.commit_sha)}`];
  if (src.commit_date) head.push(src.commit_date);
  if (src.dirty) head.push("DIRTY");
  head.push(`${snap.stats.skills} skills, ${snap.stats.agents} agents`);

  const search = h("input", {
    type: "search",
    placeholder: "search name, description, path  ( / )",
    value: f.q,
    oninput: (e) => {
      replaceParams({ q: e.target.value, sel: null });
      updateSkillList();
    },
  });
  const dedupe = h("input", {
    type: "checkbox",
    name: "dedupe",
    checked: f.dedupe,
    onchange: (e) => {
      replaceParams({ dedupe: e.target.checked ? null : "0", sel: null });
      updateSkillList();
    },
  });
  refs = {
    search,
    dedupe,
    tbody: h("tbody"),
    tabs: h("nav", { class: "tabs" }),
    pane: h("div"),
    status: h("span"),
    open: h("a", { class: "button", target: "_blank", rel: "noopener noreferrer" }, "Open on GitHub ", h("kbd", {}, "o")),
    exportLink: h(
      "a",
      { class: "button", href: `/api/snapshot/raw?file=${encodeURIComponent(file)}`, download: file },
      "Export JSON ",
      h("kbd", {}, "e"),
    ),
  };
  root.replaceChildren(
    h(
      "header",
      { class: "bar" },
      h("button", { onclick: () => (location.hash = "#/") }, "← Repositories ", h("kbd", {}, "Esc")),
      h("span", { class: "crumb" }, head.join("  ·  ")),
      h("span", { class: "spacer" }),
      refs.open,
      refs.exportLink,
    ),
    h(
      "div",
      { class: "toolbar" },
      search,
      select("cat", CATEGORY, f.cat, "category", "g"),
      select("kind", KIND, f.kind, "kind", "f"),
      select("type", ["all", ...snap.types], f.type, "type", "t"),
      select("comp", COMPLIANCE, f.comp, "compliance", "c"),
      h("label", {}, dedupe, " group copies ", h("kbd", {}, "d")),
    ),
    h(
      "main",
      { class: "split" },
      h(
        "section",
        { class: "list" },
        h(
          "table",
          { class: "grid" },
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              ["name", "copies", "category", "type", "compliance", "path"].map((c) => h("th", {}, c)),
            ),
          ),
          refs.tbody,
        ),
      ),
      h("section", { class: "detail" }, refs.tabs, refs.pane),
    ),
    h(
      "footer",
      { class: "status" },
      refs.status,
      h(
        "span",
        { class: "keys" },
        h("kbd", {}, "j/k"), " move ", h("kbd", {}, "1–6"), " tabs ",
        h("kbd", {}, "/"), " search ", h("kbd", {}, "Esc"), " back",
      ),
    ),
  );
  updateSkillList();
}

function matches(s, f, checkCategory = true) {
  if (checkCategory) {
    if (f.cat === "relevant" && !s.relevant) return false;
    if (f.cat === "auxiliary" && s.relevant) return false;
  }
  if (f.kind !== "all" && s.kind !== f.kind) return false;
  if (f.type !== "all" && s.type !== f.type) return false;
  if (f.comp !== "all" && s.compliance.status !== f.comp) return false;
  if (f.q) {
    const hay = [s.name, s.description, s.path].filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(f.q.toLowerCase())) return false;
  }
  return true;
}

function group(skills, dedupe) {
  if (!dedupe) return skills.map((s) => ({ skill: s, copies: [] }));
  const byKey = new Map();
  const out = [];
  for (const s of skills) {
    const g = byKey.get(s.dup_key);
    if (g) g.copies.push(s);
    else {
      const row = { skill: s, copies: [] };
      byKey.set(s.dup_key, row);
      out.push(row);
    }
  }
  return out;
}

function updateSkillList() {
  const snap = state.snap;
  const f = filters();
  state.rows = group(snap.skills.filter((s) => matches(s, f)), f.dedupe);
  refs.tbody.replaceChildren(
    ...state.rows.map(({ skill: s, copies }, i) =>
      h(
        "tr",
        { class: s.relevant ? null : "aux", onclick: () => selectSkill(i) },
        h("td", {}, dash(s.name)),
        h("td", { class: "copies" }, copies.length ? `+${copies.length}` : ""),
        h("td", {}, dash(s.category)),
        h("td", {}, s.type),
        h("td", { class: s.compliance.status }, s.compliance.status),
        h("td", { class: "path" }, dash(s.path || s.source_pointer)),
      ),
    ),
  );
  const shown = state.rows.reduce((n, r) => n + 1 + r.copies.length, 0);
  const hidden = snap.skills.filter((s) => matches(s, f, false) && !matches(s, f)).length;
  let text = `${shown}/${snap.skills.length} shown`;
  if (shown > state.rows.length) text += ` in ${state.rows.length} rows`;
  if (hidden) text += ` · ${hidden} hidden by category (g)`;
  text += ` · ${snap.file}`;
  refs.status.textContent = text;
  const idx = state.rows.findIndex((r) => r.skill.id === f.sel);
  selectSkill(idx >= 0 ? idx : 0, false);
}

function selectSkill(i, remember = true) {
  if (!state.rows.length) {
    state.sel = 0;
    refs.open.removeAttribute("href");
    showTab(null);
    return;
  }
  state.sel = Math.max(0, Math.min(i, state.rows.length - 1));
  markSelected();
  const row = state.rows[state.sel];
  if (remember) replaceParams({ sel: row.skill.id });
  if (row.skill.permalink) refs.open.setAttribute("href", row.skill.permalink);
  else refs.open.removeAttribute("href");
  showTab(row);
}

function markSelected() {
  const trs = refs.tbody.children;
  for (let i = 0; i < trs.length; i++) trs[i].classList.toggle("selected", i === state.sel);
  trs[state.sel]?.scrollIntoView({ block: "nearest" });
}

function showTab(row, tab = filters().tab) {
  refs.tabs.replaceChildren(
    ...TABS.map((t, i) =>
      h(
        "button",
        {
          class: t === tab ? "active" : null,
          onclick: () => {
            replaceParams({ tab: t === "overview" ? null : t });
            showTab(state.rows[state.sel] || null, t);
          },
        },
        `${t[0].toUpperCase()}${t.slice(1)} `,
        h("kbd", {}, i + 1),
      ),
    ),
  );
  refs.pane.replaceChildren(...tabContent(tab, row).filter(Boolean));
}

function tabContent(tab, row) {
  const snap = state.snap;
  if (tab === "scan") return [scanKV(snap.scan, snap.source, snap.file)];
  if (!row) return [h("p", { class: "muted" }, "No entries match the current filters.")];
  const s = row.skill;
  if (tab === "overview") return overview(s, row.copies);
  if (tab === "frontmatter") {
    if (s.frontmatter_raw === null) return [h("p", { class: "muted" }, s.frontmatter_error || "No frontmatter.")];
    return [
      h("pre", { class: "raw" }, s.frontmatter_raw),
      s.frontmatter_error ? h("p", { class: "warning" }, `✗ ${s.frontmatter_error}`) : null,
    ];
  }
  if (tab === "body") {
    if (s.body_html === null) return [h("p", { class: "muted" }, "No content.")];
    const div = h("div", { class: "markdown" });
    div.innerHTML = s.body_html; // server-rendered, raw HTML disabled; see the file header
    return [div];
  }
  if (tab === "resources") {
    if (!s.resources.length) return [h("p", { class: "muted" }, "No resources.")];
    return [
      h(
        "table",
        { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", {}, "path"), h("th", { class: "num" }, "bytes"))),
        h(
          "tbody",
          {},
          s.resources.map((r) => h("tr", {}, h("td", { class: "path" }, r.path), h("td", { class: "num" }, dash(r.bytes)))),
        ),
      ),
    ];
  }
  // warnings
  const out = s.warnings.map((w) => h("p", { class: "warning" }, `• ${w}`));
  if (snap.scan.warnings.length) {
    out.push(h("h2", {}, "scan warnings"), ...snap.scan.warnings.map((w) => h("p", {}, `• ${w}`)));
  }
  return out.length ? out : [h("p", { class: "muted" }, "No warnings.")];
}

function overview(s, copies) {
  const size = s.body_bytes === null ? null : `${s.body_bytes} bytes, ${s.body_lines} lines`;
  const pairs = [
    ["name", s.name],
    ["kind / type", `${s.kind} / ${s.type} (${s.detector})`],
    ["category", s.category ? `${s.category} (${s.category_reason})` : null],
    ["compliance", s.compliance.status],
    ["path", s.path || s.source_pointer],
    ["scope", s.scope],
    ["plugin", s.plugin_id],
    ["name source", s.name_source],
    ["content sha256", s.content_sha256],
    ["size", size],
  ];
  if (s.aliases.length) pairs.push(["aliases", s.aliases.join(", ")]);
  for (const c of copies) pairs.push(["identical copy", c.path || c.source_pointer]);
  const out = [kv(pairs), h("h2", {}, "description"), h("p", { class: "pre" }, dash(s.description))];
  for (const v of s.compliance.violations) out.push(h("p", { class: "warning" }, `✗ ${v.code}: ${v.message}`));
  const repo = state.snap.repo;
  if (repo) {
    out.push(
      h("h2", {}, "repository"),
      kv([
        ["description", repo.description],
        ["license", repo.license],
        ["stars", repo.stars],
        ["topics", repo.topics.join(", ")],
        ["visibility", repo.visibility],
        ["archived", repo.archived],
      ]),
    );
  }
  return out;
}

// --- keyboard ----------------------------------------------------------------------

function cycle(name, options) {
  const f = route().params.get(name);
  const current = f === null ? options[0] : f;
  const next = options[(options.indexOf(current) + 1) % options.length];
  replaceParams({ [name]: next, sel: null });
  const el = document.querySelector(`select[name="${name}"]`);
  if (el) el.value = next;
  updateSkillList();
}

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const inField = ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) && e.target.type !== "checkbox";
  if (inField) {
    if (e.key === "Escape") {
      if (e.target === refs.search && e.target.value) {
        e.target.value = "";
        e.target.dispatchEvent(new Event("input"));
      }
      e.target.blur();
    } else if (e.key === "Enter" && e.target === refs.search) {
      e.target.blur();
    }
    return;
  }
  const view = route().view;
  const move = (d) => (view === "snap" ? selectSkill(state.sel + d) : selectRepo(state.sel + d));
  const keys = {
    j: () => move(1),
    ArrowDown: () => move(1),
    k: () => move(-1),
    ArrowUp: () => move(-1),
    "/": () => refs.search?.focus(),
  };
  if (view === "repos") {
    Object.assign(keys, {
      Enter: () => openRepo(state.sel),
      n: () => refs.scanInput?.focus(),
      Escape: () => {
        if (refs.search.value) {
          refs.search.value = "";
          refs.search.dispatchEvent(new Event("input"));
        }
      },
    });
  } else {
    Object.assign(keys, {
      Escape: () => (location.hash = "#/"),
      g: () => cycle("cat", CATEGORY),
      f: () => cycle("kind", KIND),
      t: () => cycle("type", ["all", ...state.snap.types]),
      c: () => cycle("comp", COMPLIANCE),
      d: () => {
        refs.dedupe.checked = !refs.dedupe.checked;
        refs.dedupe.dispatchEvent(new Event("change"));
      },
      o: () => {
        const url = state.rows[state.sel]?.skill.permalink;
        if (url) window.open(url, "_blank", "noopener,noreferrer");
        else toast("Permalink is available only for GitHub scans.", true);
      },
      e: () => refs.exportLink.click(),
    });
    TABS.forEach((t, i) => {
      keys[String(i + 1)] = () => {
        replaceParams({ tab: t === "overview" ? null : t });
        showTab(state.rows[state.sel] || null, t);
      };
    });
  }
  const action = keys[e.key];
  if (action) {
    e.preventDefault();
    action();
  }
});

render();

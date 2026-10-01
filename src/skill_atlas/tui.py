"""Textual TUI for one snapshot (SPEC section 8).

All snapshot strings go through `sanitize` and reach widgets as `rich.text.Text`
or Rich renderables, never as markup strings.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Static, TabbedContent, TabPane

from skill_atlas import aggregate, categories, crossrepo, similarity, store
from skill_atlas.errors import AtlasError
from skill_atlas.model import OrgRepo, OrgScan, Skill, Snapshot
from skill_atlas.progress import org_status_line
from skill_atlas.sanitize import sanitize, sanitize_line
from skill_atlas.views import (
    dedupe,
    org_counts_line,
    org_summary,
    owner_key,
    permalink,
    versions,
)

if TYPE_CHECKING:  # the scan modules load on the first scan, not on every TUI start
    from skill_atlas.org import OrgState
    from skill_atlas.target import OrgTarget, Target

TABS = ("overview", "frontmatter", "body", "resources", "warnings", "scan", "similar", "repos")
TAB_TITLES = {"repos": "Other repos"}
COMPLIANCE_FILTERS: tuple[str | None, ...] = (None, "compliant", "loadable", "broken")
KIND_FILTERS: tuple[str | None, ...] = ("skill", "agent", None)
CATEGORY_FILTERS = ("relevant", "auxiliary", "all")


def _s(value: object) -> str:
    return sanitize_line(str(value)) if value is not None else "—"


def header_text(snap: Snapshot) -> Text:
    src = snap.source
    sha = (src.commit_sha or "")[:8] or "no commit"
    parts = [f"{_s(src.repo_key)}@{sha}"]
    if src.commit_date:
        parts.append(_s(src.commit_date))
    if src.dirty:
        parts.append("DIRTY")
    parts.append(f"{snap.stats.skills} skills, {snap.stats.agents} agents")
    return Text("  ·  ".join(parts), style="bold")


def overview(
    snap: Snapshot,
    skill: Skill | None,
    copies: list[Skill] | None = None,
    other_versions: list[Skill] | None = None,
) -> RenderableType:
    if skill is None:
        return Text("No entries match the current filters.", style="dim")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    rows: list[tuple[str, object]] = [
        ("name", skill.name),
        ("kind / type", f"{skill.kind} / {skill.type} ({skill.detector})"),
        ("category", f"{skill.category} ({skill.category_reason})" if skill.category else None),
        ("compliance", skill.compliance.status),
        ("path", skill.path or skill.source_pointer),
        ("scope", skill.scope),
        ("plugin", skill.plugin_id),
        ("name source", skill.name_source),
        ("content sha256", skill.content_sha256),
        (
            "size",
            f"{skill.body_bytes} bytes, {skill.body_lines} lines"
            if skill.body_bytes is not None
            else None,
        ),
    ]
    if skill.aliases:
        rows.append(("aliases", ", ".join(skill.aliases)))
    for copy in copies or []:
        rows.append(("identical copy", copy.path or copy.source_pointer))
    for version in other_versions or []:
        rows.append(
            ("other version", f"{version.path or version.source_pointer} (different content)")
        )
    for label, value in rows:
        grid.add_row(label, Text(_s(value)))
    parts: list[RenderableType] = [grid, Text("")]
    parts.append(Text("description", style="bold cyan"))
    parts.append(Text(sanitize(skill.description or "—")))
    for v in skill.compliance.violations:
        parts.append(Text(f"✗ {v.code}: {sanitize_line(v.message)}", style="yellow"))
    repo = snap.repo
    if repo is not None:
        parts += [Text(""), Text("repository", style="bold cyan")]
        meta = Table.grid(padding=(0, 2))
        meta.add_column(style="cyan", no_wrap=True)
        meta.add_column()
        for label, value in (
            ("description", repo.description),
            ("license", repo.license),
            ("stars", repo.stars),
            ("topics", ", ".join(repo.topics) or None),
            ("visibility", repo.visibility),
            ("archived", repo.archived),
        ):
            meta.add_row(label, Text(_s(value)))
        parts.append(meta)
    return Group(*parts)


def frontmatter_view(skill: Skill | None) -> RenderableType:
    if skill is None:
        return Text("")
    if skill.frontmatter_raw is None:
        return Text(sanitize(skill.frontmatter_error or "No frontmatter."), style="dim")
    text = Text(sanitize(skill.frontmatter_raw))
    if skill.frontmatter_error:
        text.append(f"\n\n✗ {sanitize_line(skill.frontmatter_error)}", style="yellow")
    return text


def body_view(skill: Skill | None) -> RenderableType:
    if skill is None or skill.body is None:
        return Text("No content.", style="dim")
    return Markdown(sanitize(skill.body), hyperlinks=False)


def resources_view(skill: Skill | None) -> RenderableType:
    if skill is None or not skill.resources:
        return Text("No resources.", style="dim")
    table = Table(box=None, show_header=True, header_style="bold cyan")
    table.add_column("path")
    table.add_column("bytes", justify="right")
    for r in skill.resources:
        table.add_row(Text(_s(r.path)), Text(_s(r.bytes)))
    return table


def warnings_view(snap: Snapshot, skill: Skill | None) -> RenderableType:
    lines = Text()
    for w in skill.warnings if skill else []:
        lines.append(f"• {sanitize_line(w)}\n", style="yellow")
    if snap.scan.warnings:
        lines.append("\nscan warnings\n", style="bold cyan")
        for w in snap.scan.warnings:
            lines.append(f"• {sanitize_line(w)}\n")
    return lines if lines.plain else Text("No warnings.", style="dim")


def scan_view(snap: Snapshot, path: Path | None) -> RenderableType:
    """When and how the snapshot was made."""
    scan, src, opts = snap.scan, snap.source, snap.scan.options
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column(overflow="fold")
    rows: list[tuple[str, object]] = [
        ("scanned at", scan.scanned_at),
        ("duration", f"{scan.duration_ms / 1000:.1f}s"),
        ("fetch method", scan.fetch_method),
        ("tool version", f"{scan.tool_version} (detectors v{scan.detectors_version})"),
        ("scan id", scan.id),
        ("source", src.kind),
        ("location", src.url or src.local_path),
        ("requested ref", src.requested_ref),
        ("resolved ref", src.resolved_ref),
        ("commit", src.commit_sha),
        ("commit date", src.commit_date),
        ("dirty", None if src.dirty is None else ("yes" if src.dirty else "no")),
        ("subdirectory", opts.path),
        ("include", ", ".join(opts.include) or None),
        ("exclude", ", ".join(opts.exclude) or None),
        ("excluded candidates", scan.excluded_candidates),
        ("scan warnings", len(scan.warnings)),
        ("snapshot file", path),
    ]
    for label, value in rows:
        grid.add_row(label, Text(_s(value)))
    return grid


LEVEL_STYLE = {"near-identical": "bold red", "strong": "yellow", "related": "cyan"}


def similar_view(index: similarity.Index, skill: Skill | None) -> RenderableType:
    """Skills of the same snapshot above the similarity threshold."""
    if skill is None:
        return Text("")
    threshold = similarity.percent(similarity.THRESHOLD)
    matches = index.similar(skill)
    parts: list[RenderableType] = [
        Text(
            f"Other skills at least {threshold} similar, by shared vocabulary or copied text. "
            "Copies of this skill are on Overview.",
            style="dim",
        ),
        Text(""),
    ]
    for v in index.versions(skill):
        parts.append(
            Text(
                f"other version of this skill: {_s(v.skill.path or v.skill.source_pointer)} "
                f"(different content, {similarity.percent(v.overlap_here)} of this text is there)",
                style="magenta",
            )
        )
    if len(parts) > 2:
        parts.append(Text(""))
    if not matches:
        parts.append(Text(f"No other skill in this snapshot reaches {threshold}.", style="dim"))
    for m in matches:
        other = m.skill
        pct = similarity.percent
        head = Text()
        head.append(f"{pct(m.score):>4} ", style=LEVEL_STYLE[m.level])
        head.append(f"{m.level:<15}", style=LEVEL_STYLE[m.level])
        head.append(_s(other.name), style="bold")
        head.append(f"  {other.category or '—'} · {other.type}", style="dim")
        info = (
            f"     vocabulary {pct(m.topic)} · shared text: {pct(m.overlap_here)} of this skill "
            f"is in that one, {pct(m.overlap_there)} of that one is in this skill"
        )
        if m.copies:
            info += f" · {m.copies + 1} locations"
        parts += [
            head,
            Text(f"     {_s(other.path or other.source_pointer)}", style="dim"),
            Text(info),
            Text(f"     terms: {_s(', '.join(m.shared_terms))}", style="dim"),
            Text(""),
        ]
    return Group(*parts)


STATUS_TEXT = {"identical": "identical", "fork": "copied text", "related": "same topic"}


def crossrepo_view(
    index: crossrepo.CrossIndex | None, skill: Skill | None, exclude: set[str]
) -> RenderableType:
    """The skill in the latest snapshots of other repositories."""
    if skill is None:
        return Text("")
    if index is None:
        return Text("This snapshot is not in a store: nothing to compare with.", style="dim")
    threshold = similarity.percent(similarity.THRESHOLD)
    others = sum(1 for r in index.repos if r.ident not in exclude)
    parts: list[RenderableType] = [
        Text(
            f"This skill in the latest snapshots of {others} other repositories, matched by "
            f"content at {threshold} or more. Names are not compared.",
            style="dim",
        ),
        Text(""),
    ]
    matches = index.matches(skill, exclude)
    if not matches:
        parts.append(Text(f"No skill in the other repositories reaches {threshold}.", style="dim"))
    pct = similarity.percent
    for c in matches:
        m, other = c.match, c.match.skill
        head = Text()
        head.append(f"{pct(m.score):>4} ", style=LEVEL_STYLE[c.level])
        head.append(f"{STATUS_TEXT[c.status]:<12}", style="magenta")
        head.append(_s(c.repo.repo_key), style="cyan")
        head.append(" · ")
        head.append(_s(other.name), style="bold")
        if other.name != skill.name:
            head.append("  other name", style="yellow")
        info = (
            f"     vocabulary {pct(m.topic)} · shared text: {pct(m.overlap_here)} of this skill "
            f"is in that one, {pct(m.overlap_there)} of that one is in this skill"
        )
        if m.copies:
            info += f" · {m.copies + 1} locations"
        parts += [head, Text(f"     {_s(other.path or other.source_pointer)}", style="dim")]
        if c.status != "identical":
            parts.append(Text(info))
        parts.append(Text(""))
    return Group(*parts)


def repo_view(items: list[store.Loaded]) -> RenderableType:
    """Latest snapshot of one repository plus its scan history, newest first."""
    latest = items[-1]
    snap = latest.snapshot
    parts: list[RenderableType] = [Text(_s(snap.source.repo_key), style="bold")]
    if snap.repo is not None and snap.repo.description:
        parts.append(Text(sanitize(snap.repo.description)))
    counts = f"{snap.stats.skills} skills, {snap.stats.agents} agents, {len(snap.plugins)} plugins"
    by_category = ", ".join(f"{k} {v}" for k, v in sorted(snap.stats.by_category.items()))
    unique = len(dedupe(snap.skills))
    if unique < len(snap.skills):
        counts += f" · {unique} unique after grouping identical copies"
    parts += [
        Text(""),
        Text(counts),
        Text(f"by category: {by_category}" if by_category else "", style="dim"),
        Text(""),
        Text("latest scan", style="bold cyan"),
        scan_view(snap, latest.path),
        Text(""),
        Text(f"history ({len(items)} scans)", style="bold cyan"),
    ]
    history = Table(box=None, header_style="cyan", pad_edge=False)
    for col in ("scanned at", "commit", "skills", "agents", "method"):
        history.add_column(col, justify="right" if col in ("skills", "agents") else "left")
    for item in reversed(items):
        s = item.snapshot
        sha = (s.source.commit_sha or "")[:8] or "—"
        history.add_row(
            Text(s.scan.scanned_at),
            Text(sha + (" dirty" if s.source.dirty else "")),
            str(s.stats.skills),
            str(s.stats.agents),
            s.scan.fetch_method,
        )
    parts.append(history)
    return Group(*parts)


def org_view(report: OrgScan) -> RenderableType:
    """The latest organization scan of the selected owner (SPEC section 8)."""
    o = org_summary(report)
    kind = "organization" if o["owner_type"] == "organization" else "user"
    status_style = {"complete": "green", "partial": "yellow"}.get(o["status"], "red")
    head = Text.assemble(
        (f"{kind} {_s(o['owner'])}", "bold"),
        "  ",
        (o["status"], status_style),
        (f"  scanned {o['finished_at'][:16].replace('T', ' ')}", "dim"),
        (f" · {o['api_requests']} API requests", "dim"),
    )
    parts: list[RenderableType] = [head, Text(org_counts_line(o))]
    if o["message"]:
        parts.append(Text(_s(o["message"]), style="yellow"))
    if o["failures"]:
        parts.append(Text(f"failed ({len(o['failures'])})", style="bold red"))
        for f in o["failures"]:
            parts.append(Text(f"  {_s(f['full_name'])}: {_s(f['error'])}"))
    return Group(*parts)


SNAPSHOT_CSS = """
#header { height: 1; padding: 0 1; background: $boost; }
#left { width: 45%; min-width: 30; border-right: vkey $panel-lighten-2; }
#search, #export { display: none; }
#search.visible, #export.visible { display: block; }
#table { height: 1fr; }
#tabs { width: 1fr; padding: 0 0 0 1; }
#status { height: 1; padding: 0 1; color: $text-muted; }
"""


class SnapshotScreen(Screen[None]):
    """Skills of one snapshot. With `can_go_back`, Esc returns to the previous screen."""

    DEFAULT_CSS = SNAPSHOT_CSS
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "app.quit", "Quit"),
        Binding("escape", "cancel", "Back / cancel"),
        Binding("slash", "search", "Search"),
        Binding("g", "cycle_category", "Category"),
        Binding("f", "cycle_kind", "Kind"),
        Binding("t", "cycle_type", "Type"),
        Binding("c", "cycle_compliance", "Compliance"),
        Binding("d", "toggle_dedupe", "Copies"),
        Binding("j", "cursor(1)", "Down", show=False),
        Binding("k", "cursor(-1)", "Up", show=False),
        Binding("o", "open", "Open on GitHub"),
        Binding("e", "export", "Export"),
        *[
            Binding(str(i + 1), f"tab('{name}')", TAB_TITLES.get(name, name.title()), show=False)
            for i, name in enumerate(TABS)
        ],
    ]

    def __init__(
        self,
        snapshot: Snapshot,
        snapshot_path: Path | None = None,
        *,
        can_go_back: bool = False,
        store_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.store_dir = store_dir
        self._cross: tuple[crossrepo.CrossIndex, set[str]] | None = None
        self.snapshot = snapshot
        self.snapshot_path = snapshot_path
        self.can_go_back = can_go_back
        self.kind_filter: str | None = "skill"
        self.category_filter = "relevant"
        self.type_filter: str | None = None
        self.compliance_filter: str | None = None
        self.query_text = ""
        self.dedupe = True
        self.rows: list[Skill] = []
        self.copies: dict[str, list[Skill]] = {}
        self._by_id = {s.id: s for s in snapshot.skills}
        self._types: tuple[str | None, ...] = (None, *sorted({s.type for s in snapshot.skills}))
        self._index: similarity.Index | None = None  # built when the Similar tab first opens
        self._shown: Skill | None = None

    def compose(self) -> ComposeResult:
        yield Static(header_text(self.snapshot), id="header")
        with Horizontal():
            with Vertical(id="left"):
                yield Input(placeholder="search name, description, path", id="search")
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with TabbedContent(id="tabs", initial="overview"):
                for name in TABS:
                    with TabPane(TAB_TITLES.get(name, name.title()), id=name), VerticalScroll():
                        yield Static(id=f"{name}-view")
        yield Input(
            placeholder="export snapshot to path (Enter to save, Esc to cancel)", id="export"
        )
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("name", "copies", "category", "type", "compliance", "path")
        self.refresh_rows()
        table.focus()

    # --- data -----------------------------------------------------------------------

    def _matches(self, s: Skill, *, check_category: bool = True) -> bool:
        if check_category and not categories.matches(s.category, self.category_filter):
            return False
        if self.kind_filter and s.kind != self.kind_filter:
            return False
        if self.type_filter and s.type != self.type_filter:
            return False
        if self.compliance_filter and s.compliance.status != self.compliance_filter:
            return False
        if self.query_text:
            hay = " ".join(x or "" for x in (s.name, s.description, s.path)).lower()
            return self.query_text.lower() in hay
        return True

    def refresh_rows(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        matching = [s for s in self.snapshot.skills if self._matches(s)]
        grouped = dedupe(matching) if self.dedupe else [(s, []) for s in matching]
        self.rows = [s for s, _ in grouped]
        self.copies = {s.id: copies for s, copies in grouped}
        for s in self.rows:
            extra = len(self.copies[s.id])
            auxiliary = categories.matches(s.category, "auxiliary")
            table.add_row(
                Text(_s(s.name), style="dim" if auxiliary else ""),
                Text(f"+{extra}" if extra else "", style="magenta"),
                Text(s.category or "—", style="dim" if auxiliary else "cyan"),
                Text(s.type),
                Text(
                    s.compliance.status,
                    style={"compliant": "green", "loadable": "yellow", "broken": "red"}[
                        s.compliance.status
                    ],
                ),
                Text(_s(s.path or s.source_pointer)),
                key=s.id,
            )
        self.show_skill(self.rows[0] if self.rows else None)
        self._update_status()

    def _update_status(self) -> None:
        filters = [
            f"category={self.category_filter}",
            f"kind={self.kind_filter or 'all'}",
            f"type={self.type_filter or 'all'}",
            f"compliance={self.compliance_filter or 'all'}",
            f"copies={'grouped' if self.dedupe else 'separate'}",
        ]
        if self.query_text:
            filters.append(f"search={sanitize_line(self.query_text)!r}")
        where = str(self.snapshot_path) if self.snapshot_path else "not saved"
        shown = len(self.rows) + sum(len(c) for c in self.copies.values())
        text = f"{shown}/{len(self.snapshot.skills)} shown"
        if shown > len(self.rows):
            text += f" in {len(self.rows)} rows"
        hidden = sum(
            1
            for s in self.snapshot.skills
            if self._matches(s, check_category=False)
            and not categories.matches(s.category, self.category_filter)
        )
        if hidden:
            text += f" · {hidden} hidden by category (g)"
        text += " · " + " ".join(filters)
        self.query_one("#status", Static).update(Text(f"{text} · {where}"))

    def current(self) -> Skill | None:
        table = self.query_one("#table", DataTable)
        if not self.rows or table.cursor_row < 0 or table.cursor_row >= len(self.rows):
            return None
        return self.rows[table.cursor_row]

    def show_skill(self, skill: Skill | None) -> None:
        snap = self.snapshot
        views: dict[str, RenderableType] = {
            "overview": overview(
                snap,
                skill,
                self.copies.get(skill.id) if skill else None,
                versions(snap.skills, skill) if skill else None,
            ),
            "frontmatter": frontmatter_view(skill),
            "body": body_view(skill),
            "resources": resources_view(skill),
            "warnings": warnings_view(snap, skill),
            "scan": scan_view(snap, self.snapshot_path),
        }
        for name, renderable in views.items():
            self.query_one(f"#{name}-view", Static).update(renderable)
        self._shown = skill
        self._update_similar()

    def _update_similar(self) -> None:
        # Comparing costs a pass over the snapshot or the store: only while the tab is visible.
        active = self.query_one("#tabs", TabbedContent).active
        similar = self.query_one("#similar-view", Static)
        if active == "similar":
            if self._index is None:
                self._index = similarity.Index(self.snapshot)
            similar.update(similar_view(self._index, self._shown))
        else:
            similar.update(Text(""))
        repos = self.query_one("#repos-view", Static)
        if active == "repos":
            index, exclude = self._cross_index()
            repos.update(crossrepo_view(index, self._shown, exclude))
        else:
            repos.update(Text(""))

    def _cross_index(self) -> tuple[crossrepo.CrossIndex | None, set[str]]:
        if self.store_dir is None:
            return None, set()
        if self._cross is None:
            db = aggregate.Store.open(self.store_dir)
            index = crossrepo.CrossIndex([(db.identity(i), i) for i in db.latest()])
            name = self.snapshot_path.name if self.snapshot_path else None
            own = {db.identity(i) for i in db.snapshots if i.path.name == name}
            # An unsaved snapshot: exclude its repository by repo_key.
            own = own or {
                db.identity(i)
                for i in db.snapshots
                if i.snapshot.source.repo_key == self.snapshot.source.repo_key
            }
            self._cross = (index, own)
        return self._cross

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._update_similar()

    # --- events ---------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        self.show_skill(self._by_id.get(key) if key else None)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.query_text = event.value
            self.refresh_rows()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self.query_one("#table", DataTable).focus()
        elif event.input.id == "export":
            self._export(event.value)

    # --- actions --------------------------------------------------------------------

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_cancel(self) -> None:
        closed = False
        for widget_id in ("#search", "#export"):
            widget = self.query_one(widget_id, Input)
            if widget.has_focus:
                closed = True
                widget.remove_class("visible")
                if widget_id == "#search" and widget.value:
                    widget.value = ""
        if not closed and self.can_go_back:
            self.dismiss()
            return
        self.query_one("#table", DataTable).focus()

    def _cycle(self, options: tuple[str | None, ...], current: str | None) -> str | None:
        idx = options.index(current) if current in options else -1
        return options[(idx + 1) % len(options)]

    def action_cycle_category(self) -> None:
        idx = CATEGORY_FILTERS.index(self.category_filter)
        self.category_filter = CATEGORY_FILTERS[(idx + 1) % len(CATEGORY_FILTERS)]
        self.refresh_rows()

    def action_cycle_kind(self) -> None:
        self.kind_filter = self._cycle(KIND_FILTERS, self.kind_filter)
        self.refresh_rows()

    def action_cycle_type(self) -> None:
        self.type_filter = self._cycle(self._types, self.type_filter)
        self.refresh_rows()

    def action_cycle_compliance(self) -> None:
        self.compliance_filter = self._cycle(COMPLIANCE_FILTERS, self.compliance_filter)
        self.refresh_rows()

    def action_toggle_dedupe(self) -> None:
        self.dedupe = not self.dedupe
        self.refresh_rows()

    def action_cursor(self, delta: int) -> None:
        table = self.query_one("#table", DataTable)
        if delta > 0:
            table.action_cursor_down()
        else:
            table.action_cursor_up()

    def action_tab(self, name: str) -> None:
        self.query_one("#tabs", TabbedContent).active = name

    def action_open(self) -> None:
        skill = self.current()
        url = permalink(self.snapshot, skill) if skill else None
        if url is None:
            self.notify("Permalink is available only for GitHub scans.", severity="warning")
            return
        webbrowser.open(url)

    def action_export(self) -> None:
        widget = self.query_one("#export", Input)
        widget.add_class("visible")
        widget.focus()

    def _export(self, raw_path: str) -> None:
        widget = self.query_one("#export", Input)
        if not raw_path.strip():
            return
        path = Path(raw_path.strip()).expanduser()
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(store.dumps(self.snapshot))
        except FileExistsError:
            self.notify(f"{path} exists; choose another path.", severity="error")
            return
        except OSError as exc:
            self.notify(f"Cannot write {path}: {exc}", severity="error")
            return
        widget.remove_class("visible")
        widget.value = ""
        self.query_one("#table", DataTable).focus()
        self.notify(f"Saved {path}")


class StatusProgress:
    """Scan progress for the TUI: one status line, updated from the scan thread."""

    def __init__(self, post: Callable[[str], None]) -> None:
        self.post = post
        self._stage = ""
        self._last = 0.0

    def stage(self, message: str) -> None:
        self._stage = sanitize_line(message)
        self._last = time.monotonic()
        self.post(self._stage)

    def detail(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last >= 0.1:  # git progress can emit hundreds of lines per second
            self._last = now
            self.post(f"{self._stage} · {sanitize_line(message)}")

    def done(self) -> None:
        pass


class OrgStatusProgress:
    """Organization scan progress for the TUI. Never blocks the scan workers: they only
    store the latest line, and the screen shows it on a timer."""

    def __init__(self) -> None:
        self.text = "listing repositories"

    def update(self, state: OrgState) -> None:
        self.text = org_status_line(state)

    def finished(self, repo: OrgRepo) -> None:
        pass


class ReposScreen(Screen[None]):
    """Known repositories, latest scan first. Enter opens the latest snapshot."""

    DEFAULT_CSS = """
    #header { height: 1; padding: 0 1; background: $boost; }
    #left { width: 50%; min-width: 30; border-right: vkey $panel-lighten-2; }
    #search, #scan-target { display: none; }
    #search.visible, #scan-target.visible { display: block; }
    #repos { height: 1fr; }
    #details { width: 1fr; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "app.quit", "Quit"),
        Binding("enter", "open", "Open", show=True),
        Binding("slash", "search", "Search"),
        Binding("n", "new_scan", "Scan repo or org"),
        Binding("o", "cycle_owner", "Owner"),
        Binding("e", "toggle_empty", "Without skills"),
        Binding("x", "stop_scan", "Stop org scan", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("j", "cursor(1)", "Down", show=False),
        Binding("k", "cursor(-1)", "Up", show=False),
    ]

    def __init__(self, db: aggregate.Store, store_dir: Path) -> None:
        super().__init__()
        self.store_dir = store_dir
        self.query_text = ""
        self.scanning = False
        self.owner: str | None = None  # owner_key filter
        self.show_empty = False  # repositories whose latest snapshot has no entries
        self.cancel: threading.Event | None = None  # set while an organization scan runs
        self._org_timer: Timer | None = None
        self.repos: list[list[store.Loaded]] = []
        self.rows: list[list[store.Loaded]] = []
        self.orgs: dict[str, OrgScan] = {}
        self._load(db)

    def _load(self, db: aggregate.Store) -> None:
        self.repos = sorted(
            db.by_repo().values(),
            key=lambda items: items[-1].snapshot.scan.scanned_at,
            reverse=True,
        )
        self.orgs = store.latest_orgs(store.orgs_dir(self.store_dir))

    def owners(self) -> list[str]:
        keys = {owner_key(items[-1].snapshot.source.repo_key) for items in self.repos}
        return sorted(keys | set(self.orgs))

    def _header(self) -> Text:
        owner = f"  ·  owner {_s(self.owner)}" if self.owner else ""
        return Text(
            f"skill-atlas  ·  {len(self.repos)} repositories{owner}  ·  {self.store_dir}",
            style="bold",
        )

    def compose(self) -> ComposeResult:
        yield Static(self._header(), id="header")
        yield Input(
            placeholder=(
                "scan: GitHub URL, owner/repo, local path or https://github.com/<org> "
                "(Enter to scan, Esc to cancel)"
            ),
            id="scan-target",
        )
        with Horizontal():
            with Vertical(id="left"):
                yield Input(placeholder="search repository", id="search")
                yield DataTable(id="repos", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="details"):
                yield Static(id="details-view")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repos", DataTable)
        table.add_columns("repo", "skills", "agents", "scans", "last scan", "commit")
        self.refresh_rows()
        table.focus()

    def _scope(self) -> list[list[store.Loaded]]:
        return [
            items
            for items in self.repos
            if self.owner is None or owner_key(items[-1].snapshot.source.repo_key) == self.owner
        ]

    def refresh_rows(self) -> None:
        table = self.query_one("#repos", DataTable)
        table.clear()
        needle = self.query_text.lower()
        matching = [
            items for items in self._scope() if needle in items[-1].snapshot.source.repo_key.lower()
        ]
        self.rows = [i for i in matching if self.show_empty or i[-1].snapshot.skills]
        hidden = len(matching) - len(self.rows)
        for i, items in enumerate(self.rows):
            snap = items[-1].snapshot
            sha = (snap.source.commit_sha or "")[:8] or "—"
            table.add_row(
                Text(_s(snap.source.repo_key)),
                Text(str(snap.stats.skills), justify="right"),
                Text(str(snap.stats.agents), justify="right"),
                Text(str(len(items)), justify="right"),
                Text(snap.scan.scanned_at[:16].replace("T", " ")),
                Text(sha + (" dirty" if snap.source.dirty else "")),
                key=str(i),
            )
        self.show_repo(self.rows[0] if self.rows else None)
        if not self.scanning:
            parts = [f"{len(self.rows)}/{len(self.repos)} shown"]
            if hidden:
                parts.append(f"{hidden} without skills hidden (e shows)")
            parts += ["Enter opens the latest snapshot", "n scans a repository or organization"]
            self.set_status(" · ".join(parts))

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(Text(text))

    def show_repo(self, items: list[store.Loaded] | None) -> None:
        view: RenderableType
        if items is not None:
            view = repo_view(items)
        elif self.repos:
            view = Text("No repositories match the search and filters.", style="dim")
        else:
            view = Text(
                f"No snapshots in {self.store_dir}.\n"
                "Press n to scan a repository or an organization.",
                style="dim",
            )
        report = self.orgs.get(self.owner) if self.owner else None
        if report is not None:
            view = Group(org_view(report), Text(""), view)
        self.query_one("#details-view", Static).update(view)

    def current(self) -> list[store.Loaded] | None:
        row = self.query_one("#repos", DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = event.cursor_row
        self.show_repo(self.rows[row] if 0 <= row < len(self.rows) else None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.query_text = event.value
            self.refresh_rows()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scan-target":
            target = event.value.strip()
            if not target:
                return
            event.input.value = ""
            event.input.remove_class("visible")
            self._start_scan(target)
        self.query_one("#repos", DataTable).focus()

    # --- scanning -------------------------------------------------------------------

    def action_new_scan(self) -> None:
        if self.scanning:
            self.notify("A scan is already running.", severity="warning")
            return
        widget = self.query_one("#scan-target", Input)
        widget.add_class("visible")
        widget.focus()

    def _start_scan(self, target: str) -> None:
        self.scanning = True
        self.set_status(f"scanning {sanitize_line(target)} …")
        self.run_worker(
            lambda: self._scan_thread(target), thread=True, exclusive=True, group="scan"
        )

    def _scan_thread(self, target: str) -> None:
        # Runs in a worker thread: touch widgets only through call_from_thread.
        from skill_atlas.scan import ScanRequest, run_scan
        from skill_atlas.target import OrgTarget, parse_target

        try:
            parsed: Target | None = parse_target(target)
        except AtlasError:
            parsed = None  # run_scan reports it
        if isinstance(parsed, OrgTarget):
            self._org_thread(parsed)
            return
        call = self.app.call_from_thread
        progress = StatusProgress(lambda text: call(self.set_status, f"scanning · {text}"))
        try:
            req = ScanRequest(target=target)
            outcome = run_scan(req, store_dir=self.store_dir, progress=progress)
        except AtlasError as exc:
            call(self._scan_failed, str(exc))
            return
        except Exception as exc:  # a crash here must not take the whole TUI down
            call(self._scan_failed, f"unexpected error: {exc!r}")
            return
        call(self._scan_finished, outcome.snapshot.source.repo_key, outcome.cache_hit)

    def _org_thread(self, target: OrgTarget) -> None:
        from skill_atlas.org import OrgRequest, run_org_scan

        call = self.app.call_from_thread
        cancel = threading.Event()
        self.cancel = cancel
        progress = OrgStatusProgress()
        call(self._watch_org, progress)
        try:
            outcome = run_org_scan(
                OrgRequest(owner=target.owner, host=target.host),
                store_dir=self.store_dir,
                progress=progress,
                cancel=cancel,
            )
        except AtlasError as exc:
            call(self._scan_failed, str(exc))
            return
        except Exception as exc:  # a crash here must not take the whole TUI down
            call(self._scan_failed, f"unexpected error: {exc!r}")
            return
        finally:
            self.cancel = None
            call(self._unwatch_org)
        call(self._org_finished, outcome.report)

    def _watch_org(self, progress: OrgStatusProgress) -> None:
        self._org_timer = self.set_interval(
            0.2, lambda: self.set_status(f"scanning · {progress.text} · x stops")
        )

    def _unwatch_org(self) -> None:
        if self._org_timer is not None:
            self._org_timer.stop()
            self._org_timer = None

    def _org_finished(self, report: OrgScan) -> None:
        self.scanning = False
        self._load(aggregate.Store.open(self.store_dir))
        self.owner = report.owner_key
        self.show_empty = False
        self.query_text = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.query_one("#header", Static).update(self._header())
        self.refresh_rows()
        o = org_summary(report)
        failed = f", {len(o['failures'])} failed" if o["failures"] else ""
        self.notify(
            f"{sanitize_line(report.owner)}: {o['selected']} repositories, "
            f"{o['with_skills']} with skills{failed}",
            title=f"Organization scan {report.status}",
            severity="information" if report.status == "complete" else "warning",
            timeout=10,
        )

    def action_stop_scan(self) -> None:
        if self.cancel is None:
            return
        self.cancel.set()
        self.notify("Stopping after the repositories in progress; saved snapshots are kept.")

    def action_cycle_owner(self) -> None:
        owners: list[str | None] = [None, *self.owners()]
        self.owner = owners[(owners.index(self.owner) + 1) % len(owners)]
        self.query_one("#header", Static).update(self._header())
        self.refresh_rows()

    def action_toggle_empty(self) -> None:
        self.show_empty = not self.show_empty
        self.refresh_rows()

    def _scan_failed(self, message: str) -> None:
        self.scanning = False
        self.refresh_rows()
        self.notify(sanitize_line(message), title="Scan failed", severity="error", timeout=10)

    def _scan_finished(self, repo_key: str, cache_hit: bool) -> None:
        self.scanning = False
        self._load(aggregate.Store.open(self.store_dir))
        self.query_one("#header", Static).update(self._header())
        self.query_text = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.refresh_rows()
        keys = [items[-1].snapshot.source.repo_key for items in self.rows]
        row = keys.index(repo_key) if repo_key in keys else None
        note = "cached snapshot" if cache_hit else "new snapshot"
        self.notify(f"{sanitize_line(repo_key)}: {note}")
        if row is not None:
            self.query_one("#repos", DataTable).move_cursor(row=row)
            self.action_open()

    def action_open(self) -> None:
        items = self.current()
        if items is None:
            return
        latest = items[-1]
        self.app.push_screen(
            SnapshotScreen(latest.snapshot, latest.path, can_go_back=True, store_dir=self.store_dir)
        )

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_cancel(self) -> None:
        target = self.query_one("#scan-target", Input)
        if target.has_focus:
            target.remove_class("visible")
            target.value = ""
        else:
            search = self.query_one("#search", Input)
            search.remove_class("visible")
            if search.value:
                search.value = ""
        self.query_one("#repos", DataTable).focus()

    def action_cursor(self, delta: int) -> None:
        table = self.query_one("#repos", DataTable)
        if delta > 0:
            table.action_cursor_down()
        else:
            table.action_cursor_up()


class AtlasApp(App[None]):
    """TUI for one snapshot."""

    TITLE = "skill-atlas"

    def __init__(
        self,
        snapshot: Snapshot,
        snapshot_path: Path | None = None,
        store_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.snapshot_path = snapshot_path
        self.store_dir = store_dir

    def on_mount(self) -> None:
        self.push_screen(
            SnapshotScreen(self.snapshot, self.snapshot_path, store_dir=self.store_dir)
        )


class BrowserApp(App[None]):
    """TUI over the whole store: repositories, then the skills of one snapshot."""

    TITLE = "skill-atlas"

    def __init__(self, db: aggregate.Store, store_dir: Path) -> None:
        super().__init__()
        self.db = db
        self.store_dir = store_dir

    def on_mount(self) -> None:
        self.push_screen(ReposScreen(self.db, self.store_dir))

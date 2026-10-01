"""Command-line interface (SPEC section 3)."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from skill_atlas import __version__, aggregate, categories, favorites, store
from skill_atlas.errors import AtlasError, StoreError, UsageError
from skill_atlas.model import ORG_REPO_STATUSES, OrgScan, Snapshot
from skill_atlas.org import DEFAULT_JOBS, MAX_JOBS, NullOrgProgress, OrgRequest, run_org_scan
from skill_atlas.plain import render as render_plain
from skill_atlas.plain import render_org as render_org_plain
from skill_atlas.progress import ConsoleOrgProgress, ConsoleProgress, NullProgress
from skill_atlas.sanitize import sanitize_line
from skill_atlas.scan import ScanRequest, run_scan
from skill_atlas.target import OrgTarget, org_target, parse_target

app = typer.Typer(
    add_completion=False,
    help=(
        "Find, browse and catalogue AI agent skills in repositories. "
        "Without a command, opens the TUI over all saved snapshots."
    ),
)
out = Console(highlight=False, soft_wrap=True)
err = Console(stderr=True, highlight=False, soft_wrap=True)


PLAIN_OPTION = typer.Option(
    "--plain", help="Print a plain-text report (no TUI, no colors) for scripts and agents."
)
WITH_BODY_OPTION = typer.Option("--with-body", help="With --plain: include skill bodies.")
PINNED_OPTION = typer.Option("--pinned", help="Only pinned entries.")
PIN_MARK = "●"


def _version(value: bool) -> None:
    if value:
        out.print(f"skill-atlas {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
    web: Annotated[bool, typer.Option("--web", help="Open the web UI instead of the TUI.")] = False,
    host: Annotated[
        str, typer.Option("--web-host", help="With --web: address to listen on.")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--web-port", help="With --web: port; 0 picks a free one.")
    ] = 0,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="With --web: do not open a browser.")
    ] = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        if web:
            raise UsageError("--web works only without a command")
        return
    if web:
        from skill_atlas.web import serve

        serve(
            store.scans_dir(),
            host=host,
            port=port,
            open_browser=not no_browser,
            announce=lambda line: err.print(Text(line)),
        )
        return
    if not _interactive():
        typer.echo(ctx.get_help())
        return
    from skill_atlas.tui import BrowserApp

    db = aggregate.Store.open(store.scans_dir())
    _print_warnings(db.warnings)
    BrowserApp(db, store.scans_dir()).run()


def _cell(value: object) -> Text:
    return Text(sanitize_line(str(value)) if value is not None else "—")


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _run_tui(snapshot: Snapshot, path: Path | None) -> None:
    from skill_atlas.tui import AtlasApp

    AtlasApp(snapshot, path, store.scans_dir()).run()


def print_summary(console: Console, snapshot: Snapshot, path: Path | None, cache_hit: bool) -> None:
    src = snapshot.source
    sha = (src.commit_sha or "")[:12] or "no commit"
    dirty = " (dirty)" if src.dirty else ""
    console.print(Text(f"{sanitize_line(src.repo_key)}@{sha}{dirty}", style="bold"))
    console.print(
        f"{snapshot.stats.skills} skills, {snapshot.stats.agents} agents, "
        f"{len(snapshot.plugins)} plugins"
    )
    if snapshot.skills:
        table = Table(box=None, header_style="bold cyan", pad_edge=False)
        for col in ("kind", "name", "category", "type", "compliance", "path"):
            table.add_column(col)
        for s in snapshot.skills:
            table.add_row(
                s.kind,
                _cell(s.name),
                s.category or "—",
                s.type,
                s.compliance.status,
                _cell(s.path or s.source_pointer),
            )
        console.print(table)
    if snapshot.scan.warnings:
        console.print(f"{len(snapshot.scan.warnings)} scan warnings", style="yellow")
    if path is not None:
        label = "cached snapshot" if cache_hit else "snapshot"
        console.print(Text(f"{label}: {path}"))
    else:
        console.print("snapshot not saved (--no-save)")


def print_org_summary(console: Console, report: OrgScan, path: Path | None) -> None:
    kind = "organization" if report.owner_type == "organization" else "user"
    skipped = ", ".join(f"{n} {k}" for k, n in sorted(report.skipped.items()))
    console.print(
        Text(f"{sanitize_line(report.owner_key)} ({kind}): {report.status}", style="bold")
    )
    console.print(
        f"{report.listed} repositories listed"
        + (f", skipped: {skipped}" if skipped else "")
        + f"; {len(report.repos)} selected"
    )
    counts = ", ".join(f"{n} {s}" for s in ORG_REPO_STATUSES if (n := report.count(s)))
    console.print(
        f"{report.with_skills} with skills · {counts or 'nothing to scan'} · "
        f"{report.api_requests} API requests in {report.duration_ms / 1000:.1f}s"
    )
    found = [r for r in report.repos if (r.skills or 0) + (r.agents or 0)]
    if found:
        table = Table(box=None, header_style="bold cyan", pad_edge=False)
        for col in ("repo", "skills", "agents", "status"):
            table.add_column(col, justify="right" if col in ("skills", "agents") else "left")
        for r in found:
            table.add_row(_cell(r.full_name), str(r.skills), str(r.agents), r.status)
        console.print(table)
    for r in report.repos:
        if r.status == "failed":
            console.print(
                Text(f"failed: {sanitize_line(r.full_name)}: {sanitize_line(r.error or '')}")
            )
    if report.message:
        console.print(Text(sanitize_line(report.message)), style="yellow")
    if path is not None:
        console.print(Text(f"organization scan report: {path}"))


def _scan_org(req: OrgRequest, *, plain: bool, quiet: bool) -> None:
    progress = NullOrgProgress() if quiet else ConsoleOrgProgress(err)
    outcome = run_org_scan(req, store_dir=store.scans_dir(), progress=progress)
    if plain:
        sys.stdout.write(render_org_plain(outcome.report, outcome.saved_path))
    else:
        print_org_summary(out, outcome.report, outcome.saved_path)
    if outcome.exit_code:
        raise typer.Exit(outcome.exit_code)


@app.command()
def scan(
    target: Annotated[
        str | None,
        typer.Argument(
            help="GitHub URL, owner/repo, or local path; https://github.com/<owner> "
            "scans every repository of an organization or user."
        ),
    ] = None,
    ref: Annotated[str | None, typer.Option(help="Branch, tag or commit SHA.")] = None,
    path: Annotated[str | None, typer.Option(help="Scan only this subdirectory.")] = None,
    include: Annotated[
        list[str] | None, typer.Option(help="Glob that overrides --exclude rules.")
    ] = None,
    exclude: Annotated[list[str] | None, typer.Option(help="Glob to exclude.")] = None,
    no_tui: Annotated[
        bool, typer.Option("--no-tui", help="Do not open the TUI; save and print a summary.")
    ] = False,
    output: Annotated[
        str | None, typer.Option("--output", "-o", help="Also write the snapshot to FILE or -.")
    ] = None,
    no_save: Annotated[
        bool, typer.Option("--no-save", help="Do not write the snapshot to the store.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Write a new snapshot on cache hit; overwrite --output."),
    ] = False,
    host: Annotated[str, typer.Option(help="GitHub host for owner/repo targets.")] = "github.com",
    plain: Annotated[bool, PLAIN_OPTION] = False,
    with_body: Annotated[bool, WITH_BODY_OPTION] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Do not report progress on stderr.")
    ] = False,
    org: Annotated[
        str | None,
        typer.Option(
            "--org",
            help="Scan every repository of this GitHub organization or user.",
            rich_help_panel="Organization scan",
        ),
    ] = None,
    include_archived: Annotated[
        bool,
        typer.Option(
            "--include-archived",
            help="Also scan archived repositories.",
            rich_help_panel="Organization scan",
        ),
    ] = False,
    include_forks: Annotated[
        bool,
        typer.Option(
            "--include-forks", help="Also scan forks.", rich_help_panel="Organization scan"
        ),
    ] = False,
    match: Annotated[
        list[str] | None,
        typer.Option(
            "--match",
            help="Glob on the repository name, case-insensitive; repeatable.",
            rich_help_panel="Organization scan",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="Scan at most N repositories (by name).",
            rich_help_panel="Organization scan",
        ),
    ] = None,
    jobs: Annotated[
        int | None,
        typer.Option(
            "--jobs",
            "-j",
            help=f"Repositories scanned at once, 1-{MAX_JOBS} (default {DEFAULT_JOBS}).",
            rich_help_panel="Organization scan",
        ),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Wait for the API rate limit to reset instead of stopping.",
            rich_help_panel="Organization scan",
        ),
    ] = False,
) -> None:
    """Scan a repository or an organization, save snapshots and open the result in the TUI."""
    host = host.lower()
    owner = None
    if org is not None:
        if target is not None:
            raise UsageError("pass either a target or --org, not both")
        owner = org_target(org, host).owner
    elif target is None:
        raise UsageError("missing target: a GitHub URL, owner/repo, a local path or --org")
    else:
        parsed = parse_target(target, host)
        if isinstance(parsed, OrgTarget):
            owner, host = parsed.owner, parsed.host
    org_only = {
        "--include-archived": include_archived,
        "--include-forks": include_forks,
        "--match": bool(match),
        "--limit": limit is not None,
        "--wait": wait,
        "--jobs": jobs is not None,
    }
    if owner is not None:
        repo_only = {
            "--ref": ref is not None,
            "--path": path is not None,
            "--output": output is not None,
            "--no-save": no_save,
            "--with-body": with_body,
        }
        if bad := [k for k, used in repo_only.items() if used]:
            raise UsageError(f"{', '.join(bad)}: not available for an organization scan")
        jobs = DEFAULT_JOBS if jobs is None else jobs
        if not 1 <= jobs <= MAX_JOBS:
            raise UsageError(f"--jobs must be between 1 and {MAX_JOBS}")
        org_req = OrgRequest(
            owner=owner,
            host=host,
            include_archived=include_archived,
            include_forks=include_forks,
            match=match or [],
            limit=limit,
            include=include or [],
            exclude=exclude or [],
            force=force,
            wait=wait,
            jobs=jobs,
        )
        _scan_org(org_req, plain=plain, quiet=quiet)
        return
    if bad := [k for k, used in org_only.items() if used]:
        raise UsageError(f"{', '.join(bad)}: only for an organization scan (--org)")
    assert target is not None
    if plain and output == "-":
        raise UsageError("--plain and --output - both write to stdout; choose one")
    if output and output != "-" and Path(output).exists() and not force:
        raise UsageError(f"{output} exists; pass --force to overwrite")
    req = ScanRequest(
        target=target,
        ref=ref,
        path=path,
        include=include or [],
        exclude=exclude or [],
        host=host,
        force=force,
    )
    progress = NullProgress() if quiet else ConsoleProgress(err)
    outcome = run_scan(req, store_dir=store.scans_dir(), save=not no_save, progress=progress)
    for notice in outcome.notices:
        err.print(Text(f"note: {sanitize_line(notice)}"), style="yellow")

    if output == "-":
        sys.stdout.write(store.dumps(outcome.snapshot))
        print_summary(err, outcome.snapshot, outcome.saved_path, outcome.cache_hit)
        return
    if output:
        try:
            Path(output).write_text(store.dumps(outcome.snapshot), encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"cannot write {output}: {exc}") from None
    if plain:
        sys.stdout.write(render_plain(outcome.snapshot, outcome.saved_path, with_body=with_body))
        return
    if no_tui or not _interactive():
        print_summary(out, outcome.snapshot, outcome.saved_path, outcome.cache_hit)
        return
    _run_tui(outcome.snapshot, outcome.saved_path)


@app.command()
def repos(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    sort: Annotated[
        Literal["scanned", "skills", "name"],
        typer.Option(help="Sort order; pinned repositories come first."),
    ] = "scanned",
    pinned: Annotated[bool, PINNED_OPTION] = False,
) -> None:
    """List known repositories and their latest scans."""
    db = aggregate.Store.open(store.scans_dir())
    rows = aggregate.repos(db, sort, _favorites(db.warnings))
    _print_warnings(db.warnings)
    if pinned:
        rows = [r for r in rows if r.pinned]
    if as_json:
        out.print_json(json.dumps([dataclasses.asdict(r) for r in rows]))
        return
    if not rows:
        out.print(f"no snapshots in {store.scans_dir()}")
        return
    table = Table(box=None, header_style="bold cyan", pad_edge=False)
    for col in ("pin", "repo", "last scan", "commit", "skills", "agents", "scans"):
        table.add_column(col, justify="right" if col in ("skills", "agents", "scans") else "left")
    for r in rows:
        sha = (r.commit_sha or "")[:8] + (" dirty" if r.dirty else "")
        table.add_row(
            PIN_MARK if r.pinned else "",
            _cell(r.repo_key),
            r.last_scanned_at,
            sha or "—",
            str(r.skills),
            str(r.agents),
            str(r.scans),
        )
    out.print(table)
    out.print(f"{len(rows)} repositories, {sum(r.skills for r in rows)} skills")


@app.command()
def skills(
    name: Annotated[str | None, typer.Option(help="Substring of the skill name.")] = None,
    repo: Annotated[str | None, typer.Option(help="Exact repo_key.")] = None,
    type_: Annotated[str | None, typer.Option("--type", help="Skill type.")] = None,
    kind: Annotated[Literal["skill", "agent", "all"], typer.Option(help="Entry kind.")] = "skill",
    category: Annotated[
        str,
        typer.Option(
            help="relevant, auxiliary, all, or one category: " + ", ".join(categories.SELECTORS[3:])
        ),
    ] = "relevant",
    group_by: Annotated[Literal["none", "name", "hash"], typer.Option(help="Group rows.")] = "none",
    all_scans: Annotated[
        bool, typer.Option("--all-scans", help="Use every snapshot, not only the latest.")
    ] = False,
    pinned: Annotated[bool, PINNED_OPTION] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List skills across the latest snapshot of each repository, pinned first."""
    if category not in categories.SELECTORS:
        choices = ", ".join(categories.SELECTORS)
        raise UsageError(f"unknown category {category!r}; use one of {choices}")
    db = aggregate.Store.open(store.scans_dir())
    rows = aggregate.skill_rows(
        db,
        name=name,
        repo=repo,
        type_=type_,
        kind=None if kind == "all" else kind,
        category=category,
        all_scans=all_scans,
        favorites=_favorites(db.warnings),
    )
    _print_warnings(db.warnings)
    if pinned:
        rows = [r for r in rows if r.pinned]
    if group_by != "none":
        groups = aggregate.group(rows, group_by)
        if as_json:
            out.print_json(json.dumps([dataclasses.asdict(g) for g in groups]))
            return
        table = Table(box=None, header_style="bold cyan", pad_edge=False)
        key_label = "name" if group_by == "name" else "sha256"
        names_col = "names" if group_by == "hash" else ""
        for col in ("pin", key_label, "repos", "variants", "types", names_col):
            if col:
                table.add_column(col)
        for g in groups:
            key = g.key if group_by == "name" else g.key[:12]
            cells: list[Text | str] = [
                PIN_MARK if g.pinned else "",
                _cell(key),
                str(len(g.repos)),
                str(g.variants),
                ", ".join(g.types),
            ]
            if group_by == "hash":
                cells.append(_cell(", ".join(g.names)))
            table.add_row(*cells)
        out.print(table)
        out.print(f"{len(groups)} groups, {len(rows)} skills")
        return
    if as_json:
        out.print_json(
            json.dumps(
                [
                    {
                        "repo_key": r.repo_key,
                        "commit_sha": r.commit_sha,
                        "scanned_at": r.scanned_at,
                        "pinned": r.pinned,
                        "skill": r.skill.model_dump(mode="json", exclude={"body"}),
                    }
                    for r in rows
                ]
            )
        )
        return
    table = Table(box=None, header_style="bold cyan", pad_edge=False)
    for col in ("pin", "repo", "name", "category", "type", "compliance", "path"):
        table.add_column(col)
    for r in rows:
        s = r.skill
        table.add_row(
            PIN_MARK if r.pinned else "",
            _cell(r.repo_key),
            _cell(s.name),
            s.category or "—",
            s.type,
            s.compliance.status,
            _cell(s.path or s.source_pointer),
        )
    out.print(table)
    out.print(f"{len(rows)} skills")


def _favorites(warnings: list[str]) -> favorites.Favorites:
    return favorites.load_or_empty(favorites.default_path(store.scans_dir()), warnings)


def _set_pins(repo_key: str, skill_ids: list[str], pinned: bool) -> None:
    db = aggregate.Store.open(store.scans_dir())
    _print_warnings(db.warnings)
    key = repo_key.lower()
    items = next(
        (g for g in db.by_repo().values() if any(i.snapshot.source.repo_key == key for i in g)),
        None,
    )
    if items is None:
        raise UsageError(f"no snapshot for {repo_key!r}; see `skill-atlas repos`")
    repo = favorites.RepoRef.of_repo(items)
    known = {s.id for i in items for s in i.snapshot.skills}
    if unknown := [s for s in skill_ids if s not in known]:
        raise UsageError(
            f"no skill {unknown[0]!r} in {repo_key!r}; see `skill-atlas skills --json`"
        )
    path = favorites.default_path(store.scans_dir())
    if skill_ids:
        refs = [favorites.SkillRef(repo, s) for s in skill_ids]
        favorites.update(path, lambda f: f.with_skills(refs, pinned))
    else:
        favorites.update(path, lambda f: f.with_repo(repo, pinned))
    verb = "pinned" if pinned else "unpinned"
    for what in skill_ids or [repo.repo_key]:
        out.print(Text(f"{verb} {sanitize_line(what)}"))


REPO_KEY_ARG = typer.Argument(help="repo_key, see `skill-atlas repos`.")
SKILL_IDS_ARG = typer.Argument(
    help="Skill ids (`<type>:<path>`) in that repository; without them, the repository."
)


@app.command()
def pin(
    repo_key: Annotated[str, REPO_KEY_ARG],
    skill_ids: Annotated[list[str] | None, SKILL_IDS_ARG] = None,
) -> None:
    """Pin a repository or its skills: they come first in every list."""
    _set_pins(repo_key, skill_ids or [], pinned=True)


@app.command()
def unpin(
    repo_key: Annotated[str, REPO_KEY_ARG],
    skill_ids: Annotated[list[str] | None, SKILL_IDS_ARG] = None,
) -> None:
    """Remove the pin from a repository or its skills."""
    _set_pins(repo_key, skill_ids or [], pinned=False)


@app.command()
def show(
    ref: Annotated[str, typer.Argument(help="repo_key[@sha] or a snapshot JSON file.")],
    plain: Annotated[bool, PLAIN_OPTION] = False,
    with_body: Annotated[bool, WITH_BODY_OPTION] = False,
) -> None:
    """Open a saved snapshot in the TUI without network access."""
    candidate = Path(ref).expanduser()
    if candidate.is_file():
        snapshot, path = store.load(candidate), candidate
    else:
        repo_key, _, sha = ref.partition("@")
        db = aggregate.Store.open(store.scans_dir())
        _print_warnings(db.warnings)
        found = db.find(repo_key, sha or None)
        if found is None:
            raise UsageError(f"no snapshot for {ref!r}; see `skill-atlas repos`")
        snapshot, path = found.snapshot, found.path
    if plain:
        sys.stdout.write(render_plain(snapshot, path, with_body=with_body))
        return
    if not _interactive():
        print_summary(out, snapshot, path, cache_hit=False)
        return
    _run_tui(snapshot, path)


def _print_warnings(warnings: list[str]) -> None:
    for w in warnings:
        err.print(Text(f"warning: {sanitize_line(w)}"), style="yellow")


def main() -> None:
    # Standalone mode lets Typer report usage errors with exit code 2.
    # Our own errors carry their exit codes (SPEC section 3.3).
    try:
        app()
    except AtlasError as exc:
        err.print(Text(f"error: {sanitize_line(str(exc))}"), style="red")
        sys.exit(exc.exit_code)

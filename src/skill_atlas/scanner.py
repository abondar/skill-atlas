"""Skill detection over a tree source (SPEC section 5)."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from skill_atlas import frontmatter, paths, plugins
from skill_atlas.model import (
    Compliance,
    Kind,
    Plugin,
    Resource,
    ScanOptions,
    Skill,
    SkillType,
    Violation,
)
from skill_atlas.progress import NullProgress, Progress
from skill_atlas.sources.base import MAX_FILE_BYTES, TreeEntry, TreeSource

MAX_CANDIDATES = 5000
MAX_RESOURCES = 1000
MAX_SYMLINKS = 200
MAX_SYMLINK_HOPS = 8
PREFETCH_WORKERS = 8

KNOWN_ROOTS = (
    (".agents", "skills"),
    (".claude", "skills"),
    (".cursor", "skills"),
    (".codex", "skills"),
    (".github", "skills"),
    (".opencode", "skills"),
)
DETECTOR_PRIORITY = {"D1": 0, "D3": 1, "D2": 2, "D5": 3, "D6": 4, "D7": 5}
_SYMLINK_HINTS = frozenset({"skills", "commands", "agents", "prompts"})
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True)
class LogicalFile:
    path: str
    real: str
    entry: TreeEntry
    virtual: bool


@dataclass
class Candidate:
    detector: str
    type: SkillType
    kind: Kind
    file: LogicalFile | None
    path_name: str | None = None
    name_override: str | None = None
    name_override_source: str | None = None
    description_override: str | None = None
    plugin_id: str | None = None
    inline_content: str | None = None
    manifest_path: str | None = None
    aliases: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        if self.file is None:
            return f"{self.type}:{self.manifest_path}#{self.name_override}"
        return f"{self.type}:{self.file.path}"


@dataclass
class ScanResult:
    skills: list[Skill]
    plugins: list[Plugin]
    warnings: list[str]
    excluded_candidates: int


def _after(segs: list[str], pair: tuple[str, str]) -> list[str] | None:
    """Segments after the first occurrence of `pair`, if any remain."""
    for i in range(len(segs) - 2):
        if (segs[i], segs[i + 1]) == pair:
            return segs[i + 2 :]
    return None


def path_detector(path: str) -> tuple[str, str | None] | None:
    """Detectors that depend on the path alone. Return (detector, path-derived name)."""
    segs = paths.segments(path)
    if not segs:
        return None
    base = segs[-1]
    if base == "SKILL.md":
        return "D1", segs[-2] if len(segs) > 1 else None
    if not base.endswith(".md"):
        return None
    if (rest := _after(segs, (".claude", "commands"))) is not None:
        return "D2", ":".join(rest)[: -len(".md")]
    if len(segs) >= 3 and segs[-3:-1] == [".github", "prompts"] and base.endswith(".prompt.md"):
        return "D5", base[: -len(".prompt.md")]
    if (rest := _after(segs, (".claude", "agents"))) is not None:
        return "D6", ":".join(rest)[: -len(".md")]
    if len(segs) >= 3 and segs[-3:-1] == [".github", "agents"] and base.endswith(".agent.md"):
        return "D7", base[: -len(".agent.md")]
    return None


_TYPES: dict[str, tuple[SkillType, Kind]] = {
    "D1": ("agent-skill", "skill"),
    "D2": ("claude-command", "skill"),
    "D3": ("plugin-command", "skill"),
    "D4": ("plugin-command-inline", "skill"),
    "D5": ("copilot-prompt", "skill"),
    "D6": ("claude-agent", "agent"),
    "D7": ("copilot-agent", "agent"),
}


class Scanner:
    def __init__(
        self, source: TreeSource, options: ScanOptions, progress: Progress | None = None
    ) -> None:
        self.source = source
        self.options = options
        self.progress = progress or NullProgress()
        self.warnings: list[str] = []
        self._dir_cache: dict[str, list[TreeEntry]] = {}
        self._cache: dict[str, bytes | Exception] = {}

    # --- tree assembly -------------------------------------------------------------

    def _listing(self, prefix: str) -> list[TreeEntry]:
        if prefix not in self._dir_cache:
            self._dir_cache[prefix] = self.source.entries(prefix)
        return self._dir_cache[prefix]

    def _lookup(self, path: str) -> TreeEntry | None:
        for e in self._listing(paths.parent(path)):
            if e.path == path:
                return e
        return None

    def _resolve_link(self, link: str) -> tuple[str, str, TreeEntry | None] | None:
        """Follow a symlink inside the repository. Return (kind, real path, entry)."""
        current = link
        for _ in range(MAX_SYMLINK_HOPS):
            try:
                raw = self.source.link_target(current).strip()
            except (OSError, KeyError) as exc:
                self.warnings.append(f"{link}: cannot read symlink: {exc}")
                return None
            resolved = paths.normalize_inside(paths.parent(current), raw)
            if resolved is None:
                self.warnings.append(f"{link}: symlink points outside the repository; skipped")
                return None
            entry = self._lookup(resolved) if resolved else None
            if entry is not None and entry.kind == "file":
                return "file", resolved, entry
            if entry is not None and entry.kind == "symlink":
                current = resolved
                continue
            if self._listing(resolved):
                return "dir", resolved, None
            self.warnings.append(f"{link}: dangling symlink to {raw!r}")
            return None
        self.warnings.append(f"{link}: symlink chain is too long; skipped")
        return None

    @staticmethod
    def _symlink_relevant(path: str) -> bool:
        segs = paths.segments(path)
        return segs[-1].endswith(".md") or any(s in _SYMLINK_HINTS for s in segs)

    def _collect_files(self, prefix: str) -> dict[str, LogicalFile]:
        files: dict[str, LogicalFile] = {}
        links: list[str] = []
        base = self._listing(prefix)
        self.warnings.extend(self.source.warnings)
        for e in base:
            if e.kind == "file":
                files[e.path] = LogicalFile(e.path, e.path, e, virtual=False)
            elif e.kind == "symlink":
                links.append(e.path)
            else:
                self.warnings.append(f"{e.path}: git submodule; not scanned")
        relevant = [link for link in links if self._symlink_relevant(link)]
        if len(relevant) > MAX_SYMLINKS:
            self.warnings.append(
                f"{len(relevant)} relevant symlinks; only the first {MAX_SYMLINKS} were resolved"
            )
            relevant = relevant[:MAX_SYMLINKS]
        for link in relevant:
            resolved = self._resolve_link(link)
            if resolved is None:
                continue
            kind, real, entry = resolved
            if kind == "file":
                assert entry is not None
                files.setdefault(link, LogicalFile(link, real, entry, virtual=True))
                continue
            for e in self._listing(real):
                if e.kind != "file":
                    continue
                logical = link + e.path[len(real) :] if real else paths.join(link, e.path)
                files.setdefault(logical, LogicalFile(logical, e.path, e, virtual=True))
        return files

    def _apply_exclusions(
        self, files: dict[str, LogicalFile]
    ) -> tuple[dict[str, LogicalFile], int]:
        kept: dict[str, LogicalFile] = {}
        excluded_candidates = 0
        opts = self.options
        for path, f in files.items():
            if paths.in_default_excluded_dir(path):
                continue
            excluded = (
                not opts.include_fixtures and paths.is_fixture_path(path)
            ) or paths.matches_glob(path, opts.exclude)
            if excluded and paths.matches_glob(path, opts.include):
                excluded = False
            if excluded:
                if path_detector(path) is not None:
                    excluded_candidates += 1
                continue
            kept[path] = f
        return kept, excluded_candidates

    # --- reading ---------------------------------------------------------------------

    def _read_text(self, f: LogicalFile) -> str | None:
        if f.entry.size is not None and f.entry.size > MAX_FILE_BYTES:
            return None
        try:
            data = self._read(f.real)
        except (OSError, KeyError):
            return None
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

    # --- main ------------------------------------------------------------------------

    def run(self) -> ScanResult:
        prefix = (self.options.path or "").strip("/")
        self.progress.stage("listing files")
        files = self._collect_files(prefix)
        files, excluded = self._apply_exclusions(files)
        self.progress.stage(f"detecting skills in {len(files)} files")

        real_files = {p: f for p, f in files.items() if not f.virtual}
        comps = plugins.discover(real_files, lambda p: self._read_text(real_files[p]))
        self.warnings.extend(comps.warnings)

        candidates = self._detect(files, comps)
        candidates = self._dedupe(candidates)
        candidates.sort(key=lambda c: c.id)
        if len(candidates) > MAX_CANDIDATES:
            self.warnings.append(
                f"{len(candidates)} candidates found; only the first {MAX_CANDIDATES} were kept"
            )
            candidates = candidates[:MAX_CANDIDATES]

        skill_dirs = {
            paths.parent(p)
            for c in candidates
            if c.detector == "D1" and c.file is not None
            for p in [c.file.path, *c.aliases]
        }
        self._prefetch(candidates, files, skill_dirs)
        skills = [self._record(c, files, comps, skill_dirs) for c in candidates]
        return ScanResult(skills, comps.plugins, self.warnings, excluded)

    def _prefetch(
        self, candidates: list[Candidate], files: dict[str, LogicalFile], skill_dirs: set[str]
    ) -> None:
        """Read candidate files concurrently; remote sources pay one round trip per file."""
        wanted: list[LogicalFile] = [c.file for c in candidates if c.file is not None]
        wanted += [f for d in skill_dirs if (f := files.get(paths.join(d, "agents/openai.yaml")))]
        todo = sorted(
            {f.real for f in wanted if f.entry.size is None or f.entry.size <= MAX_FILE_BYTES}
            - self._cache.keys()
        )

        def fetch(real: str) -> tuple[str, bytes | Exception]:
            try:
                return real, self.source.read(real)
            except (OSError, KeyError) as exc:
                return real, exc

        self.progress.stage(f"reading {len(todo)} candidate files")
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            for i, (real, result) in enumerate(pool.map(fetch, todo), 1):
                self._cache[real] = result
                self.progress.detail(f"{i}/{len(todo)}")
        self.progress.stage(f"building records for {len(candidates)} entries")

    def _read(self, real: str) -> bytes:
        cached = self._cache.pop(real, None)
        if isinstance(cached, bytes):
            return cached
        if isinstance(cached, Exception):
            raise cached
        return self.source.read(real)

    def _detect(
        self, files: dict[str, LogicalFile], comps: plugins.PluginComponents
    ) -> list[Candidate]:
        found: list[Candidate] = []
        for path in sorted(files):
            f = files[path]
            base = posixpath.basename(path)
            if base.lower() == "skill.md" and base != "SKILL.md":
                self.warnings.append(f"{path}: ignored; the Agent Skills spec requires 'SKILL.md'")
            options: list[Candidate] = []
            if (hit := path_detector(path)) is not None:
                detector, name = hit
                typ, kind = _TYPES[detector]
                options.append(Candidate(detector, typ, kind, f, path_name=name))
            if (cmd := comps.commands.get(path)) is not None:
                typ, kind = _TYPES["D3"]
                c = Candidate("D3", typ, kind, f, path_name=cmd.name, plugin_id=cmd.plugin_id)
                if cmd.name_source == "manifest":
                    c.name_override, c.name_override_source = cmd.name, "manifest"
                c.description_override = cmd.description
                options.append(c)
            if (agent := comps.agents.get(path)) is not None:
                typ, kind = _TYPES["D6"]
                options.append(
                    Candidate("D6", typ, kind, f, path_name=agent.name, plugin_id=agent.plugin_id)
                )
            if options:
                found.append(min(options, key=lambda c: DETECTOR_PRIORITY[c.detector]))
        for inline in comps.inline_commands:
            typ, kind = _TYPES["D4"]
            found.append(
                Candidate(
                    "D4",
                    typ,
                    kind,
                    None,
                    name_override=inline.name,
                    name_override_source="manifest",
                    description_override=inline.description,
                    plugin_id=inline.plugin_id,
                    inline_content=inline.content,
                    manifest_path=inline.manifest_path,
                )
            )
        return found

    @staticmethod
    def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
        groups: dict[str, list[Candidate]] = defaultdict(list)
        out: list[Candidate] = []
        for c in candidates:
            if c.file is None:
                out.append(c)
            else:
                groups[c.file.real].append(c)
        for group in groups.values():
            group.sort(
                key=lambda c: (
                    c.file.virtual if c.file else False,
                    DETECTOR_PRIORITY[c.detector],
                    c.file.path if c.file else "",
                )
            )
            primary = group[0]
            primary.aliases = sorted(c.file.path for c in group[1:] if c.file is not None)
            out.append(primary)
        return out

    def _scope(self, skill_dir: str, comps: plugins.PluginComponents) -> tuple[str, str | None]:
        current = skill_dir
        while True:
            if current in comps.skill_dirs:
                return "plugin", comps.skill_dirs[current]
            if current == skill_dir and current in comps.roots:
                return "plugin", comps.roots[current]
            segs = paths.segments(current)
            if len(segs) >= 2 and (segs[-2], segs[-1]) in KNOWN_ROOTS:
                return "/".join(segs[-2:]), None
            if not current:
                return "unscoped", None
            current = paths.parent(current)

    def _resources(
        self, skill_dir: str, own_path: str, files: dict[str, LogicalFile], skill_dirs: set[str]
    ) -> tuple[list[Resource], list[str]]:
        nested = [d for d in skill_dirs if d != skill_dir and paths.is_under(d, skill_dir)]
        out: list[Resource] = []
        warnings: list[str] = []
        for path in sorted(files):
            if path == own_path or not paths.is_under(path, skill_dir):
                continue
            if any(paths.is_under(path, d) for d in nested):
                continue
            if len(out) >= MAX_RESOURCES:
                warnings.append(f"more than {MAX_RESOURCES} resources; the rest were skipped")
                break
            f = files[path]
            out.append(
                Resource(path=path, bytes=f.entry.size, git_blob_sha=self.source.blob_sha(f.entry))
            )
        return out, warnings

    def _record(
        self,
        c: Candidate,
        files: dict[str, LogicalFile],
        comps: plugins.PluginComponents,
        skill_dirs: set[str],
    ) -> Skill:
        warnings: list[str] = []
        path = c.file.path if c.file else None
        skill_dir = paths.parent(path) if (path is not None and c.detector == "D1") else None
        base: dict[str, Any] = {
            "id": c.id,
            "kind": c.kind,
            "type": c.type,
            "detector": c.detector,
            "path": path,
            "dir": skill_dir,
            "plugin_id": c.plugin_id,
            "aliases": c.aliases,
        }
        if c.file is None:
            base["source_pointer"] = f"{c.manifest_path}#/commands/{c.name_override}"
        if c.file is not None and c.file.virtual:
            warnings.append(f"read through symlink to {c.file.real}")
        if skill_dir is not None:
            scope, plugin_id = self._scope(skill_dir, comps)
            base["scope"] = scope
            base["plugin_id"] = plugin_id or c.plugin_id
            resources, res_warnings = self._resources(skill_dir, path or "", files, skill_dirs)
            base["resources"] = resources
            warnings += res_warnings
        elif c.plugin_id is not None:
            base["scope"] = "plugin"
        elif c.detector in ("D2", "D6"):
            base["scope"] = ".claude"
        elif c.detector in ("D5", "D7"):
            base["scope"] = ".github"

        data, broken = self._load_bytes(c)
        if c.file is not None:
            base["git_blob_sha"] = c.file.entry.blob_sha or (
                self.source.blob_sha(c.file.entry) if data is not None else None
            )
        if broken is not None:
            base["name"], base["name_source"] = self._fallback_name(c)
            return Skill(
                **base,
                compliance=Compliance(status="broken", violations=[broken]),
                warnings=warnings,
            )
        assert data is not None
        text = data.decode("utf-8-sig")
        parsed = frontmatter.parse(text)
        fm = parsed.data
        if parsed.error:
            warnings.append(f"frontmatter: {parsed.error}")

        name, name_source = self._name(c, fm)
        description, description_source = self._description(c, fm, parsed.body)
        body = parsed.body
        extras: dict[str, Any] = {}
        if skill_dir is not None:
            openai = files.get(paths.join(skill_dir, "agents/openai.yaml"))
            if openai is not None:
                extras["openai_yaml"] = self._load_openai_yaml(openai)

        if c.detector == "D1":
            violations = _agent_skill_violations(parsed, skill_dir or "")
            compliance = Compliance(
                status="loadable" if violations else "compliant", violations=violations
            )
        else:
            compliance = Compliance(status="loadable")

        return Skill(
            **base,
            name=name,
            name_source=name_source,
            description=description,
            description_source=description_source,
            frontmatter=fm,
            frontmatter_raw=parsed.raw,
            frontmatter_error=parsed.error,
            body=body,
            body_bytes=len(body.encode("utf-8")),
            body_lines=body.count("\n") + 1 if body else 0,
            content_sha256=hashlib.sha256(data).hexdigest(),
            extras=extras,
            compliance=compliance,
            warnings=warnings,
        )

    def _load_bytes(self, c: Candidate) -> tuple[bytes | None, Violation | None]:
        if c.inline_content is not None:
            return c.inline_content.encode("utf-8"), None
        assert c.file is not None
        size = c.file.entry.size
        if size is not None and size > MAX_FILE_BYTES:
            return None, Violation(code="too-large", message=f"file exceeds {MAX_FILE_BYTES} bytes")
        try:
            data = self._read(c.file.real)
        except (OSError, KeyError) as exc:
            return None, Violation(code="unreadable", message=str(exc))
        if len(data) > MAX_FILE_BYTES:
            return None, Violation(code="too-large", message=f"file exceeds {MAX_FILE_BYTES} bytes")
        if data.startswith(_LFS_HEADER):
            return None, Violation(code="lfs-pointer", message="file is a Git LFS pointer")
        if b"\0" in data:
            return None, Violation(code="binary", message="file contains NUL bytes")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return None, Violation(code="encoding", message=f"invalid UTF-8: {exc.reason}")
        return data, None

    @staticmethod
    def _fallback_name(c: Candidate) -> tuple[str | None, str | None]:
        if c.name_override is not None:
            return c.name_override, c.name_override_source
        return c.path_name, "path" if c.path_name is not None else None

    def _name(self, c: Candidate, fm: dict[str, Any] | None) -> tuple[str | None, str | None]:
        if c.name_override is not None:
            return c.name_override, c.name_override_source
        # Claude Code command files ignore `name`; the path decides the command name.
        if c.detector not in ("D2", "D3") and fm and isinstance(fm.get("name"), str):
            value = fm["name"].strip()
            if value:
                return value, "frontmatter"
        return self._fallback_name(c)

    @staticmethod
    def _description(
        c: Candidate, fm: dict[str, Any] | None, body: str
    ) -> tuple[str | None, str | None]:
        if fm and isinstance(fm.get("description"), str) and fm["description"].strip():
            return fm["description"].strip(), "frontmatter"
        if c.description_override:
            return c.description_override, "manifest"
        for line in body.split("\n"):
            if line.strip():
                return line.strip(), "body"
        return None, None

    def _load_openai_yaml(self, f: LogicalFile) -> Any:
        text = self._read_text(f)
        if text is None:
            return {"error": "cannot read file"}
        try:
            return frontmatter.safe_load(text)
        except frontmatter.YamlError as exc:
            return {"error": str(exc)}


def _agent_skill_violations(parsed: frontmatter.Parsed, skill_dir: str) -> list[Violation]:
    v: list[Violation] = []

    def add(code: str, message: str) -> None:
        v.append(Violation(code=code, message=message))

    if parsed.raw is None:
        add("frontmatter-missing", parsed.error or "SKILL.md has no YAML frontmatter")
        return v
    if parsed.data is None:
        add("frontmatter-invalid", parsed.error or "frontmatter cannot be parsed")
        return v
    fm = parsed.data
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        add("name-missing", "required field 'name' is missing or empty")
    else:
        if len(name) > 64:
            add("name-too-long", f"'name' has {len(name)} characters; max is 64")
        if not _NAME_RE.match(name):
            add("name-invalid", "'name' must be lowercase a-z, 0-9 and single inner hyphens")
        dir_name = posixpath.basename(skill_dir)
        if name != dir_name:
            add("name-dir-mismatch", f"'name' {name!r} does not match directory {dir_name!r}")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        add("description-missing", "required field 'description' is missing or empty")
    elif len(desc) > 1024:
        add("description-too-long", f"'description' has {len(desc)} characters; max is 1024")
    if "compatibility" in fm:
        comp = fm["compatibility"]
        if not isinstance(comp, str) or not 1 <= len(comp) <= 500:
            add("compatibility-invalid", "'compatibility' must be a string of 1-500 characters")
    if "license" in fm and not isinstance(fm["license"], str):
        add("license-invalid", "'license' must be a string")
    if "metadata" in fm:
        meta = fm["metadata"]
        if not isinstance(meta, dict) or not all(isinstance(x, str) for x in meta.values()):
            add("metadata-invalid", "'metadata' must map strings to strings")
    if "allowed-tools" in fm and not isinstance(fm["allowed-tools"], str):
        add("allowed-tools-invalid", "'allowed-tools' must be a space-separated string")
    return v

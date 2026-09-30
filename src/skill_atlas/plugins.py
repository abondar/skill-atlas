"""Claude Code plugin and marketplace manifest parsing (SPEC section 5.2, "Плагины")."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from skill_atlas import paths
from skill_atlas.model import Plugin

PLUGIN_MANIFEST = (".claude-plugin", "plugin.json")
MARKETPLACE_MANIFEST = (".claude-plugin", "marketplace.json")


@dataclass(frozen=True)
class FileComponent:
    plugin_id: str
    path: str
    name: str
    name_source: str
    description: str | None = None


@dataclass(frozen=True)
class InlineCommand:
    plugin_id: str
    manifest_path: str
    name: str
    content: str
    description: str | None = None


@dataclass
class PluginComponents:
    plugins: list[Plugin] = field(default_factory=list)
    # Directories whose <name>/SKILL.md children belong to a plugin.
    skill_dirs: dict[str, str] = field(default_factory=dict)
    # Plugin roots: a SKILL.md directly in a root is a single-skill plugin.
    roots: dict[str, str] = field(default_factory=dict)
    commands: dict[str, FileComponent] = field(default_factory=dict)
    agents: dict[str, FileComponent] = field(default_factory=dict)
    inline_commands: list[InlineCommand] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Spec:
    root: str
    manifest_path: str | None = None
    manifest: dict[str, Any] | None = None
    marketplace_path: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    # Entries whose source is the marketplace root and list `skills` load only those.
    only_listed_skills: bool = False
    id: str = ""


@dataclass(frozen=True)
class _Entry:
    root: str
    marketplace_path: str
    data: dict[str, Any]
    at_marketplace_root: bool


def _manifest_root(path: str, manifest: tuple[str, str]) -> str | None:
    segs = paths.segments(path)
    if tuple(segs[-2:]) != manifest:
        return None
    return "/".join(segs[:-2])


def discover(file_paths: Iterable[str], read_text: Callable[[str], str | None]) -> PluginComponents:
    files = sorted(set(file_paths))
    file_set = set(files)
    out = PluginComponents()
    specs: dict[str, _Spec] = {}

    def load_json(path: str) -> Any:
        text = read_text(path)
        if text is None:
            out.warnings.append(f"{path}: cannot read manifest")
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            out.warnings.append(f"{path}: invalid JSON: {exc}")
            return None

    for path in files:
        root = _manifest_root(path, PLUGIN_MANIFEST)
        if root is None:
            continue
        data = load_json(path)
        spec = specs.setdefault(root, _Spec(root))
        spec.manifest_path = path
        if isinstance(data, dict):
            spec.manifest = data
        elif data is not None:
            out.warnings.append(f"{path}: manifest is not a JSON object")

    market_entries: list[_Entry] = []
    for path in files:
        market_root = _manifest_root(path, MARKETPLACE_MANIFEST)
        if market_root is None:
            continue
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        base = ""
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("pluginRoot"), str):
            base = metadata["pluginRoot"]
        for entry in data.get("plugins") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            source = entry.get("source")
            if isinstance(source, str):
                rel = source
                if base and not rel.startswith(("./", "../")):
                    rel = posixpath.join(base, rel)
                root = paths.normalize_inside(market_root, rel)
                if root is None:
                    out.warnings.append(f"{path}: plugin {name!r} source escapes the repository")
                    continue
                market_entries.append(_Entry(root, path, entry, root == market_root))
            else:
                out.plugins.append(
                    Plugin(
                        id=f"{path}#{name}",
                        name=_str(name),
                        version=_str(entry.get("version")),
                        description=_str(entry.get("description")),
                        marketplace_path=path,
                        remote_source=source,
                    )
                )

    # A root with plugin.json is one plugin; marketplace entries layer onto it.
    # Without plugin.json, every marketplace entry is its own plugin.
    resolved: list[_Spec] = []
    by_root: dict[str, list[_Entry]] = {}
    for e in market_entries:
        by_root.setdefault(e.root, []).append(e)
    for root, spec in specs.items():
        for e in by_root.pop(root, []):
            spec.entries.append(e.data)
            spec.marketplace_path = e.marketplace_path
        spec.id = root or "."
        resolved.append(spec)
    for root, entries in by_root.items():
        for e in entries:
            name = _str(e.data.get("name"))
            plugin_id = root or "."
            if len(entries) > 1:
                plugin_id = f"{plugin_id}#{name}"
            resolved.append(
                _Spec(
                    root,
                    marketplace_path=e.marketplace_path,
                    entries=[e.data],
                    only_listed_skills=e.at_marketplace_root and "skills" in e.data,
                    id=plugin_id,
                )
            )
    for spec in sorted(resolved, key=lambda x: x.id):
        _resolve(spec, file_set, files, out)
    out.plugins.sort(key=lambda p: p.id)
    return out


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _resolve(spec: _Spec, file_set: set[str], files: list[str], out: PluginComponents) -> None:
    root = spec.root
    plugin_id = spec.id
    exists = any(paths.is_under(f, root) for f in files)
    if not exists:
        out.warnings.append(f"plugin source {plugin_id!r} not found in repository")
    layers: list[tuple[str, dict[str, Any]]] = []
    if spec.manifest is not None:
        layers.append((spec.manifest_path or "", spec.manifest))
    layers += [(spec.marketplace_path or "", e) for e in spec.entries]
    primary = layers[0][1] if layers else {}
    out.plugins.append(
        Plugin(
            id=plugin_id,
            root=root,
            manifest_path=spec.manifest_path,
            name=_str(primary.get("name")) or (posixpath.basename(root) or None),
            version=_str(primary.get("version")),
            description=_str(primary.get("description")),
            marketplace_path=spec.marketplace_path,
        )
    )
    if not exists:
        return
    out.roots.setdefault(root, plugin_id)

    def resolve_path(value: Any, where: str, *, allow_dot: bool = False) -> str | None:
        if not isinstance(value, str):
            out.warnings.append(f"{where}: component path must be a string")
            return None
        if not (value.startswith("./") or (allow_dot and value == ".")):
            out.warnings.append(f"{where}: component path {value!r} must start with './'")
            return None
        resolved = paths.normalize_inside(root, value)
        if resolved is None or not paths.is_under(resolved, root):
            out.warnings.append(f"{where}: component path {value!r} escapes the plugin root")
            return None
        return resolved

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else [value]

    # Skills: listed directories add to the default `skills/`.
    if not spec.only_listed_skills:
        out.skill_dirs.setdefault(paths.join(root, "skills"), plugin_id)
    for where, layer in layers:
        if "skills" in layer:
            for item in as_list(layer["skills"]):
                resolved = resolve_path(item, where, allow_dot=True)
                if resolved is not None:
                    out.skill_dirs.setdefault(resolved, plugin_id)

    # Commands and agents: the first layer that sets the key replaces the default folder.
    def component_layers(key: str) -> list[tuple[str, Any]]:
        chosen = [(w, layer[key]) for w, layer in layers if key in layer]
        if not layers or key not in layers[0][1]:
            chosen.insert(0, ("default", f"./{key}/"))
        return chosen

    for where, value in component_layers("commands"):
        if isinstance(value, dict):
            for cmd_name, cfg in value.items():
                _command_map_entry(spec, plugin_id, where, str(cmd_name), cfg, resolve_path, out)
            continue
        for item in as_list(value):
            resolved = resolve_path(item, where)
            if resolved is not None:
                _add_markdown(
                    out.commands,
                    plugin_id,
                    resolved,
                    file_set,
                    files,
                    where,
                    out,
                    required=where != "default",
                )

    for where, value in component_layers("agents"):
        for item in as_list(value):
            resolved = resolve_path(item, where)
            if resolved is None:
                continue
            if where != "default" and resolved not in file_set:
                if any(paths.is_under(f, resolved) for f in files):
                    out.warnings.append(f"{where}: agents entries must be .md files: {item!r}")
                else:
                    out.warnings.append(f"{where}: agent path not found: {item!r}")
                continue
            _add_markdown(
                out.agents,
                plugin_id,
                resolved,
                file_set,
                files,
                where,
                out,
                required=where != "default",
            )


def _command_map_entry(
    spec: _Spec,
    plugin_id: str,
    where: str,
    name: str,
    cfg: Any,
    resolve_path: Callable[..., str | None],
    out: PluginComponents,
) -> None:
    if not isinstance(cfg, dict) or ("source" in cfg) == ("content" in cfg):
        out.warnings.append(f"{where}: command {name!r} must set exactly one of source/content")
        return
    description = _str(cfg.get("description"))
    if "content" in cfg:
        if not isinstance(cfg["content"], str):
            out.warnings.append(f"{where}: command {name!r} content must be a string")
            return
        out.inline_commands.append(
            InlineCommand(plugin_id, where, name, cfg["content"], description)
        )
        return
    resolved = resolve_path(cfg["source"], where)
    if resolved is None:
        return
    out.commands[resolved] = FileComponent(plugin_id, resolved, name, "manifest", description)


def _add_markdown(
    target: dict[str, FileComponent],
    plugin_id: str,
    resolved: str,
    file_set: set[str],
    files: list[str],
    where: str,
    out: PluginComponents,
    *,
    required: bool,
) -> None:
    if resolved in file_set:
        if resolved.endswith(".md"):
            stem = posixpath.basename(resolved)[: -len(".md")]
            target.setdefault(resolved, FileComponent(plugin_id, resolved, stem, "path"))
        return
    found = False
    for f in files:
        if f.endswith(".md") and paths.is_under(f, resolved) and f != resolved:
            found = True
            rel = f[len(resolved) + 1 :] if resolved else f
            name = rel[: -len(".md")].replace("/", ":")
            target.setdefault(f, FileComponent(plugin_id, f, name, "path"))
    if not found and required:
        out.warnings.append(f"{where}: component path not found: {resolved!r}")

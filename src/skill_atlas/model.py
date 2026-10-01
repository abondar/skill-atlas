"""Snapshot data model (SPEC section 6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from skill_atlas.categories import Category

Kind = Literal["skill", "agent"]
SkillType = Literal[
    "agent-skill",
    "claude-command",
    "plugin-command",
    "plugin-command-inline",
    "copilot-prompt",
    "claude-agent",
    "copilot-agent",
    "external-plugin",
]
ComplianceStatus = Literal["compliant", "loadable", "broken"]
FetchMethod = Literal["api", "clone", "fs", "git"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanOptions(_Model):
    ref: str | None = None
    path: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    def cache_key(self) -> tuple[Any, ...]:
        # `ref` is excluded: the commit SHA already identifies the content.
        return (self.path, tuple(self.include), tuple(self.exclude))


class ScanInfo(_Model):
    id: str
    scanned_at: str
    tool_version: str
    detectors_version: int
    fetch_method: FetchMethod
    duration_ms: int
    options: ScanOptions
    excluded_candidates: int = 0
    warnings: list[str] = Field(default_factory=list)


class SourceInfo(_Model):
    kind: Literal["github", "local"]
    repo_key: str
    host: str | None = None
    owner: str | None = None
    name: str | None = None
    url: str | None = None
    node_id: str | None = None
    requested_ref: str | None = None
    resolved_ref: str | None = None
    commit_sha: str | None = None
    commit_date: str | None = None
    local_path: str | None = None
    remote_url: str | None = None
    dirty: bool | None = None


class RepoMeta(_Model):
    description: str | None = None
    default_branch: str | None = None
    license: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int | None = None
    forks: int | None = None
    visibility: str | None = None
    archived: bool | None = None
    is_fork: bool | None = None
    pushed_at: str | None = None


class Plugin(_Model):
    id: str
    root: str | None = None
    manifest_path: str | None = None
    name: str | None = None
    version: str | None = None
    description: str | None = None
    marketplace_path: str | None = None
    remote_source: Any = None


class Resource(_Model):
    path: str
    bytes: int | None = None
    git_blob_sha: str | None = None


class Violation(_Model):
    code: str
    message: str


class Compliance(_Model):
    status: ComplianceStatus
    violations: list[Violation] = Field(default_factory=list)


class Skill(_Model):
    id: str
    kind: Kind
    type: SkillType
    detector: str
    scope: str | None = None
    path: str | None = None
    dir: str | None = None
    source_pointer: str | None = None
    plugin_id: str | None = None
    # None only in snapshots made before detectors version 2.
    category: Category | None = None
    category_reason: str | None = None
    name: str | None = None
    name_source: str | None = None
    description: str | None = None
    description_source: str | None = None
    frontmatter: dict[str, Any] | None = None
    frontmatter_raw: str | None = None
    frontmatter_error: str | None = None
    body: str | None = None
    body_bytes: int | None = None
    body_lines: int | None = None
    content_sha256: str | None = None
    git_blob_sha: str | None = None
    resources: list[Resource] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    compliance: Compliance
    warnings: list[str] = Field(default_factory=list)


class Stats(_Model):
    skills: int = 0
    agents: int = 0
    # External plugins are placeholders, not skills: counted apart.
    external: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_compliance: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def of(cls, skills: list[Skill]) -> Stats:
        stats = cls()
        for s in skills:
            if s.category == "external":
                stats.external += 1
            elif s.kind == "skill":
                stats.skills += 1
            else:
                stats.agents += 1
            if s.category is not None:
                stats.by_category[s.category] = stats.by_category.get(s.category, 0) + 1
            stats.by_type[s.type] = stats.by_type.get(s.type, 0) + 1
            status = s.compliance.status
            stats.by_compliance[status] = stats.by_compliance.get(status, 0) + 1
        return stats


class Snapshot(_Model):
    schema_version: Literal[1] = 1
    scan: ScanInfo
    source: SourceInfo
    repo: RepoMeta | None = None
    plugins: list[Plugin] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)


# --- organization scans (SPEC section 3.4) ---------------------------------------------

OrgRepoStatus = Literal["scanned", "unchanged", "empty", "failed", "pending"]
OrgScanStatus = Literal["complete", "partial", "stopped", "interrupted"]
ORG_REPO_STATUSES: tuple[OrgRepoStatus, ...] = (
    "scanned",
    "unchanged",
    "empty",
    "failed",
    "pending",
)


class OrgRepo(_Model):
    repo_key: str
    full_name: str
    status: OrgRepoStatus
    snapshot: str | None = None  # file name in the snapshot store
    commit_sha: str | None = None
    skills: int | None = None
    agents: int | None = None
    error: str | None = None


class OrgOptions(_Model):
    include_archived: bool = False
    include_forks: bool = False
    match: list[str] = Field(default_factory=list)
    limit: int | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    force: bool = False


class OrgScan(_Model):
    """One organization scan: which repositories it saw and how each one ended.

    Snapshots stay per repository; this report only links them. Immutable, like them.
    """

    schema_version: Literal[1] = 1
    id: str
    started_at: str
    finished_at: str
    duration_ms: int
    tool_version: str
    host: str
    owner: str  # the login as GitHub spells it
    owner_type: Literal["organization", "user"]
    owner_key: str  # `<host>/<owner>` in lower case: the prefix of its repo keys
    status: OrgScanStatus
    message: str | None = None
    options: OrgOptions
    listed: int = 0
    skipped: dict[str, int] = Field(default_factory=dict)  # archived, fork, name, limit
    api_requests: int = 0
    repos: list[OrgRepo] = Field(default_factory=list)

    def count(self, status: OrgRepoStatus) -> int:
        return sum(1 for r in self.repos if r.status == status)

    @property
    def with_skills(self) -> int:
        return sum(1 for r in self.repos if (r.skills or 0) + (r.agents or 0) > 0)

"""Web UI served on localhost (SPEC section 8.1).

Security model:
- The server binds to 127.0.0.1 by default and checks the Host header, so a web page
  cannot reach it through DNS rebinding.
- A random token in the start URL sets an HttpOnly, SameSite=Strict cookie. Every API
  call needs the cookie; other local users and other sites cannot read the store.
- POST needs a custom header and a JSON body, so a cross-site form cannot start a scan.
- Every string from a snapshot is sanitized server-side and inserted as text. Skill
  bodies are rendered from Markdown with raw HTML and images disabled. A strict CSP
  forbids inline scripts, so injected markup cannot run.
"""

from __future__ import annotations

import http.server
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from markdown_it import MarkdownIt

from skill_atlas import aggregate, categories, store
from skill_atlas.errors import AtlasError
from skill_atlas.model import Snapshot
from skill_atlas.sanitize import sanitize, sanitize_line
from skill_atlas.views import dup_key, permalink

COOKIE = "skill_atlas_token"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
STATIC = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.ico": ("favicon.svg", "image/svg+xml"),
}
MAX_BODY = 64 * 1024


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})

    # Render rules are bound as renderer methods, hence `renderer` first.
    def image(renderer: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
        # Remote images would let a skill track who reads it; show the alt text instead.
        token = tokens[idx]
        alt = md.utils.escapeHtml(token.content or "")
        src = md.utils.escapeHtml(token.attrGet("src") or "")
        return f'<span class="img-placeholder">[image: {alt}] {src}</span>'

    def link_open(renderer: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
        tokens[idx].attrSet("rel", "noopener noreferrer nofollow")
        tokens[idx].attrSet("target", "_blank")
        return str(renderer.renderToken(tokens, idx, options, env))

    md.add_render_rule("image", image)
    md.add_render_rule("link_open", link_open)
    return md


_MD = _markdown()


def _clean(value: Any) -> Any:
    """Sanitize every string in a JSON-like structure."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {sanitize_line(str(k)): _clean(v) for k, v in value.items()}
    return value


def render_markdown(text: str) -> str:
    return str(_MD.render(sanitize(text)))


def snapshot_view(snap: Snapshot, name: str | None) -> dict[str, Any]:
    data = snap.model_dump(mode="json", exclude={"skills"})
    skills = []
    for s in snap.skills:
        item = s.model_dump(mode="json")
        item["body_html"] = render_markdown(s.body) if s.body is not None else None
        item["permalink"] = permalink(snap, s)
        item["dup_key"] = json.dumps(dup_key(s))
        item["relevant"] = categories.matches(s.category, "relevant")
        skills.append(item)
    data["skills"] = skills
    data["file"] = name
    data["types"] = sorted({s.type for s in snap.skills})
    return dict(_clean(data))


def _summary(item: store.Loaded) -> dict[str, Any]:
    snap = item.snapshot
    return {
        "file": item.path.name,
        "scanned_at": snap.scan.scanned_at,
        "commit_sha": snap.source.commit_sha,
        "dirty": snap.source.dirty,
        "skills": snap.stats.skills,
        "agents": snap.stats.agents,
        "external": snap.stats.external,
        "fetch_method": snap.scan.fetch_method,
    }


@dataclass
class Job:
    id: str
    target: str
    state: str = "running"  # running | done | error
    status: str = "starting"
    error: str | None = None
    repo_key: str | None = None
    file: str | None = None
    cache_hit: bool = False
    started: float = field(default_factory=time.monotonic)
    # Finished stages as [name, seconds]; the running stage is in `status`.
    stages: list[list[Any]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return dict(
            _clean(
                {
                    "id": self.id,
                    "target": self.target,
                    "state": self.state,
                    "status": self.status,
                    "error": self.error,
                    "repo_key": self.repo_key,
                    "file": self.file,
                    "cache_hit": self.cache_hit,
                    "elapsed": round(time.monotonic() - self.started, 1),
                    "stages": [list(s) for s in self.stages],
                }
            )
        )


class JobProgress:
    def __init__(self, job: Job) -> None:
        self.job = job
        self._stage = ""
        self._started = time.monotonic()

    def _finish(self) -> None:
        if self._stage:
            self.job.stages.append([self._stage, round(time.monotonic() - self._started, 1)])

    def stage(self, message: str) -> None:
        self._finish()
        self._stage = sanitize_line(message)
        self._started = time.monotonic()
        self.job.status = self._stage

    def detail(self, message: str) -> None:
        self.job.status = f"{self._stage} · {sanitize_line(message)}"

    def done(self) -> None:
        self._finish()
        self._stage = ""


class WebApp:
    """Request-independent state and the API implementation."""

    def __init__(self, store_dir: Path, token: str | None = None) -> None:
        self.store_dir = store_dir
        self.token = token or secrets.token_urlsafe(32)
        self.allowed_hosts: set[str] = set()
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # --- store ----------------------------------------------------------------------

    def _open(self) -> aggregate.Store:
        return aggregate.Store.open(self.store_dir)

    def repos(self) -> dict[str, Any]:
        db = self._open()
        rows = []
        for ident, items in db.by_repo().items():
            latest = items[-1].snapshot
            repo = latest.repo
            src = latest.source
            rows.append(
                {
                    "id": ident,
                    "repo_key": src.repo_key,
                    "description": repo.description if repo else None,
                    "meta": repo.model_dump(mode="json") if repo else None,
                    # What to pass to a rescan: the URL for GitHub, the path for local scans.
                    "rescan_target": src.url or src.local_path,
                    "scans": len(items),
                    "by_category": latest.stats.by_category,
                    "unique": len({dup_key(s) for s in latest.skills}),
                    "total": len(latest.skills),
                    "latest": _summary(items[-1]),
                    "latest_scan": latest.scan.model_dump(mode="json"),
                    "source": latest.model_dump(mode="json")["source"],
                    "history": [_summary(i) for i in reversed(items)],
                }
            )
        rows.sort(key=lambda r: r["latest"]["scanned_at"], reverse=True)
        return dict(_clean({"store": str(self.store_dir), "warnings": db.warnings, "repos": rows}))

    def skills(self, category: str = "relevant") -> dict[str, Any]:
        """Skills from the latest snapshot of every repository, without bodies."""
        db = self._open()
        ids = {id(item): ident for ident, items in db.by_repo().items() for item in items}
        rows = []
        for item in db.latest():
            snap = item.snapshot
            for s in snap.skills:
                if not categories.matches(s.category, category):
                    continue
                rows.append(
                    {
                        "repo_id": ids[id(item)],
                        "repo_key": snap.source.repo_key,
                        "file": item.path.name,
                        "id": s.id,
                        "name": s.name,
                        "description": (s.description or "")[:400],
                        "kind": s.kind,
                        "type": s.type,
                        "category": s.category,
                        "compliance": s.compliance.status,
                        "content_sha256": s.content_sha256,
                        "path": s.path or s.source_pointer,
                    }
                )
        return dict(_clean({"skills": rows}))

    def _snapshot_path(self, name: str) -> Path | None:
        # Only plain file names from the store: no separators, no traversal.
        if (
            not name
            or "/" in name
            or "\\" in name
            or name.startswith(".")
            or not name.endswith(".json")
        ):
            return None
        path = self.store_dir / name
        return path if path.is_file() else None

    def snapshot(self, name: str) -> dict[str, Any] | None:
        path = self._snapshot_path(name)
        if path is None:
            return None
        return snapshot_view(store.load(path), name)

    def raw(self, name: str) -> bytes | None:
        path = self._snapshot_path(name)
        return path.read_bytes() if path is not None else None

    # --- scans ----------------------------------------------------------------------

    def start_scan(self, target: str) -> Job | None:
        with self._lock:
            if any(j.state == "running" for j in self.jobs.values()):
                return None
            job = Job(uuid.uuid4().hex[:12], target)
            self.jobs[job.id] = job
        threading.Thread(target=self._scan, args=(job,), daemon=True).start()
        return job

    def _scan(self, job: Job) -> None:
        from skill_atlas.scan import ScanRequest, run_scan

        try:
            outcome = run_scan(
                ScanRequest(target=job.target), store_dir=self.store_dir, progress=JobProgress(job)
            )
        except AtlasError as exc:
            job.error, job.state = str(exc), "error"
            return
        except Exception as exc:  # report, do not kill the server thread silently
            job.error, job.state = f"unexpected error: {exc!r}", "error"
            return
        job.repo_key = outcome.snapshot.source.repo_key
        job.file = outcome.saved_path.name if outcome.saved_path else None
        job.cache_hit = outcome.cache_hit
        job.state = "done"


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server
    server_version = "skill-atlas"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the terminal shows the URL; per-request logs would bury it

    # --- responses ------------------------------------------------------------------

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    # --- checks ---------------------------------------------------------------------

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.app.allowed_hosts

    def _authorized(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE)
        return morsel is not None and secrets.compare_digest(morsel.value, self.server.app.token)

    # --- routing --------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        app = self.server.app
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            self._send(HTTPStatus.OK, _static(name), ctype)
            return
        if url.path == "/":
            token = query.get("token", [""])[0]
            if token and secrets.compare_digest(token, app.token):
                # Move the token from the URL into a cookie, then drop it from the address bar.
                cookie = f"{COOKIE}={app.token}; HttpOnly; SameSite=Strict; Path=/"
                self._send(
                    HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie}
                )
                return
            if not self._authorized():
                self._send(
                    HTTPStatus.UNAUTHORIZED, _static("login.html"), "text/html; charset=utf-8"
                )
                return
            self._send(HTTPStatus.OK, _static("index.html"), "text/html; charset=utf-8")
            return
        if not url.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "open the URL printed by skill-atlas --web")
            return
        try:
            self._api_get(url.path, query)
        except AtlasError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, sanitize_line(str(exc)))

    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        app = self.server.app
        name = query.get("file", [""])[0]
        if path == "/api/repos":
            self._json(HTTPStatus.OK, app.repos())
        elif path == "/api/skills":
            category = query.get("category", ["relevant"])[0]
            if category not in categories.SELECTORS:
                self._error(HTTPStatus.BAD_REQUEST, "unknown category")
            else:
                self._json(HTTPStatus.OK, app.skills(category))
        elif path == "/api/snapshot":
            view = app.snapshot(name)
            if view is None:
                self._error(HTTPStatus.NOT_FOUND, "no such snapshot")
            else:
                self._json(HTTPStatus.OK, view)
        elif path == "/api/snapshot/raw":
            raw = app.raw(name)
            if raw is None:
                self._error(HTTPStatus.NOT_FOUND, "no such snapshot")
                return
            disposition = f'attachment; filename="{name}"'
            self._send(HTTPStatus.OK, raw, "application/json", {"Content-Disposition": disposition})
        elif path.startswith("/api/scan/"):
            job = app.jobs.get(path.removeprefix("/api/scan/"))
            if job is None:
                self._error(HTTPStatus.NOT_FOUND, "no such scan")
            else:
                self._json(HTTPStatus.OK, job.view())
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "not authorized")
            return
        # A cross-site form cannot set this header or a JSON content type without a preflight.
        if self.headers.get("X-Skill-Atlas") != "1" or not self.headers.get(
            "Content-Type", ""
        ).startswith("application/json"):
            self._error(HTTPStatus.FORBIDDEN, "missing X-Skill-Atlas header or JSON body")
            return
        if urlsplit(self.path).path != "/api/scan":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
            return
        target = body.get("target") if isinstance(body, dict) else None
        if not isinstance(target, str) or not target.strip():
            self._error(HTTPStatus.BAD_REQUEST, "target is required")
            return
        job = self.server.app.start_scan(target.strip())
        if job is None:
            self._error(HTTPStatus.CONFLICT, "a scan is already running")
            return
        self._json(HTTPStatus.ACCEPTED, job.view())


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: WebApp) -> None:
        super().__init__(address, Handler)
        self.app = app
        host, port = str(self.server_address[0]), self.server_address[1]
        names = {host, "localhost", "127.0.0.1", "[::1]"}
        app.allowed_hosts = {f"{n}:{port}" for n in names}

    @property
    def url(self) -> str:
        host, port = str(self.server_address[0]), self.server_address[1]
        shown = "localhost" if host in ("127.0.0.1", "::1") else host
        return f"http://{shown}:{port}/?token={self.app.token}"


def _static(name: str) -> bytes:
    return resources.files("skill_atlas.web").joinpath("static", name).read_bytes()


def serve(
    store_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    announce: Any = print,
) -> None:
    """Serve the web UI until Ctrl+C. Port 0 picks a free port."""
    import webbrowser

    try:
        server = Server((host, port), WebApp(store_dir))
    except OSError as exc:
        raise AtlasError(f"cannot listen on {host}:{port}: {exc.strerror or exc}") from None
    if host not in ("127.0.0.1", "localhost", "::1"):
        announce(f"warning: listening on {host}; anyone with the URL can read the store and scan")
    announce(f"skill-atlas web UI: {server.url}")
    announce("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

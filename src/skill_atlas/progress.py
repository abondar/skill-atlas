"""Scan progress reporting. Output goes to stderr, so stdout stays clean for --plain and JSON."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from rich.console import Console
from rich.status import Status
from rich.text import Text

from skill_atlas.sanitize import sanitize_line

if TYPE_CHECKING:
    from skill_atlas.model import OrgRepo
    from skill_atlas.org import OrgState


class Progress(Protocol):
    def stage(self, message: str) -> None:
        """Start a new stage. The previous stage counts as finished."""
        ...

    def detail(self, message: str) -> None:
        """Transient detail for the current stage, such as a counter."""
        ...

    def done(self) -> None: ...


class NullProgress:
    def stage(self, message: str) -> None:
        pass

    def detail(self, message: str) -> None:
        pass

    def done(self) -> None:
        pass


class ConsoleProgress:
    """Spinner with elapsed time on a terminal; one line per stage otherwise."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.live = console.is_terminal
        self._status: Status | None = None
        self._stage: str | None = None
        self._stage_started = 0.0
        self._detail = ""
        self._last_render = 0.0

    def _finish_stage(self) -> None:
        if self._stage is None:
            return
        elapsed = time.monotonic() - self._stage_started
        if self.live:
            self.console.print(Text(f"✓ {self._stage} ({elapsed:.1f}s)", style="dim"))
        self._stage = None

    def _render(self) -> None:
        if self._status is None or self._stage is None:
            return
        elapsed = time.monotonic() - self._stage_started
        text = f"{self._stage} ({elapsed:.0f}s)"
        if self._detail:
            text += f" · {self._detail}"
        self._status.update(Text(text))

    def stage(self, message: str) -> None:
        self._finish_stage()
        self._stage = sanitize_line(message)
        self._stage_started = time.monotonic()
        self._detail = ""
        if not self.live:
            self.console.print(Text(f"… {self._stage}"))
            return
        if self._status is None:
            self._status = Status(Text(self._stage), console=self.console)
            self._status.start()
        self._render()

    def detail(self, message: str) -> None:
        self._detail = sanitize_line(message)
        now = time.monotonic()
        # Throttle: git progress can emit hundreds of updates per second.
        if self.live and now - self._last_render >= 0.1:
            self._last_render = now
            self._render()

    def done(self) -> None:
        self._finish_stage()
        if self._status is not None:
            self._status.stop()
            self._status = None


def org_status_line(state: OrgState, now: float | None = None) -> str:
    """One line for an organization scan in progress; shared by the CLI and the TUI."""
    now = time.time() if now is None else now
    owner = sanitize_line(state.owner)
    if state.phase == "listing":
        return f"{owner}: listing repositories · {state.listed}"
    parts = [f"{owner}: {state.done}/{state.total} repositories"]
    parts.append(f"{state.with_skills} with skills")
    if state.failures:
        parts.append(f"{len(state.failures)} failed")
    if state.wait_until is not None and state.wait_until > now:
        until = time.strftime("%H:%M:%S", time.gmtime(state.wait_until))
        parts.append(
            f"waiting for the GitHub rate limit until {until} UTC "
            f"({(state.wait_until - now) / 60:.0f} min)"
        )
    elif state.active:
        parts.append("scanning " + ", ".join(sanitize_line(a) for a in state.active))
    if state.rate_remaining is not None:
        parts.append(f"API quota left {state.rate_remaining}")
    return " · ".join(parts)


class ConsoleOrgProgress:
    """Organization scan progress: a status line on a terminal, plus one line per
    repository with skills or an error; one line per repository otherwise."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.live = console.is_terminal
        self._status: Status | None = None
        self._last_render = 0.0
        self._state: OrgState | None = None

    def update(self, state: OrgState) -> None:
        self._state = state
        if state.phase == "done":
            self.done()
            return
        if not self.live:
            return
        now = time.monotonic()
        if self._status is None:
            self._status = Status(Text(org_status_line(state)), console=self.console)
            self._status.start()
        elif now - self._last_render >= 0.1:
            self._last_render = now
            self._status.update(Text(org_status_line(state)))

    def finished(self, repo: OrgRepo) -> None:
        name = sanitize_line(repo.full_name)
        if repo.status == "failed":
            self.console.print(Text(f"✗ {name}: {sanitize_line(repo.error or '')}", style="red"))
            return
        found = (repo.skills or 0) + (repo.agents or 0)
        if self.live and not found:
            return
        counts = f"{repo.skills or 0} skills, {repo.agents or 0} agents" if found else "no skills"
        state = self._state
        prefix = f"[{state.done + 1}/{state.total}] " if state is not None and not self.live else ""
        style = "" if found else "dim"
        self.console.print(Text(f"{prefix}{name}: {repo.status}, {counts}", style=style))

    def done(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

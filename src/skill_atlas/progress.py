"""Scan progress reporting. Output goes to stderr, so stdout stays clean for --plain and JSON."""

from __future__ import annotations

import time
from typing import Protocol

from rich.console import Console
from rich.status import Status
from rich.text import Text

from skill_atlas.sanitize import sanitize_line


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

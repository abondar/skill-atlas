"""Browser end-to-end test of the web UI: headless Chrome driven by tests/e2e/web.mjs.

Skips without Chrome or Node 22+. CI sets SKILL_ATLAS_REQUIRE_E2E=1, which turns the skip
into a failure so the test cannot silently stop running there.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from skill_atlas import store
from skill_atlas.web import Server, WebApp
from tests.conftest import make_tree, scan_path

SCRIPT = Path(__file__).parent / "e2e" / "web.mjs"
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def skill(name: str, description: str, body: str = "Body.\n") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def _unavailable(reason: str) -> None:
    if os.environ.get("SKILL_ATLAS_REQUIRE_E2E") == "1":
        pytest.fail(f"browser e2e test required but {reason}")
    pytest.skip(reason)


def _chrome() -> str | None:
    if env := os.environ.get("CHROME"):
        return env
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
        if found := shutil.which(candidate):
            return found
    return None


def _node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    version = subprocess.run(
        [node, "-p", "process.versions.node"], capture_output=True, text=True, check=False
    ).stdout.strip()
    major = int(version.split(".")[0]) if version[:1].isdigit() else 0
    return node if major >= 22 else None  # built-in WebSocket client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_web_ui_in_a_real_browser(tmp_path: Path) -> None:
    chrome, node = _chrome(), _node()
    if chrome is None:
        _unavailable("Chrome is not installed")
    if node is None:
        _unavailable("Node 22+ is not installed")
    assert chrome is not None and node is not None

    weather_body = "Get the weather.\n\n1. Extract the location.\n2. Call `scripts/weather.py`.\n"
    java_body = (
        "Find the Kotlin snippet in the docs, write the same example in Java next to it, "
        "compile both with the docs Gradle task and link them from the page header.\n"
    ) * 3
    koog = make_tree(
        tmp_path / "koog",
        {
            ".claude/skills/add-java/SKILL.md": skill(
                "add-java", "Adds Java snippets to docs.", java_body
            ),
            # A partial duplicate of add-java: the same body plus one step.
            ".claude/skills/split/SKILL.md": skill(
                "split", "Splits JVM and non-JVM code.", java_body + "Then split the modules.\n"
            ),
            "integration-tests/src/jvmTest/resources/skills/weather-retrieval/SKILL.md": skill(
                "weather-retrieval", "Retrieves the weather.", weather_body
            ),
            "integration-tests/src/jvmTest/resources/skills/arithmetic/SKILL.md": skill(
                "arithmetic", "Evaluates arithmetic."
            ),
        },
    )
    copied = skill("mps-actions", "Guidelines for MPS actions.")
    mps = make_tree(
        tmp_path / "mps",
        {
            ".agents/skills/mps-actions/SKILL.md": copied,
            ".claude/skills/mps-actions/SKILL.md": copied,
            # The bundled copy drifted: another version of the same skill.
            "plugins/mcp/resources/skills/mps-actions/SKILL.md": copied + "Also menus.\n",
            ".agents/skills/pdf/SKILL.md": skill("pdf", "Works with PDF files."),
        },
    )
    fresh = make_tree(
        tmp_path / "fresh",
        # Copied from koog's add-java under another name: found under Other repos.
        {".claude/skills/hello/SKILL.md": skill("hello", "Says hello.", java_body)},
    )
    scans = tmp_path / "scans"
    for root in (koog, mps):
        store.save(scan_path(root), scans)

    server = Server(("127.0.0.1", 0), WebApp(scans, token="e2e"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = dict(
            os.environ,
            CHROME=chrome,
            BASE=f"http://127.0.0.1:{server.server_address[1]}",
            OUT=str(tmp_path),
            SCAN_TARGET=str(fresh),
            DEBUG_PORT=str(_free_port()),
        )
        proc = subprocess.run(
            [node, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=240, check=False
        )
    finally:
        server.shutdown()
        server.server_close()
    sys.stdout.write(proc.stdout)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}\nscreenshots: {tmp_path}"
    assert "E2E PASSED" in proc.stdout

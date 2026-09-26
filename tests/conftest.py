"""Shared pytest helpers: locate the fixtures and run them for real.

Every parser test drives the *actual* fixture scripts through the scanner, so
the tests exercise the same code path as ``helpui scan`` instead of asserting
against hand-written help strings that could drift from reality.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

#: Environment for fixture subprocesses.
#:
#: A *fixed* width (100) is forced because argparse/click/typer reflow usage and
#: help text to the terminal width. Without this, the captured help differs
#: between a 80-column CI runner and a 120-column dev terminal, which would make
#: the parser tests (and the golden generator tests) environment dependent.
FIXTURE_ENV: dict[str, str] = {
    "COLUMNS": "100",
    "LINES": "50",
    "TERM": "dumb",
    "NO_COLOR": "1",
    "PYTHONIOENCODING": "utf-8",
    # Keep typer from paging/adjusting to an interactive terminal.
    "FORCE_COLOR": "0",
}

#: fixture module name -> file path
FIXTURE_FILES: dict[str, Path] = {
    "argparse": FIXTURES / "sample_argparse.py",
    "click": FIXTURES / "sample_click.py",
    "typer": FIXTURES / "sample_typer.py",
}


def fixture_path(name: str) -> Path:
    return FIXTURE_FILES[name]


def fixture_env() -> dict[str, str]:
    """``os.environ`` plus the deterministic fixture settings."""
    env = dict(os.environ)
    env.update(FIXTURE_ENV)
    return env


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@lru_cache(maxsize=32)
def help_text(name: str, subcommand: str | None = None) -> str:
    """Run a fixture's ``--help`` once per session and cache the output."""
    argv = [sys.executable, str(fixture_path(name))]
    if subcommand:
        argv.extend(subcommand.split())
    argv.append("--help")
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        env=fixture_env(),
    )
    assert completed.returncode == 0, (
        f"fixture {name} --help failed ({completed.returncode}):\n{completed.stderr}"
    )
    return completed.stdout or completed.stderr


def run_fixture(name: str, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Run a fixture with real arguments (used by executor/e2e tests)."""
    argv = [sys.executable, str(fixture_path(name)), *args]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=fixture_env(),
    )


def parse_json_output(text: str) -> dict[str, object]:
    """Parse the JSON envelope printed by ``helpui scan --json``."""
    loaded: object = json.loads(text)
    assert isinstance(loaded, dict), f"expected a JSON object, got {type(loaded).__name__}"
    return loaded

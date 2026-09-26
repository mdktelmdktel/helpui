#!/usr/bin/env python3
"""Task-5 regression evidence: option types across all three fixtures.

Prints one ``OPT <fixture> <command> <dest> type=<t>`` line per option plus a
``VERDICT`` line, so a regression on the red-line list is visible in one glance.

Red lines (must not change): config -> file, outdir -> dir, retries -> int,
ratio -> float, format -> choice, verbose/dry_run -> bool, and the password
fix: click/typer `--password` -> password.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpui.parsers.argparse_parser import ArgparseParser  # noqa: E402
from helpui.parsers.click_parser import ClickParser  # noqa: E402
from helpui.parsers.typer_parser import TyperParser  # noqa: E402
from tests.conftest import help_text  # noqa: E402

PARSERS = {"argparse": ArgparseParser(), "click": ClickParser(), "typer": TyperParser()}
COMMANDS = ("convert", "report")

#: dest -> expected type, per fixture, where the expectation is unambiguous.
#:
#: `retries`/`ratio` are int/float only for click and typer: argparse prints
#: `--retries RETRIES` with no textual type signal, which is the documented
#: SPEC §4.6.1 limitation ("argparse numeric types are invisible") and is NOT a
#: regression this script may flag. They are therefore checked per fixture.
RED_LINES: dict[str, str] = {
    "outdir": "dir",
    "format": "choice",
    "config": "file",
    "verbose": "bool",
    "dry_run": "bool",
    "password": "password",
}

#: (fixture, dest) -> expected type, for the typed metavars argparse cannot see.
TYPED_PER_FIXTURE: dict[tuple[str, str], str] = {
    ("click", "retries"): "int",
    ("click", "ratio"): "float",
    ("typer", "retries"): "int",
    ("typer", "ratio"): "float",
}

failures: list[str] = []
for fixture, parser in PARSERS.items():
    for command_name in COMMANDS:
        page = help_text(fixture, command_name)
        command = parser.parse_subcommand(
            page, tool_name=fixture, tool_path=f"{fixture}.py", command_name=command_name
        )
        for option in command.options:
            print(f"OPT {fixture:9} {command_name:8} {option.dest:10} type={option.type}")
            expected = TYPED_PER_FIXTURE.get((fixture, option.dest)) or RED_LINES.get(option.dest)
            if expected is not None and option.type != expected:
                failures.append(f"{fixture}/{command_name}/{option.dest}={option.type} want {expected}")

# The root page carries --config and --verbose for the click/argparse fixtures.
for fixture in ("argparse", "click"):
    page = help_text(fixture)
    spec = PARSERS[fixture].parse(page, tool_name=fixture, tool_path=f"{fixture}.py")
    for command in spec.commands:
        for option in command.options:
            print(f"OPT {fixture:9} {'(root)':8} {option.dest:10} type={option.type}")
            expected = TYPED_PER_FIXTURE.get((fixture, option.dest)) or RED_LINES.get(option.dest)
            if expected is not None and option.type != expected:
                failures.append(f"{fixture}/root/{option.dest}={option.type} want {expected}")

print(f"VERDICT regressions={failures or 'none'}")
sys.exit(1 if failures else 0)

#!/usr/bin/env python3
"""Fixture: a classic ``argparse`` CLI used by HelpUI's parser tests.

The option mix is deliberate -- it covers everything Phase 1 must recognise:

* positionals (``input_file``) and a choice pseudo-positional (``{json,csv,tsv}``)
* flags (``-v/--verbose``), short+long aliases (``-o/--output``)
* typed values (``--retries`` int, ``--ratio`` float)
* ``choices`` with a default
* a ``required=True`` option
* a repeatable option (``--tag``, ``action="append"``)
* metavars that imply file / dir / password widgets
* subcommands via ``add_subparsers``

Run ``python tests/fixtures/sample_argparse.py --help`` to inspect the output the
tests parse; run it with real arguments to see it execute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the parser (imported by tests to avoid re-implementing the CLI)."""
    parser = argparse.ArgumentParser(
        prog="sample_argparse",
        description="Sample argparse tool used by HelpUI tests.",
        epilog="See SPEC.md for the HelpUI coverage matrix.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--config", default="config.toml", help="Path to the config file")
    parser.add_argument("--workdir", default=".", help="Working directory for the run")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    convert = subparsers.add_parser(
        "convert",
        help="Convert an input file to another format.",
        description="Convert an input file to another format.",
    )
    convert.add_argument("input_file", help="File to convert")
    convert.add_argument("-o", "--output", default="out.txt", help="Output path")
    convert.add_argument(
        "-f",
        "--format",
        choices=["json", "csv", "tsv"],
        default="json",
        help="Output format",
    )
    convert.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    convert.add_argument("--retries", type=int, default=3, help="Number of retries")
    convert.add_argument("--ratio", type=float, default=1.5, help="Scaling ratio")
    convert.add_argument("--token", required=True, help="API token used for the upload")
    convert.add_argument("--tag", action="append", help="Tag to attach (repeatable)")
    convert.add_argument("--password", help="Password for the archive")

    report = subparsers.add_parser(
        "report",
        help="Generate a summary report.",
        description="Generate a summary report.",
    )
    report.add_argument("--limit", type=int, default=10, help="Maximum number of rows")
    report.add_argument(
        "--sort",
        choices=["name", "size", "date"],
        default="name",
        help="Sort key for the report",
    )
    report.add_argument("--outdir", default="reports", help="Directory for generated reports")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = {
        "command": args.command,
        "args": {k: v for k, v in vars(args).items() if v is not None},
    }
    if args.command == "convert" and getattr(args, "output", None):
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

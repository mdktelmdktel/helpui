#!/usr/bin/env python3
"""Verification fixture: a CLI that answers ``--help`` instantly but *sleeps*.

Used by ``scripts/verify_generated_app.py`` to exercise the cancel and timeout
paths of a generated HelpUI project. ``--help`` must return immediately, or the
scanner's 20s help timeout fires; a real run then sleeps so there is something
to cancel.

Usage::

    python slow_tool.py --help
    python slow_tool.py work --seconds 30 --tag a --tag b
    python slow_tool.py quick
    python slow_tool.py blob --file FILE
"""

from __future__ import annotations

import argparse
import sys
import time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slow_tool", description="Sleepy tool used as a generation fixture.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    work = subparsers.add_parser("work", help="Sleep for --seconds.", description="Sleep for --seconds.")
    work.add_argument("--seconds", type=int, default=30, help="How long to sleep")
    work.add_argument("--tag", action="append", help="Tag to attach (repeatable)")
    work.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")

    quick = subparsers.add_parser("quick", help="Return immediately.", description="Return immediately.")
    quick.add_argument("--note", default="done", help="Note to echo")

    # A `type=file` option: the metavar FILE makes HelpUI render an upload widget.
    blob = subparsers.add_parser("blob", help="Accept an uploaded file.", description="Accept an uploaded file.")
    blob.add_argument("--file", metavar="FILE", help="File to consume")

    # An upload declared as a required *positional* (metavar FILE, unbracketed in
    # usage) -- the shape every real CLI with a file input has.
    send = subparsers.add_parser("send", help="Send a file.", description="Send a file.")
    send.add_argument("input_file", metavar="FILE", help="File to send")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = getattr(args, "command", None)
    if command == "work":
        sys.stdout.write("sleeping\n")
        sys.stdout.flush()
        time.sleep(float(args.seconds))
        print("awake")
        return 0
    if command == "quick":
        print(f"quick {args.note}")
        return 0
    if command == "blob":
        print(f"blob {args.file}")
        return 0
    if command == "send":
        print(f"send {args.input_file}")
        return 0
    print(f"root verbose={args.verbose}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

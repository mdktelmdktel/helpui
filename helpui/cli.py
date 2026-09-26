"""``helpui`` command line entry point.

Contract (stable, machine-readable)::

    helpui scan <tool> [--json] [--subcommand NAME]
    helpui generate <tool> --out <dir> [--port 8000]
    helpui serve <dir> [--port 8000] [--host 127.0.0.1]
    helpui test <dir>

``scan`` and ``test`` print JSON on stdout.  All diagnostics go to stderr so
that ``helpui scan tool --json | jq`` is always safe.  Exit codes:

* ``0`` -- success
* ``1`` -- the operation failed (tool missing, unknown framework, bad dir)
* ``2`` -- bad command-line usage (argparse's own default)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from helpui import __version__
from helpui.scanner_service import ScanFailure, ScanReport, scan_tool

#: Version of the JSON envelope emitted by ``scan --json`` and ``test``.
SCHEMA_VERSION = 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helpui",
        description="Generate a local WebUI for any CLI tool from its --help output.",
    )
    parser.add_argument("--version", action="version", version=f"helpui {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan = subparsers.add_parser("scan", help="Scan a tool's --help output and print its spec.")
    scan.add_argument("tool", help="Tool to scan: a command name, a .py file, or a command line.")
    scan.add_argument("--json", action="store_true", help="Emit the spec as JSON.")
    scan.add_argument("--subcommand", default=None, help="Scan only this subcommand's help page.")
    scan.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation; use 0 for compact output (default: 2).",
    )
    scan.set_defaults(func=_cmd_scan)

    generate = subparsers.add_parser("generate", help="Generate a standalone WebUI project.")
    generate.add_argument("tool", help="Tool to scan and wrap.")
    generate.add_argument("--out", required=True, help="Directory to write the project into.")
    generate.add_argument("--port", type=int, default=8000, help="Default port baked into the project.")
    generate.add_argument("--host", default="127.0.0.1", help="Default host baked into the project.")
    generate.add_argument("--force", action="store_true", help="Overwrite an existing directory.")
    generate.set_defaults(func=_cmd_generate)

    serve = subparsers.add_parser("serve", help="Run a generated WebUI project.")
    serve.add_argument("dir", help="Generated project directory.")
    serve.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000).")
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    serve.add_argument("--reload", action="store_true", help="Enable auto-reload (development).")
    serve.set_defaults(func=_cmd_serve)

    test = subparsers.add_parser("test", help="Smoke-test a generated WebUI project.")
    test.add_argument("dir", help="Generated project directory.")
    test.add_argument("--timeout", type=float, default=60.0, help="Per-check timeout in seconds.")
    test.set_defaults(func=_cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return int(args.func(args))


# -- scan -------------------------------------------------------------------


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        report = scan_tool(args.tool, subcommand=args.subcommand)
    except ScanFailure as failure:
        return _emit_failure(failure, as_json=args.json)
    except Exception as exc:
        return _emit_failure(
            ScanFailure("internal_error", f"unexpected error: {exc}"), as_json=args.json
        )

    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    indent = None if args.indent <= 0 else args.indent
    if args.json:
        payload = {
            "ok": "true",
            "schema_version": SCHEMA_VERSION,
            "spec": report.spec.to_dict(),
            "detection": {
                "parser_kind": report.detection.kind,
                "confidence": report.detection.confidence,
                "reason": report.detection.reason,
            },
            "warnings": report.warnings,
        }
        print(json.dumps(payload, indent=indent, ensure_ascii=False))
    else:
        _print_spec_human(report)
    return 0


def _print_spec_human(report: ScanReport) -> None:
    spec = report.spec
    print(f"{spec.tool_name}  [{spec.parser_kind}]  ({spec.tool_path})")
    if spec.description:
        print(f"  {spec.description}")
    print(f"  detection: {report.detection.confidence} - {report.detection.reason}")
    for command in spec.commands:
        label = command.name or "(root)"
        print(f"\n  {label}: {command.help}" if command.help else f"\n  {label}")
        for option in command.options:
            _print_option(option, indent="    ")
        for positional in command.positionals:
            _print_option(positional, indent="    ", positional=True)
        if command.subcommands:
            print(f"    subcommands: {', '.join(command.subcommands)}")


def _print_option(option: Any, *, indent: str, positional: bool = False) -> None:
    req = " (required)" if option.required else ""
    default = f" [default: {option.default}]" if option.default is not None else ""
    choices = f" choices={option.choices}" if option.choices else ""
    kind = "arg " if positional else "opt "
    print(f"{indent}{kind}{option.name:<18} {option.type:<8}{req}{default}{choices}")
    if option.help:
        print(f"{indent}    {option.help}")


def _emit_failure(failure: ScanFailure, *, as_json: bool) -> int:
    if as_json:
        payload: dict[str, Any] = {
            "ok": "false",
            "schema_version": SCHEMA_VERSION,
            "code": failure.code,
            "error": failure.message,
        }
        if failure.hint:
            payload["hint"] = failure.hint
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"error: {failure.message}", file=sys.stderr)
        if failure.hint:
            print(f"hint: {failure.hint}", file=sys.stderr)
    return 1


# -- generate / serve / test --------------------------------------------------
#
# These subcommands are declared in Phase 1 so that `--help` is stable and the
# CLI contract is fixed up front; their implementations land in later phases
# (2: generator, 3-4: runtime, 5: test). Until then they exit with a clear
# NOT_IMPLEMENTED code on stderr and status 1, instead of an ImportError
# traceback.

NOT_IMPLEMENTED = "not_implemented"


def _not_implemented(command: str, phase: str) -> int:
    print(
        f"error: `helpui {command}` is not implemented yet (planned for phase {phase})",
        file=sys.stderr,
    )
    return 1


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        from helpui.generator import GenerationError, generate_project
    except ImportError:  # pragma: no cover - phase 2
        return _not_implemented("generate", "2")

    try:
        report = scan_tool(args.tool)
    except ScanFailure as failure:
        return _emit_failure(failure, as_json=False)
    try:
        result = generate_project(
            report.spec,
            args.out,
            default_port=args.port,
            default_host=args.host,
            force=args.force,
        )
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"generated WebUI for {report.spec.tool_name} in {result.out_dir}")
    print(f"run it with:  helpui serve {result.out_dir} --port {args.port}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        from helpui.runtime.server import serve_project
    except ImportError:  # pragma: no cover - phase 3
        return _not_implemented("serve", "3")

    return serve_project(args.dir, host=args.host, port=args.port, reload=args.reload)


def _cmd_test(args: argparse.Namespace) -> int:
    try:
        from helpui.selftest import run_project_tests
    except ImportError:  # pragma: no cover - phase 5
        return _not_implemented("test", "5")

    result = run_project_tests(args.dir, timeout=args.timeout)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

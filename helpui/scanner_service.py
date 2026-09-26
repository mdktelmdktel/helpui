"""High-level scan: resolve a tool, detect its framework, parse its help.

This module is the single entry point used by ``helpui scan`` and by
``helpui generate``.  It owns the *policy* decisions that the individual
parsers deliberately do not make:

* how deep to recurse into subcommands,
* how to behave when a subcommand's help cannot be fetched,
* what to report when the framework is ``unknown``.

Every failure mode raises :class:`ScanFailure` with a machine-readable ``code``
so that ``helpui scan --json`` can emit a stable error object instead of a
traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from helpui.model import CLICommand, CLISpec
from helpui.parsers.base import flatten
from helpui.parsers.detector import DetectResult, detect_parser, get_parser, looks_like_help
from helpui.scanner import HelpResult, ScanError, resolve_tool, run_help, tool_display_name

#: How deep to follow subcommands. ``tool a b --help`` is depth 2; typer's
#: ``admin reset`` needs 2, so we allow a little headroom and stop there.
MAX_SUBCOMMAND_DEPTH = 2

#: Stable, machine-readable failure codes reported by ``scan --json``.
CODE_TOOL_NOT_FOUND = "tool_not_found"
CODE_NO_HELP = "no_help_output"
CODE_UNKNOWN_FRAMEWORK = "unknown_framework"
CODE_SUBCOMMAND_NOT_FOUND = "subcommand_not_found"
CODE_PARSE_FAILED = "parse_failed"


class ScanFailure(RuntimeError):
    """A scan that could not produce a usable :class:`~helpui.model.CLISpec`."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        payload = {"ok": "false", "code": self.code, "error": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass
class ScanReport:
    """A :class:`~helpui.model.CLISpec` plus everything learned while building it."""

    spec: CLISpec
    detection: DetectResult
    #: Non-fatal problems, e.g. a subcommand whose help could not be fetched.
    warnings: list[str] = field(default_factory=list)


def scan_tool(
    tool: str,
    *,
    subcommand: str | None = None,
    max_depth: int = MAX_SUBCOMMAND_DEPTH,
) -> ScanReport:
    """Scan ``tool`` and return a fully-populated report.

    ``subcommand`` restricts the scan to one subcommand's help page, which is
    what ``helpui scan <tool> --subcommand NAME`` asks for.
    """
    try:
        tool_argv = resolve_tool(tool)
    except ScanError as exc:
        raise ScanFailure(CODE_TOOL_NOT_FOUND, str(exc)) from exc

    tool_path = tool_argv[-1]
    name = tool_display_name(tool_argv)

    if subcommand:
        return _scan_one_subcommand(tool_argv, subcommand, name=name)

    try:
        root_help = run_help(tool_argv)
    except ScanError as exc:
        raise ScanFailure(
            CODE_NO_HELP,
            str(exc),
            hint="The tool could not be executed. Check the path and that it accepts --help.",
        ) from exc

    help_text = root_help.text
    if not looks_like_help(help_text):
        # Distinguish "the tool printed nothing useful" from "the tool printed
        # help we do not recognise". Both are failures, but the codes tell the
        # caller which one to act on: a missing --help is the tool's problem,
        # `unknown_framework` is HelpUI's.
        if root_help.returncode != 0 or not help_text.strip():
            raise ScanFailure(
                CODE_NO_HELP,
                f"`{' '.join(root_help.argv)}` produced no usage information "
                f"(exit code {root_help.returncode})",
                hint="HelpUI needs a non-interactive tool that prints usage on --help or -h.",
            )
        raise ScanFailure(
            CODE_UNKNOWN_FRAMEWORK,
            "captured help output but no `usage:` line was found",
            hint="Supported frameworks: argparse, click, typer. See SPEC.md for the coverage matrix.",
        )

    detection = detect_parser(help_text, tool_path=tool_path)
    if not detection.ok:
        raise ScanFailure(
            CODE_UNKNOWN_FRAMEWORK,
            "could not identify the CLI framework from the help output",
            hint="Supported frameworks: argparse, click, typer.",
        )
    parser = get_parser(detection.kind)
    if parser is None:  # pragma: no cover - detector and registry are in sync
        raise ScanFailure(CODE_UNKNOWN_FRAMEWORK, f"no parser registered for {detection.kind!r}")

    warnings: list[str] = []
    try:
        spec = parser.parse(help_text, tool_name=name, tool_path=tool_path)
    except Exception as exc:
        raise ScanFailure(CODE_PARSE_FAILED, f"parser {detection.kind} failed: {exc}") from exc

    # Subcommands from the root page carry no options of their own: their
    # options only appear on the subcommand's own help page. Fetch those pages
    # so the generated form is complete.
    _expand_subcommands(parser, tool_argv, spec, warnings, max_depth=max_depth)

    return ScanReport(spec=spec, detection=detection, warnings=warnings)


def _expand_subcommands(
    parser: object,
    tool_argv: list[str],
    spec: CLISpec,
    warnings: list[str],
    *,
    max_depth: int,
) -> None:
    """Replace name-only subcommands with fully parsed ones.

    Mutates ``spec`` in place.  Nested sub-apps (typer's ``admin reset``) are
    appended to ``spec.commands`` under their dotted path (``"admin reset"``),
    which keeps :class:`~helpui.model.CLISpec` flat and easy to render while
    still preserving the hierarchy in the name.

    Failures are recorded as warnings: a tool with one broken subcommand should
    still produce a usable WebUI for the rest.
    """
    parse_sub = getattr(parser, "parse_subcommand", None)
    if parse_sub is None:  # pragma: no cover - all shipped parsers implement it
        return

    # (command object, argv path, depth) breadth-first so shallow commands are
    # expanded before their nested children.
    queue: list[tuple[CLICommand, list[str], int]] = [
        (cmd, [cmd.name], 1) for cmd in spec.commands if cmd.name
    ]
    seen: set[str] = {cmd.name for cmd in spec.commands if cmd.name}

    while queue:
        command, path, depth = queue.pop(0)
        try:
            result: HelpResult = run_help(tool_argv, subcommand=" ".join(path))
        except ScanError as exc:
            warnings.append(f"could not fetch help for `{' '.join(path)}`: {exc}")
            continue
        if not looks_like_help(result.text):
            warnings.append(f"`{' '.join(path)} --help` produced no usage information")
            continue
        try:
            parsed = parse_sub(
                result.text,
                tool_name=spec.tool_name,
                tool_path=spec.tool_path,
                command_name=command.name,
            )
        except Exception as exc:
            warnings.append(f"could not parse help for `{' '.join(path)}`: {exc}")
            continue

        command.options = parsed.options
        command.positionals = parsed.positionals
        # Keep the richer of the two descriptions.
        if parsed.help and (not command.help or len(parsed.help) > len(command.help)):
            command.help = parsed.help
        if isinstance(parsed, CLICommand) and parsed.subcommands:
            # `subcommands` on the parsed command is the *advertised* list; store
            # it de-duplicated so the generated UI links to each child once.
            command.subcommands = sorted(set(parsed.subcommands))

        if depth >= max_depth:
            continue

        # Discover nested sub-apps from this page's own usage/commands section.
        for nested in _nested_command_names(parsed):
            nested_path = [*path, nested]
            key = " ".join(nested_path)
            if key in seen:
                continue
            seen.add(key)
            command.subcommands = sorted({*command.subcommands, nested})
            spec.commands.append(CLICommand(name=key, help=""))
            queue.append((spec.commands[-1], nested_path, depth + 1))


def _nested_command_names(command: CLICommand) -> list[str]:
    """Names advertised as subcommands on a command's help page."""
    return list(command.subcommands)


def _scan_one_subcommand(tool_argv: list[str], subcommand: str, *, name: str) -> ScanReport:
    """Scan a single subcommand's help page (``--subcommand NAME``)."""
    try:
        result = run_help(tool_argv, subcommand=subcommand)
    except ScanError as exc:
        raise ScanFailure(CODE_SUBCOMMAND_NOT_FOUND, str(exc)) from exc
    if not looks_like_help(result.text):
        raise ScanFailure(
            CODE_SUBCOMMAND_NOT_FOUND,
            f"`{' '.join(result.argv)}` produced no usage information",
        )

    # A framework that does not know the subcommand prints its *own* usage on
    # stderr and exits non-zero (click does this for an unknown group member).
    # Treating that page as the subcommand's would silently produce a spec for
    # the wrong command, so a non-zero exit combined with stderr output is a
    # hard failure.
    if result.returncode != 0:
        detail = flatten(result.stderr.strip()) or flatten(result.stdout.strip())
        raise ScanFailure(
            CODE_SUBCOMMAND_NOT_FOUND,
            f"`{' '.join(result.argv)}` exited with code {result.returncode}"
            + (f": {detail.splitlines()[0]}" if detail else ""),
            hint=f"Check that {subcommand!r} is a real subcommand of this tool.",
        )

    detection = detect_parser(result.text, tool_path=tool_argv[-1], subcommand=subcommand)
    if not detection.ok:
        raise ScanFailure(CODE_UNKNOWN_FRAMEWORK, "could not identify the CLI framework")

    parser = get_parser(detection.kind)
    if parser is None:  # pragma: no cover
        raise ScanFailure(CODE_UNKNOWN_FRAMEWORK, f"no parser registered for {detection.kind!r}")

    # The subcommand's own page is parsed as a root page, then relabelled.
    spec = parser.parse(result.text, tool_name=name, tool_path=tool_argv[-1])
    parse_sub = getattr(parser, "parse_subcommand", None)
    if parse_sub is not None:
        command = parse_sub(
            result.text,
            tool_name=name,
            tool_path=tool_argv[-1],
            command_name=subcommand,
        )
        # Preserve any options/positionals the root parse found that the
        # subcommand parse did not (defensive; the two should agree).
        command.options = command.options or spec.commands[0].options
        command.positionals = command.positionals or spec.commands[0].positionals
        spec.commands = [command]
    spec.description = spec.commands[0].help or spec.description
    return ScanReport(spec=spec, detection=detection)

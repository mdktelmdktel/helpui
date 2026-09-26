"""Decide which parser a tool's help output needs.

Detection is *feature based* and runs on captured help text only (the optional
``tool_path`` is used solely as a tie-breaker for typer-without-rich, which
renders exactly like click).

Ordering rationale:

1. **typer** -- rich panels are unambiguous and would otherwise be mis-parsed.
2. **argparse** -- lower-case ``usage:`` plus lower-case section headers.
3. **click** -- capitalised ``Usage:``/``Options:``/``Commands:``.
4. **unknown** -- anything else; ``scan`` reports a clean error instead of
   inventing a form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from helpui.parsers.argparse_parser import ArgparseParser
from helpui.parsers.base import HelpParser
from helpui.parsers.click_parser import ClickParser
from helpui.parsers.typer_parser import TyperParser, is_typer_help

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

#: Parsers in detection priority order.
_PARSER_ORDER: tuple[HelpParser, ...] = (TyperParser(), ArgparseParser(), ClickParser())

#: Section headers that only argparse emits (lower case, plural).
_ARGPARSE_HEADERS = ("positional arguments:", "optional arguments:", "options:")

#: Section headers that only click/typer emit (capitalised).
_CLICK_HEADERS = ("Options:", "Commands:", "Arguments:")


@dataclass
class DetectResult:
    """Outcome of detection."""

    kind: str
    """One of ``argparse|click|typer|unknown``."""

    confidence: str
    """``high`` for a structural match, ``low`` for a heuristic guess."""

    reason: str
    """Human-readable explanation, surfaced by ``helpui scan`` diagnostics."""

    @property
    def ok(self) -> bool:
        return self.kind != "unknown"


def looks_like_help(text: str) -> bool:
    """True when ``text`` contains a usage line at all.

    Used by ``scan`` to distinguish "this tool does not support --help" from
    "we cannot parse this help", which get different error messages.
    """
    if not text.strip():
        return False
    return re.search(r"^\s*usage:\s", text, re.IGNORECASE | re.MULTILINE) is not None


def detect_parser(
    help_text: str,
    *,
    tool_path: str | None = None,
    subcommand: str | None = None,
) -> DetectResult:
    """Pick a parser for ``help_text``.

    Returns :class:`DetectResult` with ``kind="unknown"`` when nothing matches;
    callers must handle that without crashing.
    """
    if not help_text.strip():
        return DetectResult("unknown", "high", "no help output captured")

    if is_typer_help(help_text):
        return DetectResult("typer", "high", "typer rich table panels detected")

    lowered = help_text.lower()
    has_argparse_header = any(header in lowered for header in _ARGPARSE_HEADERS)
    has_click_header = any(header in help_text for header in _CLICK_HEADERS)
    has_lower_usage = re.search(r"^\s*usage:\s", help_text, re.MULTILINE) is not None
    has_upper_usage = re.search(r"^\s*Usage:\s", help_text, re.MULTILINE) is not None

    # argparse: lower-case "usage:" and lower-case headers, and never the
    # capitalised click headers.
    if has_lower_usage and has_argparse_header and not has_click_header:
        return DetectResult("argparse", "high", "argparse section headers detected")

    # click: capitalised "Usage:" plus capitalised headers.
    if has_upper_usage and has_click_header:
        if _source_imports_typer(tool_path):
            return DetectResult(
                "typer",
                "low",
                "click-shaped output but the target script imports typer",
            )
        return DetectResult("click", "high", "click section headers detected")

    # A bare usage line with no sections at all: argparse prints this for a
    # parser with only positionals; click always prints at least "Options:".
    if has_lower_usage and not has_upper_usage:
        return DetectResult("argparse", "low", "lower-case usage line without section headers")

    if has_upper_usage:
        return DetectResult("click", "low", "capitalised usage line without section headers")

    # Last resort: ask each parser whether it feels at home.
    for parser in _PARSER_ORDER:
        if parser.kind != "unknown" and parser.matches(help_text):
            return DetectResult(parser.kind, "low", f"{parser.kind} heuristics matched")

    return DetectResult("unknown", "high", "help output matches no supported CLI framework")


def get_parser(kind: str) -> HelpParser | None:
    """Return the parser for ``kind``, or ``None`` when unknown/unsupported."""
    for parser in _PARSER_ORDER:
        if parser.kind == kind:
            return parser
    return None


def _source_imports_typer(tool_path: str | None) -> bool:
    """Tie-breaker for typer without rich: sniff the target script's imports.

    Only applied to an existing ``.py`` file, and only ever as a *tie-breaker*
    after the text-level tests already said "click-shaped".
    """
    if not tool_path:
        return False
    path = Path(tool_path)
    if path.suffix not in {".py", ".pyw"} or not path.is_file():
        return False
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - unreadable file
        return False
    return bool(re.search(r"^\s*(?:import\s+typer|from\s+typer\s+import)\b", source, re.MULTILINE))

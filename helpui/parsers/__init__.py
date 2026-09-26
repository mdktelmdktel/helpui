"""Help-text parsers.

Import order matters only for readability; :mod:`helpui.parsers.detector`
imports each parser lazily through :data:`PARSERS`.
"""

from __future__ import annotations

from helpui.parsers.argparse_parser import ArgparseParser
from helpui.parsers.base import HelpParser
from helpui.parsers.click_parser import ClickParser
from helpui.parsers.detector import DetectResult, detect_parser, get_parser
from helpui.parsers.typer_parser import TyperParser

#: kind -> parser instance, for direct lookup without detection.
PARSERS: dict[str, HelpParser] = {
    "argparse": ArgparseParser(),
    "click": ClickParser(),
    "typer": TyperParser(),
}

__all__ = [
    "PARSERS",
    "ArgparseParser",
    "ClickParser",
    "DetectResult",
    "HelpParser",
    "TyperParser",
    "detect_parser",
    "get_parser",
]

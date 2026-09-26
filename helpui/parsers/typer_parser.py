"""Parser for ``typer`` generated help output.

Typer renders through click, so argument semantics are identical to click and
this module **reuses** :class:`~helpui.parsers.click_parser.ClickParser`.  The
only extra work is undoing the rich table layout that typer uses when ``rich``
is installed::

    +- Options -------------------------------------------------------------------------------------+
    | --output    -o      <str>           Output path. [default: out.txt]                             |
    | --format            <json|csv|tsv>  Output format. [default: json]                              |
    +-------------------------------------------------------------------------------------------------+
    +- Arguments -------------------------------------------------------------------------------------+
    | *    input_file      <path>  File to convert. [required]                                        |
    +-------------------------------------------------------------------------------------------------+

Normalisation turns each panel into the click-equivalent section::

    Options:
      --output    -o      <str>  Output path. [default: out.txt]
      --format            <json|csv|tsv>  Output format. [default: json]

which the click parser then consumes unchanged.
"""

from __future__ import annotations

import re

from helpui.model import CLICommand, CLISpec
from helpui.parsers.base import first_paragraph
from helpui.parsers.click_parser import ClickParser

#: ``+- Options ------+`` (rich draws with ASCII dashes when colour is off).
_PANEL_HEADER_RE = re.compile(r"^\s*\+-{1,}\s*(?P<title>[A-Za-z][A-Za-z ]*?)\s*-{2,}\+\s*$")

#: ``┌─ Options ─────────────────┐`` (rich's box-drawing rendering, which is
#: what typer emits in practice -- verified against typer 0.27 + rich).
_BOX_HEADER_RE = re.compile(r"^\s*[┌╭]\s*[─━\s]*(?P<title>[A-Za-z][A-Za-z ]*?)\s*[─━]{2,}\s*[┐╮]\s*$")

#: A rich table body row: ``│ content │``, possibly with padding.
_BOX_ROW_RE = re.compile(r"^\s*[│|]\s?(?P<content>.*?)\s*[│|]\s*$")

#: A box-drawing footer: ``└──────────────────┘``.
_BOX_BORDER_RE = re.compile(r"^\s*[└╰][─━\s]*[┘╯]\s*$")

#: A rich table body row: ``| content |``, possibly with a trailing space.
_PANEL_ROW_RE = re.compile(r"^\s*\|\s?(?P<content>.*?)\s*\|\s*$")

#: A rich table border row (``+-----+``).
_PANEL_BORDER_RE = re.compile(r"^\s*\+-{2,}\+\s*$")

#: A rich "empty" row inside a panel.
_PANEL_BLANK_RE = re.compile(r"^\s*\|\s*\|\s*$")
_BOX_BLANK_RE = re.compile(r"^\s*[│|]\s*[│|]\s*$")

#: Usage line inside rich output is `` Usage: ... `` with padding.
_USAGE_RE = re.compile(r"^\s*Usage:\s*(?P<rest>.+?)\s*$")

#: typer/rich marks required arguments with a leading ``*`` cell.
_REQUIRED_CELL_RE = re.compile(r"^\*\s*")

#: typer section titles that map onto click's section names.
_SECTION_ALIASES: dict[str, str] = {
    "options": "Options",
    "arguments": "Arguments",
    "commands": "Commands",
}


class TyperParser(ClickParser):
    """Parse typer help by normalising rich panels then delegating to click."""

    kind = "typer"

    def matches(self, help_text: str) -> bool:
        return is_typer_help(help_text)

    def _normalise(self, help_text: str) -> str:
        return normalise_rich_help(help_text)

    def parse(self, help_text: str, *, tool_name: str, tool_path: str) -> CLISpec:
        spec = super().parse(help_text, tool_name=tool_name, tool_path=tool_path)
        spec.parser_kind = self.kind
        # The base parser derives the description from the *raw* text, which for
        # typer means rich panel rows; re-derive it from the normalised form.
        spec.description = first_paragraph(normalise_rich_help(help_text))
        root = spec.root_command
        if root is not None and not root.help:
            root.help = spec.description
        return spec

    def parse_subcommand(
        self, help_text: str, *, tool_name: str, tool_path: str, command_name: str
    ) -> CLICommand:
        command = super().parse_subcommand(
            help_text, tool_name=tool_name, tool_path=tool_path, command_name=command_name
        )
        # Same reasoning as `parse`: the description must come from the
        # normalised text, not from rich's panel rows.
        normalised = normalise_rich_help(help_text)
        command.help = self._description(normalised) or command.help
        return command


def is_typer_help(help_text: str) -> bool:
    """Detect typer's rich rendering from its panel borders.

    Typer draws panels with ASCII ``+-`` borders when colour is disabled and
    with Unicode box-drawing characters otherwise, so both forms are checked.

    Typer *without* ``rich`` installed prints plain click output and is
    therefore indistinguishable from click at the text level; the detector
    falls back to source sniffing for that case (see
    :mod:`helpui.parsers.detector`).
    """
    for line in help_text.split("\n"):
        if _PANEL_HEADER_RE.match(line) or _BOX_HEADER_RE.match(line):
            return True
    return False


def normalise_rich_help(help_text: str) -> str:
    """Convert typer's rich panels into click-shaped plain text.

    Both renderings are handled:

    * box-drawing (``┌─ Options ────┐`` / ``│ --output  <str>  Help. │``)
    * ASCII (``+- Options ---+`` / ``| --output  <str>  Help. |``)

    Lines outside panels (the usage line and the description) are preserved
    minus rich's padding.  Section titles become click section headers so the
    click parser can consume the result unchanged.
    """
    out: list[str] = []
    for raw in help_text.replace("\r\n", "\n").split("\n"):
        header = _PANEL_HEADER_RE.match(raw) or _BOX_HEADER_RE.match(raw)
        if header is not None:
            title = header.group("title").strip().lower()
            out.append(f"{_SECTION_ALIASES.get(title, title.capitalize())}:")
            continue
        if _PANEL_BORDER_RE.match(raw) or _BOX_BORDER_RE.match(raw):
            continue
        if _PANEL_BLANK_RE.match(raw) or _BOX_BLANK_RE.match(raw):
            continue
        row = _PANEL_ROW_RE.match(raw) or _BOX_ROW_RE.match(raw)
        if row is not None:
            content = row.group("content").strip()
            if content:
                out.append(f"  {content}")
            continue
        if raw.strip():
            out.append(raw.strip())
    return "\n".join(out)


def normalize_typer_arguments(text: str) -> str:
    """Strip typer's ``*`` required marker so click's row parser sees a name.

    Kept as a public helper for tests; :meth:`ClickParser._parse_row` already
    handles the marker, this exists so callers can pre-process text themselves.
    """
    return "\n".join(_REQUIRED_CELL_RE.sub("", line) if _PANEL_ROW_RE.match(line) else line for line in text.split("\n"))

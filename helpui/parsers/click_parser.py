"""Parser for ``click`` generated help output.

Click's layout differs from argparse in three ways the parser depends on:

* section headers are capitalised (``Options:``, ``Commands:``, ``Arguments:``);
* each definition is a *single* logical line whose help text is separated from
  the flags by two or more spaces, with click-appended annotations at the end
  (``[default: x]``, ``[required]``, ``[env var: X]``, ``[a|b|c]``);
* option types appear as uppercase metavars (``TEXT``, ``INTEGER``, ``FLOAT``,
  ``PATH``, ``DIRECTORY``, ``FILE``, ``CHOICE``) or, for choices, as a
  bracketed ``[json|csv|tsv]`` list.

Verified against click 8.5 output (see ``tests/fixtures/sample_click.py``).
``typer`` renders through click and reuses this class, adding a rich-panel
pre-processing step in :mod:`helpui.parsers.typer_parser`.
"""

from __future__ import annotations

import re

from helpui.model import CLICommand, CLIOption, CLISpec
from helpui.parsers.base import (
    HelpParser,
    derive_dest,
    find_env_vars,
    flatten,
    infer_type,
    parse_usage_line,
    split_sections,
    split_usage_prog,
    unsplit,
)

#: Sections holding options / arguments / subcommands in click help.
OPTION_SECTIONS = ("options",)
ARGUMENT_SECTIONS = ("arguments",)
COMMAND_SECTIONS = ("commands",)

#: Separator between the flags column and the help column (click pads to align).
_COLUMN_GAP_RE = re.compile(r"\s{2,}")

#: click's bracketed choice rendering, e.g. ``[json|csv|tsv]``.
_BRACKET_CHOICE_RE = re.compile(r"\[(?P<values>[A-Za-z0-9_.-]+(?:\|[A-Za-z0-9_.-]+)+)\]")

#: click/typer's angle-bracket type rendering, e.g. ``<json|csv|tsv>`` or ``<int>``.
_ANGLE_TYPE_RE = re.compile(r"<(?P<values>[^<>]+)>")

#: click appends ``[required]`` / ``[default: x]`` / ``[env var: X]`` at the end.
_CLICK_ANNOTATION_RE = re.compile(r"\s*\[(?:default|required|env var|possible values|choices)[^\]]*\]")

#: ``*  input_file`` marks a required argument in typer's rich layout.
#: Defined here as a constant so the click parser can also tolerate it.
REQUIRED_MARKER = "*"

#: Options we never render in a generated form.
_HIDDEN_FLAGS = frozenset({"-h", "--help", "--install-completion", "--show-completion"})

#: A middle column is a *type* cell only when it looks like one.  Typer renders
#: ``<str>``, ``<int>``, ``<json|csv|tsv>``; click renders bare metavars such as
#: ``TEXT`` or ``[a|b]``.  Anything else is part of the help prose.
_LOOKS_LIKE_TYPE_CELL = re.compile(
    r"^(?:<[^<>]+>|\[[A-Za-z0-9_.|-]+\]|[A-Z][A-Z_]{1,12})$"
)

#: Placeholder metavars click prints in the usage line; they are structural,
#: not arguments the user fills in.
_PLACEHOLDER_METAVARS = frozenset({"OPTIONS", "ARGS", "COMMAND", "COMMANDS", "...", "[ARGS]...", "[OPTIONS]"})

#: click metavar -> HelpUI type, applied before prose sniffing.
_METAVAR_TYPES: dict[str, str] = {
    "TEXT": "str",
    "STRING": "str",
    "INTEGER": "int",
    "INT": "int",
    "FLOAT": "float",
    "NUMBER": "float",
    "PATH": "file",
    "FILE": "file",
    "FILENAME": "file",
    "DIRECTORY": "dir",
    "DIR": "dir",
    "CHOICE": "choice",
    "BOOLEAN": "bool",
    "FLAG": "bool",
}


class ClickParser(HelpParser):
    """Turn click help text into a :class:`~helpui.model.CLISpec`."""

    kind = "click"

    def matches(self, help_text: str) -> bool:
        if "Options:" not in help_text and "Commands:" not in help_text:
            return False
        if re.search(r"^\s*Usage:\s", help_text, re.MULTILINE) is None:
            return False
        # click capitalises both header and "Usage:"; argparse lower-cases them.
        return "usage:" not in help_text.lower() or "Usage:" in help_text

    # -- public API -----------------------------------------------------------

    def parse(self, help_text: str, *, tool_name: str, tool_path: str) -> CLISpec:
        spec = self.empty_spec(help_text, tool_name, tool_path, self.kind)
        root = spec.root_command or CLICommand(name="")
        root.help = flatten(root.help) or spec.description

        _, sections = split_sections(self._normalise(help_text))

        root.options = [
            opt for opt in self._parse_definitions(sections.get("options", [])) if not self._is_hidden(opt)
        ]

        for section in ARGUMENT_SECTIONS:
            root.positionals.extend(self._parse_definitions(sections.get(section, []), positional=True))

        for name in self._command_names(help_text):
            spec.commands.append(CLICommand(name=name, help=""))

        # Click's root help lists commands under "Commands:". A *subcommand's*
        # help is parsed by calling the parser on that subcommand's own text, so
        # here we only need the names plus their one-line descriptions.
        for name, text in self._command_descriptions(sections.get("commands", [])).items():
            existing = spec.command(name)
            if existing is not None:
                existing.help = text
            else:
                spec.commands.append(CLICommand(name=name, help=text))

        return spec

    def parse_subcommand(
        self, help_text: str, *, tool_name: str, tool_path: str, command_name: str
    ) -> CLICommand:
        """Parse a subcommand's help page into a fully-populated CLICommand."""
        _, sections = split_sections(self._normalise(help_text))
        options = [
            opt for opt in self._parse_definitions(sections.get("options", [])) if not self._is_hidden(opt)
        ]
        positionals: list[CLIOption] = []
        for section in ARGUMENT_SECTIONS:
            positionals.extend(self._parse_definitions(sections.get(section, []), positional=True))
        if not positionals:
            positionals = self._positionals_from_usage(help_text, command_path=command_name)
        return CLICommand(
            name=command_name,
            help=flatten(self._description(help_text)),
            options=options,
            positionals=positionals,
            subcommands=sorted(self._command_descriptions(sections.get("commands", []))),
        )

    # -- internals ------------------------------------------------------------

    def _normalise(self, help_text: str) -> str:
        """Hook for subclasses (typer strips rich panels here)."""
        return help_text

    def _is_hidden(self, option: CLIOption) -> bool:
        return any(alias in _HIDDEN_FLAGS for alias in option.aliases)

    def _command_names(self, help_text: str) -> list[str]:
        """Subcommand names declared in the usage line, if any."""
        for line in help_text.split("\n"):
            parsed = parse_usage_line(line)
            if parsed:
                _, rest = split_usage_prog(parsed)
                return self._names_from_usage(rest)
        return []

    def _names_from_usage(self, usage_rest: str) -> list[str]:
        for token in usage_rest.split():
            if token.startswith("{"):
                continue
        if "COMMAND" not in usage_rest and "[ARGS]" not in usage_rest:
            return []
        return []

    def _command_descriptions(self, lines: list[str]) -> dict[str, str]:
        """Map ``Commands:`` entries to their one-line descriptions."""
        descriptions: dict[str, str] = {}
        for raw in lines:
            if not raw.strip():
                continue
            parts = _COLUMN_GAP_RE.split(raw.strip(), maxsplit=1)
            name = parts[0].strip()
            if not name:
                continue
            descriptions[name] = parts[1].strip() if len(parts) > 1 else ""
        return descriptions

    def _description(self, help_text: str) -> str:
        """The indented prose block between the usage line and the first section."""
        preamble, _ = split_sections(self._normalise(help_text))
        prose = [ln.strip() for ln in preamble[1:] if ln.strip() and not ln.strip().lower().startswith("usage:")]
        return unsplit("\n".join(prose))

    def _positionals_from_usage(self, help_text: str, *, command_path: str = "") -> list[CLIOption]:
        """Derive positionals from ``Usage: tool cmd [OPTIONS] INPUT_FILE``.

        ``command_path`` is the (possibly nested) command name as invoked
        (``"admin reset"``); those tokens appear in the usage line but are part
        of the invocation, not arguments the user fills in.
        """
        positionals: list[CLIOption] = []
        skip_tokens = command_path.split()
        for line in help_text.split("\n"):
            parsed = parse_usage_line(line)
            if not parsed:
                continue
            _, rest = split_usage_prog(parsed)
            tokens = rest.split()
            index = 0
            # Skip the program's own command path, e.g. `sample_typer admin reset`.
            while index < len(tokens) and tokens[index] in skip_tokens:
                index += 1
            # A command name that is not part of the path (e.g. the group name)
            # is also not a user argument; usage lines list it first.
            if index < len(tokens) and not tokens[index].startswith(("[", "-")) and command_path == "":
                index += 1
            while index < len(tokens):
                token = tokens[index]
                # Normalise a usage token to its bare name: `[ARGS]...`,
                # `[OPTIONS]`, `INPUT_FILE`, `[FILE]...` -> `ARGS`, `OPTIONS`,
                # `INPUT_FILE`, `FILE`; `multiple` records the trailing `...`.
                bare = _bare_usage_token(token)
                multiple = bare.endswith("...") or token.endswith("...")
                name = bare.rstrip(".")
                optional = token.startswith("[")
                if not name or name.startswith("-") or name in _PLACEHOLDER_METAVARS:
                    index += 1
                    continue
                positionals.append(
                    CLIOption(
                        name=name,
                        dest=derive_dest(name),
                        type=infer_type(name, ""),
                        required=not optional,
                        help="",
                        multiple=multiple,
                        value_name=name,
                    )
                )
                index += 1
            break
        return positionals

    def _parse_definitions(self, lines: list[str], *, positional: bool = False) -> list[CLIOption]:
        """Parse click definition rows into options.

        Every row is ``<flags>  <help>``; continuation lines are indented past
        the help column and are joined back into the previous row's help text.
        """
        options: list[CLIOption] = []
        for row in self._join_rows(lines):
            option = self._parse_row(row, positional=positional)
            if option is not None:
                options.append(option)
        return options

    @staticmethod
    def _join_rows(lines: list[str]) -> list[str]:
        """Join wrapped help text back onto its defining row.

        Click aligns the help column; a wrapped continuation line is indented at
        least as far as that column (typer panels can be wider still).  We treat
        a line as a continuation when it does not start a new definition, i.e.
        when it does not begin with ``-``/``*`` at the row indent.
        """
        rows: list[str] = []
        for raw in lines:
            if not raw.strip():
                continue
            stripped = raw.strip()
            starts_new = stripped.startswith(("-", "*"))
            if rows and not starts_new:
                rows[-1] = f"{rows[-1]} {stripped}"
            else:
                rows.append(raw.rstrip())
        return rows

    def _parse_row(self, row: str, *, positional: bool) -> CLIOption | None:
        indent = len(row) - len(row.lstrip(" "))
        content = row.strip()
        if not content:
            return None

        required_marker = False
        if content.startswith(REQUIRED_MARKER):
            required_marker = True
            content = content[len(REQUIRED_MARKER) :].strip()

        if positional or not content.startswith("-"):
            if positional:
                return self._parse_positional_row(content, required_marker=required_marker)
            # Not an option and not a positional section: ignore.
            return None

        return self._parse_option_row(content, indent=indent)

    def _parse_positional_row(self, content: str, *, required_marker: bool) -> CLIOption:
        parts = _COLUMN_GAP_RE.split(content, maxsplit=2)
        name = parts[0].strip()
        # typer prints the type in a middle column: `input_file  <path>  Help.`
        value_name = name
        help_text = ""
        if len(parts) == 3:
            type_cell, help_text = parts[1].strip(), parts[2].strip()
            value_name = name
            type_hint = type_cell
        elif len(parts) == 2:
            help_text = parts[1].strip()
            type_hint = ""
        else:
            type_hint = ""

        choices = self._choices_from_type(type_hint)
        option_type = self._type_from_angle(type_hint, choices) or infer_type(
            "" if choices else name, help_text, choices=choices
        )
        return CLIOption(
            name=name,
            dest=derive_dest(name),
            type=option_type,
            required=required_marker or infer_required(help_text) or True,
            default=None,
            choices=choices,
            help=help_text,
            is_flag=False,
            multiple=name.endswith("...") or "multiple" in help_text.lower(),
            value_name=value_name,
            aliases=[name],
        )

    def _parse_option_row(self, content: str, *, indent: int) -> CLIOption | None:
        flags_blob, help_text, type_cell = self._split_row(content)
        alias_tokens: list[str] = []
        metavar = ""
        for token in flags_blob.split():
            if token.startswith("-"):
                alias_tokens.append(token.rstrip(","))
            elif not metavar:
                metavar = token
        if not alias_tokens:
            return None

        long_names = [a for a in alias_tokens if a.startswith("--")]
        primary = long_names[0] if long_names else alias_tokens[0]

        # Typer's rich layout puts the type in its own middle column as
        # `<str>` / `<json|csv|tsv>`; click puts it inline in the flags column
        # (`--format [json|csv|tsv]`) or nowhere at all.
        type_source = type_cell or metavar

        choices = (
            self._angle_choices(type_source)
            or self._choices_from_type(type_source)
            or self._bracket_choices(metavar)
            or self._bracket_choices(content)
        )
        if choices:
            metavar = ""

        is_flag = metavar == "" and not type_cell
        option_type = (
            self._type_from_angle(type_source, choices)
            or ("bool" if is_flag else "")
            or _METAVAR_TYPES.get(metavar.upper(), "")
            or infer_type(metavar, _prose_only(help_text), choices=choices)
        )

        default = extract_default(help_text)
        if is_flag and default is not None:
            default = default.lower() if default.lower() in {"true", "false"} else default

        multiple = bool(re.search(r"\bmultiple\b|repeatable|may be repeated", help_text, re.IGNORECASE))
        required = infer_required(help_text)

        # typer renders required options with a leading `*` in the flags cell.
        if content.strip().startswith(REQUIRED_MARKER):
            required = True

        return CLIOption(
            name=primary,
            dest=derive_dest(primary),
            type=option_type,
            required=required,
            default=default,
            choices=choices,
            help=help_text,
            is_flag=is_flag,
            multiple=multiple,
            value_name="" if is_flag else metavar,
            aliases=alias_tokens,
        )

    @staticmethod
    def _split_row(content: str) -> tuple[str, str, str]:
        """Split a definition row into ``(flags, help, type_cell)``.

        Handles both layouts::

            click:  -o, --output TEXT        Output path.  [default: out.txt]
            typer:  --output    -o    <str>  Output path. [default: out.txt]
                    --format          <json|csv|tsv>  Output format. [default: json]

        The typer form splits the *flags cell* itself across columns: the long
        spelling, then an optional short spelling, then the type.  So we scan
        leading cells while they look like flags or types, and treat the first
        cell that looks like neither as the start of the help prose.
        """
        cells = _COLUMN_GAP_RE.split(content.strip())
        flags: list[str] = []
        type_cell = ""
        index = 0
        while index < len(cells):
            cell = cells[index].strip()
            if not type_cell and _LOOKS_LIKE_TYPE_CELL.match(cell):
                type_cell = cell
                index += 1
                continue
            if all(_is_flag_token(tok) for tok in cell.split()) and cell.split():
                flags.append(cell)
                index += 1
                continue
            break
        if not flags:
            # Fall back to the click shape: everything up to the first gap is
            # the flags cell, even if it contains an unrecognised metavar.
            return cells[0] if cells else "", " ".join(cells[1:]).strip(), ""
        return " ".join(flags), " ".join(cells[index:]).strip(), type_cell

    @staticmethod
    def _choices_from_type(type_cell: str) -> list[str] | None:
        """``[json|csv|tsv]`` -> ``["json", "csv", "tsv"]``."""
        match = _BRACKET_CHOICE_RE.search(type_cell)
        if match is None:
            return None
        values = [v.strip() for v in match.group("values").split("|") if v.strip()]
        return values or None

    @staticmethod
    def _angle_choices(type_cell: str) -> list[str] | None:
        """typer's ``<json|csv|tsv>`` choice rendering."""
        match = _ANGLE_TYPE_RE.search(type_cell)
        if match is None:
            return None
        inner = match.group("values").strip()
        if "|" not in inner:
            return None
        values = [v.strip() for v in inner.split("|") if v.strip()]
        return values or None

    @staticmethod
    def _type_from_angle(type_cell: str, choices: list[str] | None) -> str:
        """Map typer's ``<int>`` / ``<path>`` / ``<float>`` cells to a type."""
        if choices:
            return "choice"
        match = _ANGLE_TYPE_RE.search(type_cell)
        if match is None:
            return ""
        inner = match.group("values").strip().lower()
        if inner in {"int", "integer"}:
            return "int"
        if inner in {"float"}:
            return "float"
        if inner in {"bool", "boolean"}:
            return "bool"
        if inner in {"path", "file", "filename"}:
            return "file"
        if inner in {"dir", "directory"}:
            return "dir"
        if inner in {"str", "text", "string"}:
            return "str"
        return ""

    @staticmethod
    def _bracket_choices(help_text: str) -> list[str] | None:
        """``[json|csv]`` may also be rendered at the end of the help column."""
        match = _BRACKET_CHOICE_RE.search(help_text)
        if match is None:
            return None
        values = [v.strip() for v in match.group("values").split("|") if v.strip()]
        return values or None


def infer_required(help_text: str) -> bool:
    """True when click appended ``[required]`` to the help text."""
    return bool(re.search(r"\[required\]", help_text, re.IGNORECASE))


def extract_default(help_text: str) -> str | None:
    """Extract click/typer's ``[default: value]`` annotation.

    Returns ``None`` for values that carry no usable literal default
    (``[default: (dynamic)]``, ``[default: None]``, empty values).
    """
    match = re.search(r"\[default:\s*(?P<value>[^\]]*)\]", help_text, re.IGNORECASE)
    if match is None:
        return None
    value = match.group("value").strip().strip("'\"")
    if not value or value.lower() in {"none", "null", "(dynamic)", "dynamic", "-"}:
        return None
    return value


def strip_annotations(help_text: str) -> str:
    """Remove click's trailing annotations, keeping the human help sentence."""
    return _CLICK_ANNOTATION_RE.sub("", help_text).strip()


def _prose_only(help_text: str) -> str:
    """Help text without click's appended annotations.

    Type sniffing must not see ``[default: secret]`` or ``[env var: TOKEN]``,
    which would otherwise misclassify options as ``password``.
    """
    return _CLICK_ANNOTATION_RE.sub("", help_text).strip()


def _is_flag_token(token: str) -> bool:
    """True for a single ``-x`` / ``--xyz`` spelling (a trailing comma is fine)."""
    return re.fullmatch(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*,?", token) is not None


def _bare_usage_token(token: str) -> str:
    """Normalise a usage-line token to its bare argument name.

    Brackets and a trailing ellipsis are usage syntax, not part of the name:
    ``"[ARGS]..."`` -> ``"ARGS..."``, ``"[FILE]"`` -> ``"FILE"``.
    """
    return token.replace("[", "").replace("]", "").replace("(", "").replace(")", "").strip()


def env_vars(help_text: str) -> list[str]:
    """Public wrapper so tests can assert on ``[env var: X]`` handling."""
    return find_env_vars(help_text)

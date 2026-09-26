"""Parser for ``argparse`` generated help output.

Shape of the input (classic argparse layout)::

    usage: tool [-h] [--output OUTPUT] {json,csv} input_file

    Process an input file.

    positional arguments:
      input_file            file to process
      {json,csv}            output format

    options:
      -h, --help            show this help message and exit
      -o, --output OUTPUT   where to write the result (default: out.txt)
      -v, --verbose         increase verbosity

The parser is line/block oriented rather than regex-over-the-whole-document,
because argparse help is structured around an indent-based definition list.
"""

from __future__ import annotations

import re

from helpui.model import CLICommand, CLIOption, CLISpec
from helpui.parsers.base import (
    HelpParser,
    derive_dest,
    find_choices,
    find_choices_in_metavar,
    find_default,
    flatten,
    infer_type,
    parse_usage_line,
    split_sections,
    split_usage_prog,
    unsplit,
)

#: Keys argparse uses for the sections we care about.
OPTION_SECTIONS = ("options", "optional arguments")
POSITIONAL_SECTIONS = ("positional arguments", "arguments")
SUBCOMMAND_SECTIONS = ("commands", "subcommands")

#: ``{a,b,c}`` appearing as a pseudo-positional in the usage line.
_CHOICE_METAVAR_RE = re.compile(r"^\{[^{}]+\}$")

#: argparse's help flag, which we deliberately drop from generated forms.
_HELP_FLAGS = frozenset({"-h", "--help"})

#: Help strings that identify argparse's built-in no-value actions.
_FLAG_HELP_RE = re.compile(
    r"store_true|store_false|show this help message|increase verbosity|show program's version",
    re.IGNORECASE,
)

#: Metavars argparse uses for the subparser slot; the real names live one indent in.
_SUBPARSER_METAVARS = frozenset({"COMMAND", "COMMANDS", "{command}", "{commands}"})


class ArgparseParser(HelpParser):
    """Turn argparse help text into a :class:`~helpui.model.CLISpec`."""

    kind = "argparse"

    def matches(self, help_text: str) -> bool:
        lowered = help_text.lower()
        # argparse always emits a lower-case "usage:" and lower-case section
        # headers; click/typer capitalise "Options:", "Commands:".
        if "options:" not in lowered and "optional arguments:" not in lowered:
            return False
        if re.search(r"^\s*usage:\s", help_text, re.MULTILINE) is None:
            return False
        return "Options:" not in help_text and "Commands:" not in help_text

    def parse(self, help_text: str, *, tool_name: str, tool_path: str) -> CLISpec:
        spec = self.empty_spec(help_text, tool_name, tool_path, self.kind)
        preamble, sections = split_sections(help_text)

        _usage_prog, usage_rest = self._usage(help_text, fallback=tool_name)
        root = spec.root_command or CLICommand(name="")
        # The preamble opens with the usage line; the description is the prose
        # that follows it. `first_paragraph` already extracted the description.
        root.help = spec.description

        # -- positionals ------------------------------------------------------
        positional_lines: list[str] = []
        for key in POSITIONAL_SECTIONS:
            positional_lines.extend(sections.get(key, []))
        # Subcommands are rendered as a nested block under the COMMAND metavar;
        # pull them out before parsing the flat positional list.
        for command in self._subcommands_from_positionals(positional_lines):
            spec.commands.append(command)
        root.positionals = [
            p
            for p in self._parse_blocks(positional_lines, positional=True)
            if p.name.upper() not in _SUBPARSER_METAVARS and not _is_placeholder_name(p.name)
        ]

        # A `{a,b,c}` pseudo-positional in the usage line is a choice argument
        # even though argparse prints it under "positional arguments:" too; the
        # section parsing already covers it, so only fall back to the usage line
        # when the section yielded nothing user-fillable.
        if not root.positionals and not [c for c in spec.commands if c.name]:
            root.positionals = self._positionals_from_usage(usage_rest)

        # -- options ----------------------------------------------------------
        option_lines: list[str] = []
        for key in OPTION_SECTIONS:
            option_lines.extend(sections.get(key, []))
        root.options = [
            opt
            for opt in self._parse_blocks(option_lines, positional=False)
            if not _HELP_FLAGS.issuperset(set(opt.aliases))
        ]

        # -- subcommands ------------------------------------------------------
        sub_lines: list[str] = []
        for key in SUBCOMMAND_SECTIONS:
            sub_lines.extend(sections.get(key, []))
        for command in self._subcommands_from_section(sub_lines):
            spec.commands.append(command)

        # `{a,b}` in the usage line means argparse subparsers even when the tool
        # printed no nested block under a COMMAND metavar. Only consult it when
        # the section/block parsing found nothing: a `{json,csv,tsv}` choice
        # pseudo-positional in a *subcommand's* usage line is not a subcommand.
        if not [c for c in spec.commands if c.name] and not root.positionals:
            for name in self._subcommand_names_from_usage(usage_rest):
                spec.commands.append(CLICommand(name=name, help=""))

        if not spec.description:
            spec.description = flatten(" ".join(preamble[:3]))
        return spec

    def parse_subcommand(
        self, help_text: str, *, tool_name: str, tool_path: str, command_name: str
    ) -> CLICommand:
        """Parse a subcommand's own help page into a fully-populated command.

        ``sample_argparse convert --help`` has exactly the same shape as a root
        parser's help, so we parse it with the same code and then remove the
        artifacts of *being* a subcommand:

        * the ``COMMAND`` pseudo-positional, if this subparser has its own
          sub-subparsers;
        * the command name itself, which argparse echoes in the usage line
          (``usage: sample_argparse report [-h] ...``) but which the user does
          not type as an argument.
        """
        spec = self.parse(help_text, tool_name=tool_name, tool_path=tool_path)
        root = spec.root_command or CLICommand(name=command_name)
        aliases_to_drop = {command_name.lower(), command_name.replace(" ", "_").lower()}
        positionals = [
            p
            for p in root.positionals
            if p.name.upper() not in _SUBPARSER_METAVARS
            and not _is_placeholder_name(p.name)
            # Drop the echoed command name, e.g. `positional arguments: report`.
            and not (not p.help and p.dest.lower() in aliases_to_drop)
        ]
        return CLICommand(
            name=command_name,
            help=self._usage_description(help_text) or root.help,
            options=root.options,
            positionals=positionals,
            subcommands=[c.name for c in spec.commands if c.name],
        )

    def _usage_description(self, help_text: str) -> str:
        """Description prose printed between ``usage:`` and the first section.

        The preamble's first line is the ``usage: ...`` line, which may *wrap*
        onto continuation lines; every line that belongs to the usage block must
        be skipped or the command's description becomes a wall of metavars.
        """
        preamble, _ = split_sections(help_text)
        prose: list[str] = []
        in_usage = False
        for line in preamble:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("usage:"):
                in_usage = True
                continue
            if in_usage:
                # A wrapped usage line continues while it is indented relative
                # to `usage:`; the description starts at column 0.
                if line.startswith((" ", "\t")):
                    continue
                in_usage = False
            prose.append(stripped)
        return flatten(" ".join(prose[:3]))

    # -- helpers --------------------------------------------------------------

    def _usage(self, help_text: str, *, fallback: str) -> tuple[str, str]:
        for line in help_text.split("\n"):
            parsed = parse_usage_line(line)
            if parsed:
                prog, rest = split_usage_prog(parsed)
                return (prog or fallback), rest
        return fallback, ""

    def _parse_blocks(self, lines: list[str], *, positional: bool) -> list[CLIOption]:
        """Parse an argparse definition list into options.

        A definition starts at the section indent (2 spaces in argparse) and its
        help text continues on more deeply indented lines.
        """
        options: list[CLIOption] = []
        for head, body in _iterate_blocks(lines):
            option = self._parse_block(head, body, positional=positional)
            if option is not None:
                options.append(option)
        return options

    def _subcommands_from_positionals(self, lines: list[str]) -> list[CLICommand]:
        """Recover argparse subcommands from the nested ``COMMAND`` block.

        argparse renders subparsers as a pseudo-positional grouped by metavar::

            positional arguments:
              COMMAND
                convert          Convert an input file to another format.
                report           Generate a summary report.

        The nested, more-indented rows are the actual subcommand names.
        """
        commands: list[CLICommand] = []
        for head, body in _iterate_blocks(lines):
            name = head.strip().split()[0] if head.strip() else ""
            if name.upper() not in _SUBPARSER_METAVARS:
                continue
            for nested in body:
                stripped = nested.strip()
                if not stripped:
                    continue
                parts = re.split(r"\s{2,}", stripped, maxsplit=1)
                sub_name = parts[0].strip()
                if not sub_name or sub_name.startswith("-"):
                    continue
                commands.append(
                    CLICommand(name=sub_name, help=parts[1].strip() if len(parts) > 1 else "")
                )
        return commands

    def _parse_block(self, head: str, body: list[str], *, positional: bool) -> CLIOption | None:
        stripped = head.strip()
        if not stripped:
            return None
        # Keep the prose column separate from the flags column: for options the
        # help text may start on the same physical line.
        help_text = unsplit("\n".join(body))
        columns = re.split(r"\s{2,}", stripped, maxsplit=1)
        inline_help = columns[1].strip() if len(columns) > 1 else ""
        if inline_help:
            help_text = f"{inline_help} {help_text}".strip()

        if positional:
            # `input_file` or `{json,csv}` optionally followed by an indented
            # continuation describing extra positionals of the same metavar.
            name = columns[0].strip().split()[0] if columns[0].strip() else ""
            if not name or name in {"...", ".."} or set(name) <= {".", "["}:
                return None
            choices = find_choices_in_metavar(name) or find_choices(help_text)
            return CLIOption(
                name=name,
                dest=derive_dest(name),
                type=infer_type("" if _CHOICE_METAVAR_RE.match(name) else name, help_text, choices=choices),
                required=True,  # argparse positionals are required by definition
                default=None,
                choices=choices,
                help=help_text,
                is_flag=False,
                multiple=name.endswith("...") or "..." in name,
                value_name=name,
                aliases=[name],
            )

        spellings, metavar = _split_flags_and_metavar(stripped)
        if not spellings:
            return None

        long_names = [s for s in spellings if s.startswith("--")]
        primary = long_names[0] if long_names else spellings[0]

        # A flag takes no value: either the definition ended with the flags, or
        # argparse's own help strings name the store_true/store_false actions.
        is_flag = metavar == "" or _FLAG_HELP_RE.search(help_text) is not None

        choices = find_choices_in_metavar(metavar) or find_choices(help_text)
        if metavar and metavar.startswith("{"):
            metavar = ""
        default = find_default(help_text)
        if is_flag:
            # `store_true`/`store_false` switches have an implicit boolean
            # default; only honour an explicit `default: True/False`.
            match = re.search(r"default:\s*(true|false)", help_text, re.IGNORECASE)
            default = match.group(1).lower() if match is not None else "false"
        elif default is None and metavar:
            default = None

        option_type = "bool" if is_flag else infer_type(metavar, help_text, choices=choices)
        multiple = _is_multiple(metavar, head, help_text)

        return CLIOption(
            name=primary,
            dest=derive_dest(primary),
            type=option_type,
            required=bool(re.search(r"\brequired\b(?!:)", help_text, re.IGNORECASE)),
            default=default,
            choices=choices,
            help=help_text,
            is_flag=is_flag,
            multiple=multiple,
            value_name="" if is_flag else metavar,
            aliases=spellings,
        )

    def _positionals_from_usage(self, usage_rest: str) -> list[CLIOption]:
        """Fallback: derive positionals from the usage line only.

        Used when a tool prints a ``usage:`` line but no ``positional
        arguments:`` section (argparse does this when the argument has no help
        text). ``[--opt X]`` groups and option spellings are skipped.
        """
        result: list[CLIOption] = []
        cleaned = usage_rest.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ")
        skipping_optional = False
        for token in cleaned.split():
            if token.startswith("-"):
                skipping_optional = True  # its metavar belongs to the option
                continue
            if skipping_optional:
                skipping_optional = False
                continue
            if _is_placeholder_name(token) or token.upper() in _SUBPARSER_METAVARS:
                continue
            if _CHOICE_METAVAR_RE.match(token):
                choices = find_choices_in_metavar(token) or []
                result.append(
                    CLIOption(
                        name=token,
                        dest="format",
                        type="choice",
                        required=True,
                        choices=choices,
                        help="",
                        value_name=token,
                    )
                )
                continue
            result.append(
                CLIOption(
                    name=token,
                    dest=derive_dest(token),
                    type=infer_type(token, ""),
                    required=True,
                    help="",
                    value_name=token,
                )
            )
        return result

    def _subcommands_from_section(self, lines: list[str]) -> list[CLICommand]:
        commands: list[CLICommand] = []
        for head, body in _iterate_blocks(lines):
            name = head.strip().split()[0] if head.strip() else ""
            if not name or name.startswith("-"):
                continue
            commands.append(CLICommand(name=name, help=unsplit("\n".join(body))))
        return commands

    def _subcommand_names_from_usage(self, usage_rest: str) -> list[str]:
        for token in usage_rest.split():
            bare = token.strip("[]()")
            if bare.startswith("{") and bare.endswith("}"):
                return [n.strip() for n in bare[1:-1].split(",") if n.strip()]
        return []


def _is_metavar(token: str) -> bool:
    """True for the right-hand side of an option definition (``FILE``, ``{a,b}``)."""
    if token.startswith("-"):
        return False
    return token.strip(",") not in {"", "..."}


def _split_flags_and_metavar(stripped: str) -> tuple[list[str], str]:
    """Split ``"-o, --output FILE"`` into ``(["-o", "--output"], "FILE")``.

    argparse separates the flags column from the help column with **two or more
    spaces** (it pads to an aligned column), while individual spellings are
    separated by a comma or a single space.  Splitting on the first multi-space
    run therefore yields the flags + metavar, and everything after it is help
    prose -- which is what keeps ``-v, --verbose       Enable verbose logging``
    from being read as ``metavar="Enable verbose logging"``.
    """
    flags_part = re.split(r"\s{2,}", stripped, maxsplit=1)[0].strip()
    tokens = flags_part.split()
    spellings: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") and not _is_negative_number(token):
            spellings.append(token.rstrip(","))
            index += 1
            continue
        break
    metavar = " ".join(tokens[index:]).strip()
    # A dangling separator left behind by the flag list carries no metavar.
    if metavar.strip(", ").strip() in {"", "..."}:
        metavar = ""
    return spellings, metavar


def _is_negative_number(token: str) -> bool:
    """``-1`` / ``-2.5`` are metavars, not option spellings."""
    return re.fullmatch(r"-\d+(?:\.\d+)?", token) is not None


def _is_placeholder_name(name: str) -> bool:
    """True for argparse usage placeholders that are not user-fillable args."""
    return name.strip(".").upper() in _SUBPARSER_METAVARS or set(name) <= {".", "["}


#: Prose that indicates an option may be given more than once.
_MULTIPLE_RE = re.compile(
    r"\b(?:multiple|repeatable|may be repeated|can be repeated|append|one or more)\b",
    re.IGNORECASE,
)


def _is_multiple(metavar: str, head: str, help_text: str) -> bool:
    """True when the option accepts several values or may be repeated.

    argparse signals this three different ways:

    * ``nargs="+"`` / ``"*"`` render a trailing ``...`` in the usage line;
    * ``action="append"`` renders nothing special, so only the help prose
      (``"Tag to attach (repeatable)"``) reveals it;
    * a help string mentioning "multiple"/"one or more".
    """
    if "..." in metavar or metavar.endswith("+"):
        return True
    # `--tag TAG [TAG ...]` in the usage line means a variadic option.
    if re.search(r"\[[^\]]*\.\.\.\]", head):
        return True
    return _MULTIPLE_RE.search(help_text) is not None


def _looks_like_flag_only(stripped: str, spellings: list[str]) -> bool:
    """``"-h, --help  show this help..."`` has no metavar after the flags."""
    remainder = stripped
    for spelling in spellings:
        remainder = remainder.replace(spelling, "", 1)
    return remainder.strip().strip(",").strip() == ""


def _iterate_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group a definition list into ``(head_line, continuation_lines)`` pairs.

    argparse indents definitions by 2 spaces relative to the section header and
    wraps help text to a 24-column-wide help position; a deeper indent than the
    first head line means "continuation".
    """
    blocks: list[tuple[str, list[str]]] = []
    head: str | None = None
    body: list[str] = []
    head_indent: int | None = None

    for raw in lines:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if head is None:
            head, head_indent, body = raw, indent, []
            continue
        if indent <= (head_indent or 0):
            blocks.append((head, body))
            head, head_indent, body = raw, indent, []
        else:
            body.append(raw)
    if head is not None:
        blocks.append((head, body))
    return blocks

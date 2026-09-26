"""Parser interface plus the text utilities shared by all parsers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from helpui.model import CLISpec

#: Programs that may prefix a ``usage:`` line, e.g. ``usage: tool [-h]``.
_USAGE_RE = re.compile(r"^\s*usage:\s*(?P<rest>.*)$", re.IGNORECASE)

#: An argparse section header: ``positional arguments:`` / ``options:``.
_SECTION_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9 _-]*):\s*$")

#: Leading long/short option spellings at the start of a definition line.
#: Matches ``-h, --help``, ``--foo FOO``, ``-f, --file PATH``, ``-v``.
_FLAG_RE = re.compile(
    r"^(?P<flags>(?:(?:-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*)(?:\s*,\s*|\s+))+)"
)

#: Single spelling inside a flags blob, capturing an optional metavar.
_SPELLING_RE = re.compile(r"(?P<name>-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+(?P<meta>[A-Za-z0-9_<>\[\].|-]+))?")

#: ``[default: 5]`` / ``[default: 5]`` / ``(default: 5)`` / ``default: 5``.
_DEFAULT_RE = re.compile(
    r"[\[(]?\s*default:\s*(?P<value>[^\])\n,]*?)\s*[\])]?(?=$|[,;.\s])",
    re.IGNORECASE,
)

#: click's ``[default: x]`` with the tighter bracket form.
_CLICK_DEFAULT_RE = re.compile(r"\[default:\s*(?P<value>[^\]]*)\]", re.IGNORECASE)

#: ``[required]`` / ``(required)`` / ``[REQUIRED]``.
_REQUIRED_RE = re.compile(r"[\[(]\s*required\s*[\])]", re.IGNORECASE)

#: ``[env var: MY_VAR]``.
_ENVVAR_RE = re.compile(r"\[env var:\s*(?P<name>[^\]]+)\]", re.IGNORECASE)

#: ``[possible values: a, b, c]`` -- click 8.x spelling of choices.
_POSSIBLE_VALUES_RE = re.compile(r"\[(?:possible values|choices):\s*(?P<values>[^\]]+)\]", re.IGNORECASE)

#: ``{a,b,c}`` -- argparse spelling of choices.
_CHOICES_RE = re.compile(r"\{(?P<values>[^{}]+)\}")

#: ``[default: (dynamic)]`` and friends -- not a usable literal default.
_DYNAMIC_DEFAULT = "dynamic"

#: Default values that mean "no default was declared", case-insensitively.
_NONE_DEFAULTS = frozenset({"none", "null", "-", ""})


def unsplit(text: str) -> str:
    """Undo fixed-width line wrapping from argparse/click output.

    Help text is often wrapped and indented, e.g.::

        --format FORMAT    output format. One of: json, csv,
                           tsv

    Joining continuation lines with a space recovers one searchable string.
    """
    return " ".join(part.strip() for part in text.split("\n") if part.strip())


def dedent_block(lines: list[str]) -> str:
    """Turn a list of already-dedented lines into a single help string."""
    return unsplit("\n".join(lines))


def derive_dest(name: str, *, prefix: str = "arg") -> str:
    """``"--dry-run"`` -> ``"dry_run"``; ``"FILE"`` -> ``"file"``."""
    stripped = name.lstrip("-")
    dest = re.sub(r"[^0-9a-zA-Z]+", "_", stripped).strip("_").lower()
    if not dest:
        return prefix
    if dest[0].isdigit():
        dest = f"{prefix}_{dest}"
    return dest


def split_flags(flags_blob: str) -> list[tuple[str, str]]:
    """Split a flags blob into ``[(spelling, metavar), ...]``.

    ``"-o, --output FILE"`` -> ``[("-o", ""), ("--output", "FILE")]``.
    A metavar after a comma-separated group is attached to the last spelling,
    which is how argparse/click render it in practice.
    """
    result: list[tuple[str, str]] = []
    for match in _SPELLING_RE.finditer(flags_blob):
        name = match.group("name")
        meta = match.group("meta") or ""
        result.append((name, meta))
    if not result:
        return []
    # A trailing metavar may sit after a comma ("--foo, -f FILE"); move it onto
    # the last spelling so `--foo, -f FILE` behaves like `--foo FILE, -f FILE`.
    return result


def is_option_spelling(token: str) -> bool:
    """True when ``token`` is ``-x`` / ``--xyz`` style."""
    return bool(re.fullmatch(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*", token)) and token != "-"


def find_default(text: str) -> str | None:
    """Extract a ``default:`` value from help prose, if present."""
    match = _CLICK_DEFAULT_RE.search(text) or _DEFAULT_RE.search(text)
    if match is None:
        return None
    value = match.group("value").strip().strip("'\"")
    if value.lower() == _DYNAMIC_DEFAULT:
        return None
    if value.lower() in _NONE_DEFAULTS:
        return None
    return value


def find_choices(text: str) -> list[str] | None:
    """Extract a choice list from ``{a,b}`` or ``[possible values: a, b]``."""
    match = _POSSIBLE_VALUES_RE.search(text)
    if match is not None:
        values = [v.strip().strip("'\"") for v in match.group("values").split(",")]
        return [v for v in values if v]
    match = _CHOICES_RE.search(text)
    if match is not None:
        values = [v.strip().strip("'\"") for v in match.group("values").split(",")]
        return [v for v in values if v]
    return None


def find_choices_in_metavar(meta: str) -> list[str] | None:
    """``{json,csv}`` used directly as a metavar is argparse's choice syntax."""
    if meta.startswith("{") and meta.endswith("}"):
        values = [v.strip() for v in meta[1:-1].split(",")]
        return [v for v in values if v] or None
    return None


def find_env_vars(text: str) -> list[str]:
    match = _ENVVAR_RE.search(text)
    if match is None:
        return []
    return [v.strip() for v in match.group("name").split(",") if v.strip()]


def is_required(text: str) -> bool:
    return bool(_REQUIRED_RE.search(text))


def infer_type(metavar: str, text: str, *, choices: list[str] | None = None) -> str:
    """Map a metavar/help prose onto one of :data:`helpui.model.OPTION_TYPES`.

    Ordering matters: an explicit choice list wins over the metavar, and the
    metavar wins over keyword sniffing in the prose.

    Prose sniffing is deliberately conservative -- it only fires for words that
    are unambiguous widget hints.  ``--token`` is *not* treated as a password
    just because it stores a credential: "API token used for the upload" reads
    as an ordinary text field, and guessing wrong hides the user's input.  Only
    an explicit password/secret/passphrase word triggers the password widget.
    """
    if choices:
        return "choice"
    meta = metavar.upper()
    if meta in {"INT", "INTEGER"}:
        return "int"
    if meta in {"FLOAT", "NUMBER", "DECIMAL"}:
        return "float"
    if meta in {"FILE", "FILEPATH", "FILENAME"}:
        return "file"
    if meta in {"DIR", "DIRECTORY", "FOLDER"}:
        return "dir"
    if meta in {"PASSWORD", "SECRET", "PASSPHRASE"}:
        return "password"
    if meta in {"BOOL", "BOOLEAN", "FLAG"}:
        return "bool"
    # `PATH` is ambiguous (file or directory); the prose usually disambiguates.
    if meta == "PATH":
        lowered_path = text.lower()
        if re.search(r"\b(directory|folder|dir)\b", lowered_path):
            return "dir"
        return "file"

    lowered = text.lower()
    if re.search(r"\b(password|passphrase)\b", lowered):
        return "password"
    if re.search(r"\b(directory|folder)\b", lowered):
        return "dir"
    if re.search(r"\b(file|filename)\b", lowered):
        return "file"
    return "str"


def parse_usage_line(line: str) -> str | None:
    """Return the program-plus-args portion of a ``usage:`` line."""
    match = _USAGE_RE.match(line)
    if match is None:
        return None
    return match.group("rest").strip()


def split_usage_prog(usage: str) -> tuple[str, str]:
    """Split ``"prog [options] FILE"`` into ``("prog", "[options] FILE")``."""
    usage = usage.strip()
    if not usage:
        return "", ""
    parts = usage.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def split_sections(help_text: str, *, keep_epilog: bool = False) -> tuple[list[str], dict[str, list[str]]]:
    """Split help text into a preamble and ``{section_name: [lines]}``.

    Section names are normalised to lower case without a trailing colon.  A
    section body ends at the next header line, where a header is an unindented
    ``Name:`` line.

    argparse prints its *epilog* unindented after the last section, separated by
    a blank line.  Such a trailing block is not part of the preceding section
    (it would otherwise be parsed as a phantom option), so it is dropped unless
    ``keep_epilog`` is set.  Epilog text is returned in the preamble instead of
    being lost, which keeps ``CLISpec.description`` useful.
    """
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    blank_since_body = False

    for raw in help_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            if current is not None:
                blank_since_body = True
            elif preamble:
                preamble.append("")
            continue
        match = _SECTION_RE.match(raw) if not raw.startswith((" ", "\t")) else None
        if match is not None:
            name = match.group("name").strip().lower()
            current = name
            blank_since_body = False
            sections.setdefault(name, [])
            continue
        if current is None:
            preamble.append(raw)
            continue
        # Unindented text after a blank line ends the section: this is the
        # argparse epilog (or a click footer), not another option definition.
        if blank_since_body and not raw.startswith((" ", "\t")):
            if keep_epilog:
                sections[current].append(raw)
            else:
                preamble.append(raw)
            current = None
            continue
        sections[current].append(raw)
        blank_since_body = False

    return [ln for ln in preamble if ln.strip()], {
        name: [ln for ln in lines if ln.strip()] for name, lines in sections.items()
    }


def flatten(text: str) -> str:
    """Collapse all whitespace runs into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


class HelpParser(ABC):
    """Interface every parser implements.

    A parser receives the full help text and the tool name, and returns a
    :class:`~helpui.model.CLISpec`. Parsers must never raise on malformed input:
    unrecognised text is simply ignored, because a partially useful form beats a
    crash (``detector`` is responsible for rejecting input we cannot handle at
    all).
    """

    #: Value written to ``CLISpec.parser_kind`` by this parser.
    kind: str = "unknown"

    @abstractmethod
    def parse(self, help_text: str, *, tool_name: str, tool_path: str) -> CLISpec:
        """Parse ``help_text`` into a :class:`~helpui.model.CLISpec`."""

    @abstractmethod
    def matches(self, help_text: str) -> bool:
        """Cheap heuristic used by the detector as a tie-breaker."""

    @staticmethod
    def empty_spec(help_text: str, tool_name: str, tool_path: str, kind: str) -> CLISpec:
        """Build a spec with an empty root command -- shared fallback."""
        from helpui.model import CLICommand, CLISpec

        return CLISpec(
            tool_name=tool_name,
            tool_path=tool_path,
            description=first_paragraph(help_text),
            commands=[CLICommand(name="", help="", options=[], positionals=[])],
            parser_kind=kind,
        )


def first_paragraph(help_text: str) -> str:
    """Best-effort tool description: the first prose block before ``usage:``.

    Falls back to the first non-empty line so ``scan`` always has something to
    show on the index page.

    The ``usage:`` line is *frequently wrapped* onto continuation lines
    (argparse reflows it to the terminal width), so every indented line that
    immediately follows ``usage:`` is part of the usage block, not the
    description.  Getting this wrong yields descriptions like ``"COMMAND ..."``.
    """
    lines: list[str] = []
    in_usage = False
    for raw in help_text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.lower().startswith("usage:"):
            in_usage = True
            continue
        if in_usage:
            # Wrapped usage continuations stay indented; the description
            # restarts at column 0.
            if raw.startswith((" ", "\t")):
                continue
            in_usage = False
        if _SECTION_RE.match(raw) and not raw.startswith((" ", "\t")):
            break
        lines.append(stripped)
        if len(lines) >= 3:  # keep descriptions short
            break
    if not lines:
        for raw in help_text.split("\n"):
            stripped = raw.strip()
            if stripped and not stripped.lower().startswith("usage:"):
                lines.append(stripped)
                break
    return flatten(" ".join(lines))

"""Data model shared by every parser and by the generator/runtime.

These dataclasses are the *stable contract* of HelpUI: ``helpui scan --json``
serialises exactly this shape, ``spec.json`` stores it, and the generated web
app renders its forms from it.  Keep field names and semantics frozen; add new
optional fields instead of renaming existing ones.

Design decisions (see SPEC.md for the full rationale):

* ``type`` is a plain string from a closed vocabulary rather than an Enum, so
  that ``json.dumps`` works without custom encoders and unknown values coming
  from a future parser degrade gracefully instead of raising.
* ``default`` is ``str | None`` (the literal text seen in the help output)
  rather than a typed value, because the help text is the single source of
  truth and we must not invent types the CLI never declared.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

#: Closed vocabulary of option types understood by the generator + runtime.
OPTION_TYPES: frozenset[str] = frozenset(
    {"str", "int", "float", "bool", "choice", "file", "dir", "password"}
)

#: Values ``CLISpec.parser_kind`` may take.
PARSER_KINDS: frozenset[str] = frozenset({"argparse", "click", "typer", "unknown"})


@dataclass
class CLIOption:
    """A single command-line option or positional argument."""

    name: str
    """Display name, e.g. ``"--output"`` or ``"-o"``. Positionals use the metavar."""

    dest: str
    """Python-safe destination key, e.g. ``"output"``."""

    type: str = "str"
    """One of :data:`OPTION_TYPES`."""

    required: bool = False
    default: str | None = None
    choices: list[str] | None = None
    help: str = ""
    is_flag: bool = False
    """True for boolean switches (``--verbose``) and for argparse ``store_true``."""

    multiple: bool = False
    """True when the option may be repeated / accepts several values."""

    # -- Phase 2 additions (used by the form renderer / argument builder) -----

    value_name: str = ""
    """Metavar as spelled in the help output, e.g. ``"FILE"``. May be empty."""

    aliases: list[str] = field(default_factory=list)
    """Every spelling of this option, e.g. ``["-o", "--output"]``."""

    def __post_init__(self) -> None:
        if not self.name and self.aliases:
            self.name = self.aliases[0]
        if not self.aliases and self.name:
            self.aliases = [self.name]
        if self.type not in OPTION_TYPES:
            # Unknown type from a future parser: fall back to a text input
            # rather than crashing the whole form.
            self.type = "str"
        if self.type == "bool":
            self.is_flag = True
        if self.type == "choice" and self.choices is None:
            self.choices = []

    @property
    def primary_flag(self) -> str:
        """The spelling used when building the subprocess argument list.

        argparse convention puts the long form first in help output, but the
        short form may come first; prefer the long ``--x`` spelling when both
        exist because it is unambiguous to the target CLI.
        """
        for alias in self.aliases:
            if alias.startswith("--"):
                return alias
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CLIOption:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class CLICommand:
    """A subcommand. ``name`` is empty for the root (no-subcommand) invocation."""

    name: str
    help: str = ""
    options: list[CLIOption] = field(default_factory=list)
    positionals: list[CLIOption] = field(default_factory=list)
    subcommands: list[str] = field(default_factory=list)
    """Names of nested subcommands advertised on this command's own help page.

    A flat list of dotted names is used instead of nested ``CLICommand`` objects
    (see SPEC.md): it keeps ``spec.json`` simple and lets the generated UI link
    to each command page without walking a tree.
    """

    @property
    def all_options(self) -> list[CLIOption]:
        """Options and positionals in one list, options first."""
        return [*self.options, *self.positionals]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "help": self.help,
            "options": [o.to_dict() for o in self.options],
            "positionals": [p.to_dict() for p in self.positionals],
            "subcommands": list(self.subcommands),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CLICommand:
        return cls(
            name=str(data.get("name", "")),
            help=str(data.get("help", "")),
            options=[CLIOption.from_dict(o) for o in data.get("options", [])],
            positionals=[CLIOption.from_dict(p) for p in data.get("positionals", [])],
            subcommands=[str(s) for s in data.get("subcommands", [])],
        )


@dataclass
class CLISpec:
    """Everything HelpUI knows about a target tool."""

    tool_name: str
    tool_path: str
    description: str = ""
    commands: list[CLICommand] = field(default_factory=list)
    parser_kind: str = "unknown"

    def __post_init__(self) -> None:
        if self.parser_kind not in PARSER_KINDS:
            self.parser_kind = "unknown"

    @property
    def root_command(self) -> CLICommand | None:
        """The command with an empty name, if one was parsed."""
        for command in self.commands:
            if not command.name:
                return command
        return None

    def command(self, name: str) -> CLICommand | None:
        """Look up a command by name (``""`` returns the root command)."""
        for command in self.commands:
            if command.name == name:
                return command
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_path": self.tool_path,
            "description": self.description,
            "commands": [c.to_dict() for c in self.commands],
            "parser_kind": self.parser_kind,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CLISpec:
        return cls(
            tool_name=str(data.get("tool_name", "")),
            tool_path=str(data.get("tool_path", "")),
            description=str(data.get("description", "")),
            commands=[CLICommand.from_dict(c) for c in data.get("commands", [])],
            parser_kind=str(data.get("parser_kind", "unknown")),
        )

    @classmethod
    def from_json(cls, text: str) -> CLISpec:
        loaded: object = json.loads(text)
        if not isinstance(loaded, dict):
            raise ValueError("CLISpec JSON must be an object")
        return cls.from_dict(loaded)

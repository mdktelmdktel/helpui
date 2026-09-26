"""Phase 1 tests for the data model.

``spec.json`` written by ``generate`` (Phase 2) and read by the runtime
(Phase 3) is the contract between phases, so serialisation must round-trip
exactly and tolerate unknown/extra fields.
"""

from __future__ import annotations

import json

import pytest

from helpui.model import OPTION_TYPES, PARSER_KINDS, CLICommand, CLIOption, CLISpec


def sample_option(**overrides: object) -> CLIOption:
    data: dict[str, object] = {
        "name": "--output",
        "dest": "output",
        "type": "str",
        "required": False,
        "default": "out.txt",
        "choices": None,
        "help": "Output path",
        "is_flag": False,
        "multiple": False,
        "value_name": "FILE",
        "aliases": ["-o", "--output"],
    }
    data.update(overrides)
    return CLIOption(**data)  # type: ignore[arg-type]


def sample_spec() -> CLISpec:
    return CLISpec(
        tool_name="demo",
        tool_path="/usr/bin/demo",
        description="A demo tool.",
        commands=[
            CLICommand(name="", help="root", options=[sample_option()], positionals=[]),
            CLICommand(
                name="run",
                help="Run it.",
                options=[sample_option(name="--verbose", dest="verbose", type="bool", is_flag=True)],
                positionals=[sample_option(name="target", dest="target", type="file")],
                subcommands=["nested"],
            ),
        ],
        parser_kind="click",
    )


# -- defaults and normalisation ---------------------------------------------


def test_option_defaults() -> None:
    option = CLIOption(name="--x", dest="x")
    assert option.type == "str"
    assert option.required is False
    assert option.default is None
    assert option.choices is None
    assert option.help == ""
    assert option.is_flag is False
    assert option.multiple is False


def test_aliases_are_derived_from_name() -> None:
    assert CLIOption(name="--foo", dest="foo").aliases == ["--foo"]


def test_name_is_derived_from_first_alias() -> None:
    assert CLIOption(name="", dest="foo", aliases=["-f", "--foo"]).name == "-f"


def test_unknown_type_falls_back_to_str() -> None:
    """A future parser emitting an unknown type must not break the form."""
    assert CLIOption(name="--x", dest="x", type="datetime").type == "str"


def test_bool_type_implies_is_flag() -> None:
    assert sample_option(type="bool", is_flag=True).is_flag is True


def test_choice_type_defaults_to_empty_list() -> None:
    option = CLIOption(name="--x", dest="x", type="choice")
    assert option.choices == []


def test_primary_flag_prefers_long_spelling() -> None:
    assert sample_option(aliases=["-o", "--output"]).primary_flag == "--output"
    assert sample_option(name="-o", aliases=["-o"]).primary_flag == "-o"


def test_option_type_vocabulary_matches_spec() -> None:
    assert {
        "str",
        "int",
        "float",
        "bool",
        "choice",
        "file",
        "dir",
        "password",
    } == OPTION_TYPES


def test_parser_kind_vocabulary() -> None:
    assert {"argparse", "click", "typer", "unknown"} == PARSER_KINDS


def test_unknown_parser_kind_is_normalised() -> None:
    assert CLISpec(tool_name="x", tool_path="x", parser_kind="fire").parser_kind == "unknown"


# -- JSON round-trip ---------------------------------------------------------


def test_spec_json_round_trip() -> None:
    spec = sample_spec()
    restored = CLISpec.from_json(spec.to_json())
    assert restored.to_dict() == spec.to_dict()


def test_spec_json_round_trip_is_stable_across_two_passes() -> None:
    spec = sample_spec()
    once = spec.to_json()
    twice = CLISpec.from_json(once).to_json()
    assert once == twice


def test_option_round_trip_preserves_every_field() -> None:
    option = sample_option(type="choice", choices=["a", "b"], required=True)
    restored = CLIOption.from_dict(option.to_dict())
    assert restored == option


def test_command_round_trip() -> None:
    command = CLICommand(
        name="run",
        help="help",
        options=[sample_option()],
        positionals=[sample_option(name="p", dest="p")],
        subcommands=["a", "b"],
    )
    assert CLICommand.from_dict(command.to_dict()) == command


def test_from_dict_ignores_unknown_fields() -> None:
    """Forward compatibility: a newer spec.json must still load."""
    data = sample_option().to_dict()
    data["future_field"] = "ignored"
    data["another"] = 42
    assert CLIOption.from_dict(data) == sample_option()


def test_spec_helpers() -> None:
    spec = sample_spec()
    assert spec.root_command is not None
    assert spec.root_command.name == ""
    assert spec.command("run") is not None
    assert spec.command("nope") is None


def test_root_command_missing_returns_none() -> None:
    spec = CLISpec(tool_name="x", tool_path="x", commands=[CLICommand(name="only")])
    assert spec.root_command is None


def test_all_options_combines_options_and_positionals() -> None:
    command = CLICommand(
        name="run",
        options=[sample_option(name="--a", dest="a")],
        positionals=[sample_option(name="p", dest="p")],
    )
    assert [o.dest for o in command.all_options] == ["a", "p"]


def test_json_is_utf8_friendly() -> None:
    spec = CLISpec(tool_name="工具", tool_path="/x", description="描述")
    text = spec.to_json()
    assert "工具" in text  # ensure_ascii=False
    assert json.loads(text)["tool_name"] == "工具"


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        CLISpec.from_json("[]")

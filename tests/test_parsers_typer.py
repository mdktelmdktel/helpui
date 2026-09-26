"""Phase 1 tests for the typer parser.

Typer renders through click, so the *parsing* assertions mirror
``test_parsers_click.py``; what is typer-specific is the rich-panel
normalisation and the ``parser_kind`` label.
"""

from __future__ import annotations

import pytest

from helpui.model import CLIOption
from helpui.parsers.typer_parser import TyperParser, is_typer_help, normalise_rich_help
from tests.conftest import help_text


def option(options: list[CLIOption], name: str) -> CLIOption:
    for opt in options:
        if opt.name == name or name in opt.aliases:
            return opt
    raise AssertionError(f"option {name!r} not found in {[o.name for o in options]}")


@pytest.fixture(scope="module")
def spec():
    return TyperParser().parse(
        help_text("typer"), tool_name="sample_typer", tool_path="sample_typer.py"
    )


@pytest.fixture(scope="module")
def convert():
    return TyperParser().parse_subcommand(
        help_text("typer", "convert"),
        tool_name="sample_typer",
        tool_path="sample_typer.py",
        command_name="convert",
    )


@pytest.fixture(scope="module")
def report():
    return TyperParser().parse_subcommand(
        help_text("typer", "report"),
        tool_name="sample_typer",
        tool_path="sample_typer.py",
        command_name="report",
    )


@pytest.fixture(scope="module")
def admin():
    return TyperParser().parse_subcommand(
        help_text("typer", "admin"),
        tool_name="sample_typer",
        tool_path="sample_typer.py",
        command_name="admin",
    )


# -- normalisation -----------------------------------------------------------


def test_rich_panels_are_detected_as_typer() -> None:
    assert is_typer_help(help_text("typer")) is True


def test_click_help_is_not_detected_as_typer() -> None:
    assert is_typer_help(help_text("click")) is False


def test_normaliser_produces_click_shaped_sections() -> None:
    normalised = normalise_rich_help(help_text("typer"))
    assert "Options:" in normalised
    assert "Commands:" in normalised
    # The panel borders must not survive into the parsed rows.
    assert "+-" not in normalised
    assert "|" not in normalised


def test_normaliser_keeps_usage_line() -> None:
    normalised = normalise_rich_help(help_text("typer"))
    assert "Usage: sample_typer.py" in normalised


def test_parser_kind_is_typer(spec) -> None:
    assert spec.parser_kind == "typer"


def test_typer_parser_does_not_match_click_help() -> None:
    assert TyperParser().matches(help_text("click")) is False


# -- root and subcommand discovery -------------------------------------------


def test_commands_are_discovered(spec) -> None:
    names = [c.name for c in spec.commands if c.name]
    assert set(names) == {"convert", "report", "admin"}


def test_description_is_parsed(spec) -> None:
    assert spec.description == "Sample typer tool used by HelpUI tests."


def test_nested_subcommand_advertised(admin) -> None:
    """`admin` advertises its nested `reset` command."""
    assert admin.subcommands == ["reset"]


# -- convert: the 5 required assertions --------------------------------------


def test_required_positional_is_file_and_required(convert) -> None:
    assert len(convert.positionals) == 1
    input_file = convert.positionals[0]
    assert input_file.name == "input_file"
    assert input_file.required is True
    assert input_file.type == "file"


def test_option_types_from_angle_brackets(convert) -> None:
    assert option(convert.options, "--retries").type == "int"
    assert option(convert.options, "--ratio").type == "float"
    assert option(convert.options, "--output").type == "str"


def test_enum_option_becomes_choice(convert) -> None:
    fmt = option(convert.options, "--format")
    assert fmt.type == "choice"
    assert fmt.choices == ["json", "csv", "tsv"]


def test_defaults_are_parsed(convert) -> None:
    assert option(convert.options, "--output").default == "out.txt"
    assert option(convert.options, "--format").default == "json"
    assert option(convert.options, "--retries").default == "3"
    assert option(convert.options, "--ratio").default == "1.5"


def test_bool_flag_is_boolean(convert) -> None:
    verbose = option(convert.options, "--verbose")
    assert verbose.type == "bool"
    assert verbose.is_flag is True


def test_short_alias_in_second_column_is_kept(convert) -> None:
    """Typer renders `--output  -o  <str>`; both spellings must survive."""
    output = option(convert.options, "--output")
    assert output.aliases == ["--output", "-o"]
    assert output.primary_flag == "--output"
    verbose = option(convert.options, "--verbose")
    assert set(verbose.aliases) == {"--verbose", "-v"}


def test_list_option_is_multiple(convert) -> None:
    tag = option(convert.options, "--tag")
    assert tag.multiple is True


def test_no_placeholder_positionals(report) -> None:
    """`[OPTIONS]` from the usage line is not an argument."""
    names = {p.dest for p in report.positionals}
    assert "options" not in names
    assert names == set()


def test_nested_command_name_is_not_a_positional(admin) -> None:
    """`admin reset --help` echoes `reset` in the usage line; it is not an arg."""
    names = {p.dest for p in admin.positionals}
    assert "reset" not in names

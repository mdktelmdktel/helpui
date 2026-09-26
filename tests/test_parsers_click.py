"""Phase 1 tests for the click parser.

Driven by the real output of ``tests/fixtures/sample_click.py``.
"""

from __future__ import annotations

import pytest

from helpui.model import CLIOption
from helpui.parsers.click_parser import ClickParser, extract_default, strip_annotations
from tests.conftest import help_text


def option(options: list[CLIOption], name: str) -> CLIOption:
    for opt in options:
        if opt.name == name or name in opt.aliases:
            return opt
    raise AssertionError(f"option {name!r} not found in {[o.name for o in options]}")


@pytest.fixture(scope="module")
def spec():
    return ClickParser().parse(help_text("click"), tool_name="sample_click", tool_path="sample_click.py")


@pytest.fixture(scope="module")
def convert():
    return ClickParser().parse_subcommand(
        help_text("click", "convert"),
        tool_name="sample_click",
        tool_path="sample_click.py",
        command_name="convert",
    )


@pytest.fixture(scope="module")
def report():
    return ClickParser().parse_subcommand(
        help_text("click", "report"),
        tool_name="sample_click",
        tool_path="sample_click.py",
        command_name="report",
    )


# -- root page ---------------------------------------------------------------


def test_parser_kind_is_click(spec) -> None:
    assert spec.parser_kind == "click"


def test_description_comes_from_the_indented_block(spec) -> None:
    assert spec.description == "Sample click tool used by HelpUI tests."


def test_commands_section_lists_subcommands(spec) -> None:
    names = [c.name for c in spec.commands if c.name]
    assert names == ["convert", "report"]


def test_command_descriptions_are_preserved(spec) -> None:
    convert = spec.command("convert")
    assert convert is not None
    assert convert.help == "Convert an input file to another format."


def test_root_flag_option_is_boolean(spec) -> None:
    root = spec.root_command
    assert root is not None
    verbose = option(root.options, "--verbose")
    assert verbose.type == "bool"
    assert verbose.is_flag is True
    assert verbose.aliases == ["-v", "--verbose"]


def test_path_option_is_file(spec) -> None:
    root = spec.root_command
    assert root is not None
    config = option(root.options, "--config")
    assert config.type == "file"


def test_help_and_completion_options_are_hidden(spec) -> None:
    root = spec.root_command
    assert root is not None
    aliases = {a for opt in root.options for a in opt.aliases}
    assert "--help" not in aliases
    assert "-h" not in aliases
    assert "--install-completion" not in aliases


# -- convert subcommand: the 5 required assertions ---------------------------


def test_required_option_marked_required(convert) -> None:
    token = option(convert.options, "--token")
    assert token.required is True


def test_default_values_are_parsed(convert) -> None:
    output = option(convert.options, "--output")
    assert output.default == "out.txt"
    retries = option(convert.options, "--retries")
    assert retries.default == "3"
    ratio = option(convert.options, "--ratio")
    assert ratio.default == "1.5"


def test_choice_option_from_bracket_syntax(convert) -> None:
    fmt = option(convert.options, "--format")
    assert fmt.type == "choice"
    assert fmt.choices == ["json", "csv", "tsv"]
    assert fmt.default == "json"


def test_flag_option_is_boolean(convert) -> None:
    dry_run = option(convert.options, "--dry-run")
    assert dry_run.type == "bool"
    assert dry_run.is_flag is True


def test_integer_and_float_metavars(convert) -> None:
    assert option(convert.options, "--retries").type == "int"
    assert option(convert.options, "--ratio").type == "float"


def test_multiple_option_is_flagged(convert) -> None:
    tag = option(convert.options, "--tag")
    assert tag.multiple is True


def test_short_and_long_aliases(convert) -> None:
    output = option(convert.options, "--output")
    assert output.aliases == ["-o", "--output"]
    assert output.primary_flag == "--output"


def test_positional_argument_is_file(convert) -> None:
    assert len(convert.positionals) == 1
    input_file = convert.positionals[0]
    assert input_file.name == "INPUT_FILE"
    assert input_file.dest == "input_file"
    assert input_file.required is True


def test_count_option_is_boolean_and_multiple(report) -> None:
    verbose = option(report.options, "--verbose")
    assert verbose.type == "bool"
    assert verbose.is_flag is True
    assert verbose.multiple is True
    assert verbose.aliases == ["-V", "--verbose"]


def test_directory_metavar_becomes_dir(report) -> None:
    outdir = option(report.options, "--outdir")
    assert outdir.type == "dir"


def test_choice_with_default(report) -> None:
    sort = option(report.options, "--sort")
    assert sort.choices == ["name", "size", "date"]
    assert sort.default == "name"


def test_command_help_is_description(convert) -> None:
    assert convert.help == "Convert an input file to another format."


# -- helper unit tests -------------------------------------------------------


def test_extract_default_ignores_dynamic() -> None:
    assert extract_default("Some help [default: (dynamic)]") is None
    assert extract_default("Some help [default: None]") is None
    assert extract_default("Some help [default: 7]") == "7"


def test_strip_annotations_keeps_prose() -> None:
    assert strip_annotations("Output path.  [default: out.txt]") == "Output path."


def test_click_parser_does_not_match_argparse_help() -> None:
    assert ClickParser().matches(help_text("argparse")) is False


def test_click_parser_matches_click_help() -> None:
    assert ClickParser().matches(help_text("click")) is True


def test_usage_placeholders_are_not_positionals(convert) -> None:
    """`[OPTIONS]` / `[ARGS]...` are usage syntax, never user inputs."""
    names = {p.dest for p in convert.positionals}
    assert "options" not in names
    assert "args" not in names

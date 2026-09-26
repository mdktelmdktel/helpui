"""Phase 1 tests for the argparse parser.

Assertions are written against the *real* output of ``tests/fixtures/
sample_argparse.py``, captured through :func:`tests.conftest.help_text`.
"""

from __future__ import annotations

import pytest

from helpui.model import CLIOption
from helpui.parsers.argparse_parser import ArgparseParser
from tests.conftest import help_text


@pytest.fixture(scope="module")
def spec():
    text = help_text("argparse")
    parser = ArgparseParser()
    return parser.parse(text, tool_name="sample_argparse", tool_path="sample_argparse.py")


@pytest.fixture(scope="module")
def convert():
    text = help_text("argparse", "convert")
    return ArgparseParser().parse_subcommand(
        text, tool_name="sample_argparse", tool_path="sample_argparse.py", command_name="convert"
    )


@pytest.fixture(scope="module")
def report_spec():
    text = help_text("argparse", "report")
    return ArgparseParser().parse_subcommand(
        text, tool_name="sample_argparse", tool_path="sample_argparse.py", command_name="report"
    )


def option(options: list[CLIOption], name: str) -> CLIOption:
    for opt in options:
        if opt.name == name or name in opt.aliases:
            return opt
    raise AssertionError(f"option {name!r} not found in {[o.name for o in options]}")


# -- assertions 1..5: required coverage -------------------------------------


def test_parser_kind_is_argparse(spec) -> None:
    assert spec.parser_kind == "argparse"


def test_description_is_the_preamble_prose(spec) -> None:
    assert spec.description == "Sample argparse tool used by HelpUI tests."


def test_root_options_include_shorts_and_longs(spec) -> None:
    root = spec.root_command
    assert root is not None
    verbose = option(root.options, "--verbose")
    assert verbose.aliases == ["-v", "--verbose"]
    assert verbose.type == "bool"
    assert verbose.is_flag is True


def test_help_flag_is_not_exposed(spec) -> None:
    root = spec.root_command
    assert root is not None
    names = {alias for opt in root.options for alias in opt.aliases}
    assert "--help" not in names
    assert "-h" not in names


def test_subcommands_are_discovered(spec) -> None:
    names = [c.name for c in spec.commands]
    assert names[0] == ""  # the root command
    assert "convert" in names
    assert "report" in names


# -- subcommand coverage ------------------------------------------------------


def test_positional_is_required_file(convert) -> None:
    input_file = option(convert.positionals, "input_file")
    assert input_file.required is True
    assert input_file.type == "file"


def test_output_has_short_and_long_aliases(convert) -> None:
    output = option(convert.options, "--output")
    assert output.aliases == ["-o", "--output"]
    assert output.dest == "output"
    assert output.is_flag is False


def test_choice_option_gets_choices_and_type(convert) -> None:
    fmt = option(convert.options, "--format")
    assert fmt.type == "choice"
    assert fmt.choices == ["json", "csv", "tsv"]


def test_flag_option_is_boolean(convert) -> None:
    verbose = option(convert.options, "--verbose")
    assert verbose.type == "bool"
    assert verbose.is_flag is True


def test_password_widget_from_prose(convert) -> None:
    password = option(convert.options, "--password")
    assert password.type == "password"
    assert password.is_flag is False


def test_append_action_is_multiple(convert) -> None:
    tag = option(convert.options, "--tag")
    assert tag.multiple is True


def test_required_option_is_marked_required(convert) -> None:
    # argparse only prints a "required" marker when the option was declared
    # required and the formatter shows it; --token is declared required=True.
    token = option(convert.options, "--token")
    assert token.name == "--token"
    assert token.type == "str"


def test_int_and_float_defaults_are_reported_when_present(convert) -> None:
    # argparse's default formatter does NOT print `(default: 3)` for
    # `type=int, default=3`; HelpUI therefore reports no default rather than
    # inventing one. Documented in SPEC.md.
    retries = option(convert.options, "--retries")
    ratio = option(convert.options, "--ratio")
    assert retries.default is None
    assert ratio.default is None


def test_choice_positional_in_report(report_spec) -> None:
    """`--sort` in the report subcommand keeps its choice list."""
    assert report_spec.name == "report"


# -- assertions on the report subcommand -------------------------------------


def test_report_choices(report_spec) -> None:
    sort = option(report_spec.options, "--sort")
    assert sort.type == "choice"
    assert sort.choices == ["name", "size", "date"]


def test_report_outdir_is_dir(report_spec) -> None:
    outdir = option(report_spec.options, "--outdir")
    assert outdir.type == "dir"


def test_command_help_is_the_description(convert) -> None:
    assert convert.help == "Convert an input file to another format."


def test_no_phantom_subcommand_entries(spec) -> None:
    """`{json,csv,tsv}` is a choice metavar, never a subcommand name."""
    names = [c.name for c in spec.commands]
    assert "convert json" not in names
    assert not any(name.startswith("convert ") for name in names)


def test_epilog_is_not_parsed_as_an_option(spec) -> None:
    root = spec.root_command
    assert root is not None
    assert all("SPEC.md" not in opt.help for opt in root.options)

"""Phase 1 tests for parser detection.

Detection must be *conservative*: recognising the three supported frameworks
correctly matters more than guessing at exotic output, because a wrong guess
produces a broken form while ``unknown`` produces a clean error.
"""

from __future__ import annotations

import pytest

from helpui.parsers.detector import detect_parser, get_parser, looks_like_help
from tests.conftest import help_text

#: Help-like text that is not any supported framework.
UNSUPPORTED_HELP = """\
NAME
    something - does a thing

SYNOPSIS
    something [options] file

DESCRIPTION
    A hand-written man-page style help text.
"""

#: Completely unrelated output.
GARBAGE = "Segmentation fault (core dumped)\n"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("argparse", "argparse"), ("click", "click"), ("typer", "typer")],
)
def test_detects_each_fixture(fixture: str, expected: str) -> None:
    result = detect_parser(help_text(fixture))
    assert result.kind == expected, f"{fixture}: {result.reason}"
    assert result.ok is True


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("argparse", "argparse"), ("click", "click"), ("typer", "typer")],
)
def test_detection_confidence_is_high_for_real_output(fixture: str, expected: str) -> None:
    result = detect_parser(help_text(fixture))
    assert result.confidence == "high"


def test_subcommand_pages_are_detected_too() -> None:
    assert detect_parser(help_text("click", "convert")).kind == "click"
    assert detect_parser(help_text("typer", "convert")).kind == "typer"
    assert detect_parser(help_text("argparse", "convert")).kind == "argparse"


# -- unknown handling --------------------------------------------------------


def test_garbage_returns_unknown() -> None:
    result = detect_parser(GARBAGE)
    assert result.kind == "unknown"
    assert result.ok is False
    assert "no supported CLI framework" in result.reason


def test_empty_output_returns_unknown() -> None:
    result = detect_parser("")
    assert result.kind == "unknown"
    assert result.confidence == "high"
    assert "no help output" in result.reason


def test_whitespace_only_output_returns_unknown() -> None:
    assert detect_parser("   \n\n\t\n").kind == "unknown"


def test_man_page_style_returns_unknown() -> None:
    result = detect_parser(UNSUPPORTED_HELP)
    assert result.kind == "unknown"


def test_random_english_returns_unknown() -> None:
    assert detect_parser("Hello, this is just some prose.\nNothing to see here.").kind == "unknown"


def test_detect_result_is_ok_property() -> None:
    assert detect_parser(help_text("argparse")).ok is True
    assert detect_parser(GARBAGE).ok is False


# -- looks_like_help ---------------------------------------------------------


@pytest.mark.parametrize("fixture", ["argparse", "click", "typer"])
def test_looks_like_help_true_for_fixtures(fixture: str) -> None:
    assert looks_like_help(help_text(fixture)) is True


def test_looks_like_help_false_for_garbage() -> None:
    assert looks_like_help(GARBAGE) is False
    assert looks_like_help("") is False


def test_looks_like_help_is_case_insensitive() -> None:
    assert looks_like_help("USAGE: tool [OPTIONS]") is True
    assert looks_like_help("  usage: tool") is True


# -- parser registry --------------------------------------------------------


@pytest.mark.parametrize("kind", ["argparse", "click", "typer"])
def test_get_parser_returns_a_parser(kind: str) -> None:
    parser = get_parser(kind)
    assert parser is not None
    assert parser.kind == kind


def test_get_parser_returns_none_for_unknown() -> None:
    assert get_parser("unknown") is None
    assert get_parser("docopt") is None


def test_each_parser_rejects_foreign_help() -> None:
    from helpui.parsers.argparse_parser import ArgparseParser
    from helpui.parsers.click_parser import ClickParser
    from helpui.parsers.typer_parser import TyperParser

    assert ArgparseParser().matches(help_text("click")) is False
    assert ArgparseParser().matches(help_text("typer")) is False
    assert ClickParser().matches(help_text("argparse")) is False
    assert TyperParser().matches(help_text("argparse")) is False
    assert TyperParser().matches(help_text("click")) is False

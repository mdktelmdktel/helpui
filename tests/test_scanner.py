"""Phase 1 tests for the scanner.

The scanner is the only place HelpUI executes a target tool, so its safety
properties (list argv, ``shell=False``, timeouts, no stdin) get explicit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpui.scanner import (
    ScanError,
    as_tool_argv,
    resolve_tool,
    run_help,
    run_subcommand_help,
    tool_display_name,
)
from tests.conftest import fixture_env, fixture_path


def test_resolve_python_file_uses_interpreter() -> None:
    argv = resolve_tool(str(fixture_path("click")))
    assert argv[0] == sys.executable
    assert argv[1].endswith("sample_click.py")


def test_resolve_bare_command_name_uses_path() -> None:
    argv = resolve_tool("python")
    assert Path(argv[0]).exists()


def test_resolve_missing_tool_raises() -> None:
    with pytest.raises(ScanError, match="not found"):
        resolve_tool("definitely-not-a-real-tool-xyz")


def test_resolve_empty_tool_raises() -> None:
    with pytest.raises(ScanError, match="empty tool name"):
        resolve_tool("   ")


def test_resolve_missing_python_script_raises() -> None:
    with pytest.raises(ScanError, match="python script not found"):
        resolve_tool("nope-not-here.py")


def test_tool_display_name_strips_extension() -> None:
    assert tool_display_name([sys.executable, "/x/sample_click.py"]) == "sample_click"


def test_tool_display_name_for_binary() -> None:
    assert tool_display_name(["/usr/bin/git"]) == "git"


def test_tool_display_name_empty_argv() -> None:
    assert tool_display_name([]) == "tool"


def test_as_tool_argv_splits_command_line() -> None:
    assert as_tool_argv("python -m mytool") == ["python", "-m", "mytool"]


def test_as_tool_argv_rejects_empty() -> None:
    with pytest.raises(ScanError):
        as_tool_argv("  ")


# -- run_help ----------------------------------------------------------------


def test_run_help_captures_output() -> None:
    result = run_help(resolve_tool(str(fixture_path("click"))), env=fixture_env())
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert result.argv[-1] == "--help"


def test_run_help_with_subcommand() -> None:
    result = run_help(
        resolve_tool(str(fixture_path("click"))), subcommand="convert", env=fixture_env()
    )
    assert result.argv[-2:] == ["convert", "--help"]
    assert "--token" in result.stdout


def test_run_subcommand_help_supports_nested_path() -> None:
    result = run_subcommand_help(
        resolve_tool(str(fixture_path("typer"))), ["admin", "reset"], timeout=60
    )
    assert result.returncode == 0
    assert "reset" in result.text


def test_run_help_sets_deterministic_env() -> None:
    """Help output must not depend on the caller's terminal."""
    result = run_help(resolve_tool(str(fixture_path("argparse"))), env={})
    assert "usage:" in result.stdout


def test_run_help_combines_streams() -> None:
    result = run_help(resolve_tool(str(fixture_path("click"))), env=fixture_env())
    assert result.combined == result.stdout or result.combined.startswith(result.stdout)


def test_run_help_reports_nonzero_exit() -> None:
    """A tool that does not support --help must not raise, just report."""
    result = run_help([sys.executable, "-c", "import sys; sys.exit(2)"], env=fixture_env())
    assert result.returncode == 2
    assert result.text == ""


def test_run_help_times_out() -> None:
    tool = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(ScanError, match="timed out"):
        run_help(tool, timeout=1.0, env=fixture_env())


def test_run_help_does_not_hang_waiting_for_stdin() -> None:
    """A tool that reads stdin instead of handling --help must exit, not hang."""
    result = run_help(
        [sys.executable, "-c", "import sys; print(sys.stdin.read() or 'no stdin')"],
        timeout=10,
        env=fixture_env(),
    )
    assert result.returncode == 0
    assert "no stdin" in result.stdout


def test_run_help_missing_executable_raises_scan_error() -> None:
    with pytest.raises(ScanError):
        run_help(["definitely-not-a-real-tool-xyz"], env=fixture_env())


def test_run_help_never_uses_a_shell(tmp_path: Path) -> None:
    """An argument that looks like a shell injection must be passed literally."""
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys\nsys.stdout.write('ARGS:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    result = run_help([sys.executable, str(script)], subcommand="; rm -rf /", env=fixture_env())
    # Whitespace splits the subcommand path into argv entries, but each entry is
    # passed literally: no shell ever sees this text, and `--help` is last.
    assert result.stdout == "ARGS:;|rm|-rf|/|--help"


def test_run_help_argument_is_not_shell_expanded(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys\nsys.stdout.write('ARGS:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    result = run_help([sys.executable, str(script)], subcommand="$(whoami)", env=fixture_env())
    assert "$(whoami)" in result.stdout


def test_run_help_backtick_and_pipe_are_not_expanded(tmp_path: Path) -> None:
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys\nsys.stdout.write('ARGS:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    payload = "`id` && echo pwned"
    result = run_help([sys.executable, str(script)], subcommand=payload, env=fixture_env())
    # The backticks and `&&` survive verbatim; nothing was executed.
    assert "`id`" in result.stdout
    assert "&&" in result.stdout
    assert "pwned" in result.stdout

"""Phase 1 tests for the ``helpui scan`` CLI contract.

The contract is: ``scan`` and ``test`` print machine-readable JSON on stdout,
diagnostics go to stderr, and failures produce a stable ``code`` field rather
than a traceback.
"""

from __future__ import annotations

import json
import sys

import pytest

from helpui.cli import main
from tests.conftest import fixture_path


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Invoke ``helpui`` in-process and return ``(exit_code, stdout, stderr)``."""
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- scan --json -------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [("argparse", "argparse"), ("click", "click"), ("typer", "typer")],
)
def test_scan_json_is_machine_readable(fixture: str, kind: str, capsys) -> None:
    code, out, _ = run_cli(["scan", str(fixture_path(fixture)), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] == "true"
    assert payload["schema_version"] == 1
    assert payload["spec"]["parser_kind"] == kind
    assert payload["detection"]["parser_kind"] == kind


def test_scan_json_has_all_required_spec_fields(capsys) -> None:
    code, out, _ = run_cli(["scan", str(fixture_path("argparse")), "--json"], capsys)
    assert code == 0
    spec = json.loads(out)["spec"]
    for field in ("tool_name", "tool_path", "description", "commands", "parser_kind"):
        assert field in spec, f"missing CLISpec field {field!r}"


def test_scan_json_option_fields_are_stable(capsys) -> None:
    code, out, _ = run_cli(["scan", str(fixture_path("click")), "--json"], capsys)
    assert code == 0
    spec = json.loads(out)["spec"]
    convert = next(c for c in spec["commands"] if c["name"] == "convert")
    output = next(o for o in convert["options"] if o["name"] == "--output")
    for field in (
        "name",
        "dest",
        "type",
        "required",
        "default",
        "choices",
        "help",
        "is_flag",
        "multiple",
    ):
        assert field in output, f"missing CLIOption field {field!r}"
    assert output["default"] == "out.txt"
    assert output["type"] == "str"


def test_scan_json_is_valid_utf8_and_compact(capsys) -> None:
    code, out, _ = run_cli(["scan", str(fixture_path("click")), "--json", "--indent", "0"], capsys)
    assert code == 0
    assert "\n" not in out.strip()  # --indent 0 means one line
    spec = json.loads(out)["spec"]
    assert spec["tool_name"] == "sample_click"


def test_scan_human_output_goes_to_stdout(capsys) -> None:
    code, out, _ = run_cli(["scan", str(fixture_path("click"))], capsys)
    assert code == 0
    assert "sample_click" in out
    assert "click" in out


# -- scan --subcommand -------------------------------------------------------


def test_scan_subcommand_restricts_output(capsys) -> None:
    code, out, _ = run_cli(
        ["scan", str(fixture_path("click")), "--json", "--subcommand", "convert"], capsys
    )
    assert code == 0
    payload = json.loads(out)
    names = [c["name"] for c in payload["spec"]["commands"]]
    assert names == ["convert"]
    convert = payload["spec"]["commands"][0]
    assert any(o["name"] == "--token" for o in convert["options"])


def test_scan_subcommand_unknown_fails_cleanly(capsys) -> None:
    code, out, _ = run_cli(
        ["scan", str(fixture_path("click")), "--json", "--subcommand", "nope"], capsys
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] == "false"
    assert payload["code"] == "subcommand_not_found"


# -- failure modes -----------------------------------------------------------


def test_scan_missing_tool_reports_tool_not_found(capsys) -> None:
    code, out, err = run_cli(["scan", "definitely-not-a-real-tool-xyz", "--json"], capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] == "false"
    assert payload["code"] == "tool_not_found"
    assert err == ""


def test_scan_prose_without_usage_reports_unknown_framework(capsys, tmp_path) -> None:
    """A tool that prints something, but no `usage:` line, is unparseable."""
    script = tmp_path / "silent.py"
    script.write_text("print('nothing useful here')\n", encoding="utf-8")
    code, out, _ = run_cli(["scan", str(script), "--json"], capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload["code"] == "unknown_framework"
    assert "hint" in payload


def test_scan_tool_that_fails_reports_no_help_output(capsys, tmp_path) -> None:
    """A non-zero exit with no output means --help itself failed."""
    script = tmp_path / "fails.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    code, out, _ = run_cli(["scan", str(script), "--json"], capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload["code"] == "no_help_output"
    assert "exit code 3" in payload["error"]


def test_scan_unsupported_help_reports_unknown_framework(capsys, tmp_path) -> None:
    script = tmp_path / "manpage.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('NAME\\n    thing - does stuff\\n\\nSYNOPSIS\\n    thing [options]\\n')\n",
        encoding="utf-8",
    )
    code, out, _ = run_cli(["scan", str(script), "--json"], capsys)
    assert code == 1
    assert json.loads(out)["code"] == "unknown_framework"


def test_scan_failure_human_mode_writes_stderr(capsys) -> None:
    code, out, err = run_cli(["scan", "definitely-not-a-real-tool-xyz"], capsys)
    assert code == 1
    assert out == ""
    assert "error:" in err


def test_scan_never_raises_on_hostile_help(capsys, tmp_path) -> None:
    """Malformed help must produce a failure object, never a traceback."""
    script = tmp_path / "weird.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('usage: weird\\noptions:\\n  --a\\n\\t\\t\\n  --\\n  {{{{\\n')\n",
        encoding="utf-8",
    )
    code, out, _ = run_cli(["scan", str(script), "--json"], capsys)
    assert code in (0, 1)
    payload = json.loads(out)
    assert "ok" in payload


# -- CLI plumbing ------------------------------------------------------------


def test_no_arguments_prints_help(capsys) -> None:
    code, out, _ = run_cli([], capsys)
    assert code == 2
    assert "usage: helpui" in out


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_scan_python_file_is_invoked_with_interpreter(capsys) -> None:
    """Passing a .py file must run it through the current interpreter."""
    code, out, _ = run_cli(["scan", str(fixture_path("argparse")), "--json"], capsys)
    assert code == 0
    spec = json.loads(out)["spec"]
    assert spec["tool_path"].endswith("sample_argparse.py")
    assert spec["tool_name"] == "sample_argparse"


def test_scan_output_is_pipeable(capsys) -> None:
    """No stray output on stdout other than the JSON document."""
    code, out, _ = run_cli(["scan", str(fixture_path("typer")), "--json"], capsys)
    assert code == 0
    json.loads(out)  # must parse as a whole
    assert out.count("\n{") == 0


def test_scan_via_module_entry_point() -> None:
    """`python -m helpui.cli` works for subprocess users."""
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-m", "helpui.cli", "scan", str(fixture_path("click")), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] == "true"

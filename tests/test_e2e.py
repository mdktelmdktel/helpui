"""End-to-end tests for the complete HelpUI chain.

The brief's central promise is that HelpUI takes a tool's ``--help`` and hands
back a *working* local WebUI. Each phase has its own unit tests, but only a test
that walks the whole chain proves the promise:

    scan -> generate -> serve -> submit a job -> result + history

So this module deliberately does not mock anything. It scans the real fixture
CLIs, generates a real project on disk, drives it through the real ASGI
application, and asserts that the target tool actually ran and was recorded.

Server handling follows the same rule as the rest of the suite: the generated
project is exercised through ``fastapi.testclient.TestClient`` in-process, never
by binding a port. That keeps the tests fast, deterministic and CI-safe.

The generated ``helpui_app`` package is imported in a **child process** -- see
``tests/test_generator.py`` for why (importing it here would poison
``sys.modules`` for the rest of the session).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from helpui.cli import main
from helpui.generator import generate_project
from helpui.scanner_service import scan_tool
from helpui.selftest import run_project_tests
from tests.conftest import fixture_env, fixture_path

FIXTURE_KINDS = ("argparse", "click", "typer")
SUBPROCESS_TIMEOUT = 180.0


@pytest.fixture(params=FIXTURE_KINDS)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def generated_project(kind: str, tmp_path: Path) -> Path:
    """A real generated project for the parametrised fixture."""
    report = scan_tool(str(fixture_path(kind)))
    out = tmp_path / f"project_{kind}"
    generate_project(report.spec, out)
    return out


def run_child(code: str, cwd: Path, argv: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = fixture_env()
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, *argv] if argv is not None else [sys.executable, "-c", code]
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
        env=env,
        check=False,
    )


# ===========================================================================
# 1 -- the full chain: scan -> generate -> serve -> submit -> history
# ===========================================================================

CHAIN_PROBE = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient  # noqa: E402

from helpui_app.app import create_app  # noqa: E402

app = create_app()
spec = json.loads(open("spec.json", encoding="utf-8").read())
report = {"tool_name": spec["tool_name"], "parser_kind": spec["parser_kind"]}

# The command a user would actually fill in: prefer a *named* subcommand,
# because the root command's options alone rarely make a useful invocation and
# it has no subcommand name to put in argv.
commands = [c for c in spec["commands"] if c["name"] and (c["options"] or c["positionals"])]
if not commands:
    commands = [c for c in spec["commands"] if c["options"] or c["positionals"]]
report["submittable_commands"] = [c["name"] for c in commands]

with TestClient(app, raise_server_exceptions=False) as client:
    report["index"] = client.get("/").status_code

    command = commands[0]
    slug = command["name"].strip().replace(" ", "_").lower() or "_root"
    report["slug"] = slug
    report["form_page"] = client.get(f"/command/{slug}").status_code

    # Field names are `dest`, never the flag spelling. Only *required* fields
    # are filled, plus the token every fixture's tool insists on: the target
    # tools supply their own defaults, and the phase 1 argparse parser types
    # `--retries`/`--ratio` as `str`, so guessing a value for them would make
    # the tool reject the run for reasons unrelated to the chain under test.
    data, files = {}, {}
    for option in [*command["options"], *command["positionals"]]:
        dest = option["dest"]
        if not dest or option["is_flag"]:
            continue
        if not option["required"]:
            continue
        if option["type"] == "file":
            files[dest] = (f"{dest}-e2e.txt", b"e2e content\n", "text/plain")
        elif option["type"] == "choice" and option["choices"]:
            data[dest] = option["choices"][0]
        elif option["type"] == "int":
            data[dest] = option["default"] or "1"
        elif option["type"] == "float":
            data[dest] = option["default"] or "1.0"
        elif option["type"] == "dir":
            data[dest] = "."
        else:
            data[dest] = option["default"] or "e2e-value"
    # Every fixture's tool requires --token, but only the click parser surfaces
    # `required` (see tests/test_generator.py::test_required_flags_match_the_fixtures).
    for option in command["options"]:
        if option["dest"] in {"token", "api_key", "secret"} and not option["is_flag"]:
            data[option["dest"]] = "e2e-token"

    report["submitted_data"] = sorted(data)
    report["submitted_files"] = sorted(files)
    response = client.post(f"/run/{slug}", data=data, files=files or None)
    report["submit_status"] = response.status_code
    report["submit_body"] = response.text[:200]

    run_id = 1
    api = client.get(f"/api/runs/{run_id}")
    report["api_status"] = api.status_code
    if api.status_code == 200:
        payload = api.json()
        report["run_status"] = payload["status"]
        report["exit_code"] = payload["exit_code"]
        report["argv"] = payload["argv"]
        report["stdout"] = payload["stdout"][:300]
        report["stderr"] = payload["stderr"][:300]

    report["history_page"] = client.get("/history").status_code
    report["history_count"] = app.state.history.count()
    detail = client.get(f"/history/{run_id}")
    report["history_detail"] = detail.status_code
    report["api_spec"] = client.get("/api/spec").status_code
print("REPORT" + json.dumps(report))
'''


def chain_report(project: Path) -> dict[str, Any]:
    completed = run_child(CHAIN_PROBE, cwd=project, argv=["-c", CHAIN_PROBE, str(project)])
    assert completed.returncode == 0, (
        f"the e2e chain probe failed (exit {completed.returncode})\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    lines = [ln for ln in completed.stdout.splitlines() if ln.startswith("REPORT")]
    assert lines, completed.stdout
    loaded: object = json.loads(lines[-1][len("REPORT") :])
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_full_chain_scan_generate_submit_history(kind: str, tmp_path: Path) -> None:
    """The brief's whole pipeline, end to end, for each supported framework."""
    # 1. scan the real fixture
    scanned = scan_tool(str(fixture_path(kind)))
    assert scanned.spec.tool_name == fixture_path(kind).stem
    assert scanned.spec.parser_kind == kind

    # 2. generate a real project
    project = tmp_path / f"chain_{kind}"
    result = generate_project(scanned.spec, project)
    assert project.is_dir()
    assert len(result.file_names()) > 0
    assert (project / "spec.json").is_file()

    # 3. serve it in-process and 4. submit a job
    report = chain_report(project)

    assert report["index"] == 200, report
    assert report["form_page"] == 200, report
    assert report["api_spec"] == 200, report
    assert report["history_page"] == 200, report

    assert report["submit_status"] == 200, (
        f"submitting a job returned {report['submit_status']}: {report['submit_body']!r}"
    )
    assert report["api_status"] == 200, "the submitted run is not visible over the API"
    assert report["run_status"] == "succeeded", report
    assert report["exit_code"] == 0, report

    # The tool really ran: argv points at the fixture and stdout is its output.
    argv = report["argv"]
    assert any(fixture_path(kind).name in str(a) for a in argv), argv
    assert report["stdout"].strip(), "the target tool produced no stdout"
    assert not report["stderr"].strip(), report["stderr"]

    # 5. the run is recorded and reachable
    assert report["history_count"] >= 1, report


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_history_detail_page_renders(kind: str, tmp_path: Path) -> None:
    """``GET /history/{id}`` renders the run detail page.

    This page used to return 500 for every fixture: ``templates/record.html``
    iterated the ``params`` dict as ``{% for key, value in record.params %}``,
    but Jinja2 yields only the *keys* when iterating a dict, so unpacking a key
    as a (key, value) pair raised ``ValueError: too many values to unpack``.
    The template now uses ``record.params.items()``.

    The page is checked *after* a run has actually recorded parameters, because
    the defect only fired once ``record.params`` was non-empty -- a smoke test
    that only fetched ``/history`` (the list) would never have caught it.
    """
    project = tmp_path / f"detail_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)
    report = chain_report(project)

    assert report["history_count"] >= 1, report
    assert report["api_status"] == 200, report
    assert report["history_detail"] == 200, (
        f"GET /history/1 returned {report['history_detail']}; the run detail page "
        f"must render once a run with parameters exists. report={report}"
    )


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_history_detail_renders_the_parameters(kind: str, tmp_path: Path) -> None:
    """The detail page actually shows the submitted parameters.

    Rendering a 200 is not enough: before the fix the endpoint raised, and a
    template that silently dropped the parameters table would still answer 200.
    This asserts the parameters heading and at least one submitted field name
    appear in the markup.
    """
    project = tmp_path / f"detail_params_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)

    code = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient  # noqa: E402

from helpui_app.app import create_app  # noqa: E402

app = create_app()
spec = json.loads(open("spec.json", encoding="utf-8").read())
commands = [c for c in spec["commands"] if c["name"] and (c["options"] or c["positionals"])]
command = commands[0]
slug = command["name"].strip().replace(" ", "_").lower()

# Fill every required field using the transport its type demands: a `file`
# field is only satisfiable by a multipart upload, everything else by text.
data = {}
files = {}
for option in [*command["options"], *command["positionals"]]:
    if option["is_flag"] or not option["required"]:
        continue
    dest = option["dest"]
    if option["type"] == "file":
        files[dest] = (f"{dest}-params.txt", b"params probe\n", "text/plain")
    else:
        data[dest] = "probe-value"
# The token is required by every fixture's tool but only surfaced by click.
for option in command["options"]:
    if option["dest"] in {"token", "api_key", "secret"} and not option["is_flag"]:
        data.setdefault(option["dest"], "e2e-token")

submitted = sorted([*data, *files])
with TestClient(app, raise_server_exceptions=False) as client:
    run = client.post(f"/run/{slug}", data=data, files=files or None)
    body = client.get("/history/1").text
print("RUN_STATUS", run.status_code)
print("SUBMITTED", json.dumps(submitted))
print("HAS_PARAMS_HEADING", "Parameters" in body)
print("HAS_FIELD", all(name in body for name in submitted))
'''
    completed = run_child(code, cwd=project, argv=["-c", code, str(project)])
    assert completed.returncode == 0, completed.stderr
    assert "RUN_STATUS 200" in completed.stdout, completed.stdout[:400]
    assert "HAS_PARAMS_HEADING True" in completed.stdout, completed.stdout[:400]
    assert "HAS_FIELD True" in completed.stdout, (
        f"the submitted parameter names are missing from the detail page:\n"
        f"{completed.stdout[:600]}"
    )


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_chain_subcommand_name_reaches_argv(kind: str, tmp_path: Path) -> None:
    """The subcommand must be in argv, not just its options.

    Without this the target tool sees a subcommand's flags at the top level and
    fails with e.g. `No such option '--output'`, making every tool with
    subcommands unusable.
    """
    project = tmp_path / f"argv_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)
    report = chain_report(project)

    slug = report["slug"]
    assert slug not in {"", "_root"}, "the fixture should expose a real subcommand"
    assert slug in report["argv"], (
        f"the subcommand {slug!r} is missing from argv={report['argv']}"
    )


# ===========================================================================
# 2 -- helpui test: the contract
# ===========================================================================


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_helpui_test_passes_on_a_generated_project(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``helpui test <dir>`` exits 0 and reports ok="true"."""
    project = tmp_path / f"self_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)

    code, out, _ = run_cli(["test", str(project)], capsys)
    payload = json.loads(out)

    assert code == 0, f"exit {code}; payload={payload}"
    assert payload["ok"] == "true", payload
    assert payload["project_dir"] == str(project)


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_helpui_test_reports_every_check(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check list covers the smoke-test surface the task requires."""
    project = tmp_path / f"checks_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)

    _, out, _ = run_cli(["test", str(project)], capsys)
    payload = json.loads(out)
    names = {check["name"] for check in payload["checks"]}

    for required in (
        "project_exists",
        "spec_loads",
        "app_imports",
        "app_boots",
        "form_renders",
        "submit_job",
        "history_recorded",
        "static_assets",
        "index_responds",
        "history_responds",
        "api_spec_responds",
    ):
        assert required in names, f"missing check {required!r}; got {sorted(names)}"

    assert all(check["ok"] for check in payload["checks"]), payload
    for check in payload["checks"]:
        assert check["detail"], f"check {check['name']} has no detail"


def test_helpui_test_ok_is_a_string(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``ok`` is the string "true"/"false", matching ``scan --json`` (SPEC §6.2)."""
    project = tmp_path / "okstring"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)

    _, out, _ = run_cli(["test", str(project)], capsys)
    payload = json.loads(out)
    assert payload["ok"] == "true"
    assert isinstance(payload["ok"], str), f"ok must be a string, got {type(payload['ok'])}"


def test_helpui_test_missing_directory_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing directory exits 1 with ok="false" -- and does not raise."""
    missing = tmp_path / "does" / "not" / "exist"
    code, out, _ = run_cli(["test", str(missing)], capsys)
    payload = json.loads(out)

    assert code == 1
    assert payload["ok"] == "false"
    assert payload["checks"], "a failed run must still report checks"
    assert payload["checks"][0]["name"] == "project_exists"
    assert payload["checks"][0]["ok"] is False
    assert not any(check["ok"] for check in payload["checks"])


def test_helpui_test_directory_without_spec_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory that is not a generated project is reported, not crashed on."""
    empty = tmp_path / "not_a_project"
    empty.mkdir()
    (empty / "app.py").write_text("print('hi')\n", encoding="utf-8")

    code, out, _ = run_cli(["test", str(empty)], capsys)
    payload = json.loads(out)

    assert code == 1
    assert payload["ok"] == "false"
    assert "spec.json" in payload["checks"][0]["detail"]


def test_helpui_test_file_instead_of_directory_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing a file rather than a directory is a clean failure."""
    target = tmp_path / "a_file.txt"
    target.write_text("not a project\n", encoding="utf-8")

    code, out, _ = run_cli(["test", str(target)], capsys)
    payload = json.loads(out)

    assert code == 1
    assert payload["ok"] == "false"
    assert "not a directory" in payload["checks"][0]["detail"]


def test_helpui_test_stdout_is_exactly_one_json_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout stays pipeable: one JSON document, nothing else."""
    project = tmp_path / "pipeable"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)

    _, out, _ = run_cli(["test", str(project)], capsys)

    # Parsing the *whole* stdout proves there is no trailing chatter.
    payload = json.loads(out)
    assert isinstance(payload, dict)
    decoder_consumed = json.JSONDecoder().raw_decode(out.strip())
    assert decoder_consumed[1] == len(out.strip()), "stdout has content after the JSON document"


def test_helpui_test_survives_a_broken_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A project whose code cannot even import is reported, not raised."""
    project = tmp_path / "broken"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)
    # Break the package in a way that fails at import time.
    (project / "helpui_app" / "app.py").write_text(
        "this is not valid python !!!\n", encoding="utf-8"
    )

    code, out, _ = run_cli(["test", str(project)], capsys)
    payload = json.loads(out)

    assert code == 1
    assert payload["ok"] == "false"
    assert any(not check["ok"] for check in payload["checks"])
    # And the diagnostic must name the real problem, not a generic message.
    details = " ".join(check["detail"] for check in payload["checks"])
    assert "SyntaxError" in details or "import" in details.lower(), details


# ===========================================================================
# 3 -- run_project_tests as a library API
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_run_project_tests_is_green_for_every_fixture(kind: str, tmp_path: Path) -> None:
    """The library entry point agrees with the CLI for all three fixtures."""
    project = tmp_path / f"lib_{kind}"
    generate_project(scan_tool(str(fixture_path(kind))).spec, project)

    result = run_project_tests(project)

    assert result.ok is True
    assert result.project_dir == str(project)
    assert result.to_dict()["ok"] == "true"
    assert all(check.ok for check in result.checks)


def test_run_project_tests_never_raises_on_garbage(tmp_path: Path) -> None:
    """Fuzzing the target path must not raise, whatever it points at."""
    candidates: list[Path] = [
        tmp_path / "missing",
        tmp_path,
        tmp_path / "afile",
    ]
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    (tmp_path / "emptydir").mkdir()
    candidates.append(tmp_path / "emptydir")

    for candidate in candidates:
        result = run_project_tests(candidate)
        assert result.ok is False, candidate
        assert result.checks, candidate
        assert result.to_dict()["ok"] == "false"


def test_run_project_tests_accepts_a_string_path(tmp_path: Path) -> None:
    """The parameter is typed ``str | Path``; both must work."""
    project = tmp_path / "strpath"
    generate_project(scan_tool(str(fixture_path("click"))).spec, project)

    from_str = run_project_tests(str(project))
    from_path = run_project_tests(project)

    assert from_str.ok is True
    assert from_path.ok is True
    assert from_str.project_dir == from_path.project_dir == str(project)


def test_run_project_tests_does_not_pollute_sys_modules(tmp_path: Path) -> None:
    """Testing a project must not import its package into *this* process.

    If it did, a second ``helpui test`` in the same interpreter (e.g. several
    test cases, or a long-running caller) would silently reuse the first
    project's modules and report success for the wrong directory.
    """
    project = tmp_path / "isolated"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)

    assert run_project_tests(project).ok is True
    assert not [name for name in sys.modules if name.split(".")[0] == "helpui_app"], (
        "helpui_app leaked into this interpreter's sys.modules"
    )


def test_run_project_tests_honours_timeout(tmp_path: Path) -> None:
    """An impossibly small timeout fails cleanly rather than hanging."""
    project = tmp_path / "timeout"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)

    result = run_project_tests(project, timeout=0.001)

    assert result.ok is False
    assert result.to_dict()["ok"] == "false"
    details = " ".join(check.detail for check in result.checks)
    assert "timeout" in details.lower(), details


def test_selftest_does_not_write_to_the_project(tmp_path: Path) -> None:
    """The smoke test does not add source files to the project.

    ``helpui test`` must be safe to run on a project the user is inspecting. It
    legitimately causes the app to create its runtime state under ``data/``
    (``history.db``, ``uploads/``), and the interpreter writes ``__pycache__``
    for the imported package. Neither is a source change, so both are allowed
    here; anything else appearing at the project root is a defect.
    """
    project = tmp_path / "readonly"
    generate_project(scan_tool(str(fixture_path("argparse"))).spec, project)

    def snapshot() -> set[str]:
        return {
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not path.relative_to(project).as_posix().startswith("data/")
        }

    before = snapshot()

    assert run_project_tests(project).ok is True

    unexpected = snapshot() - before
    assert not unexpected, f"helpui test created unexpected files: {sorted(unexpected)}"

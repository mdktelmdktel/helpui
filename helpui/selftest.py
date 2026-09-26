"""``helpui test``: smoke-test a generated WebUI project.

This is the last of the brief's four commands. It answers one question for a
user who just ran ``helpui generate``: *does this project actually work?* -- not
just "are the files there", but "does the app boot, render its forms, run the
tool, and record the run".

Design decisions (see SPEC.md):

* **No real server.** The checks drive the project through
  ``fastapi.testclient.TestClient``, which exercises the real ASGI application
  in-process. Binding a port would be slower, could collide with whatever the
  user already has running, and would make the command unusable in CI.
* **Every check is isolated.** A failure in one check is recorded and the rest
  still run, so one run of ``helpui test`` reports every problem rather than
  only the first. This is what makes the command useful as a diagnostic.
* **No exceptions escape.** A missing directory, a malformed ``spec.json`` or a
  generated project with a syntax error all come back as ``ok=False`` with an
  explanatory check, so the CLI can always emit its JSON envelope and a stable
  exit code.
* **Importing the project is done in a child process.** The generated layout
  contains a real ``helpui_app`` package. Importing it in this process would
  register those modules in ``sys.modules`` permanently, so a second
  ``helpui test`` in the same interpreter would silently reuse the *first*
  project's modules and report success for the wrong directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Files every generated project must have before a smoke test is meaningful.
REQUIRED_FILES: tuple[str, ...] = ("app.py", "spec.json")

#: The package the generated project exposes.
PACKAGE_NAME = "helpui_app"


@dataclass
class Check:
    """A single smoke-test assertion."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class SelfTestResult:
    """Overall outcome of ``helpui test``."""

    ok: bool
    checks: list[Check] = field(default_factory=list)
    project_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        # `ok` is the *string* "true"/"false", matching `scan --json` and the
        # decision recorded in SPEC §6.2, so a caller can test
        # `payload["ok"] == "false"` uniformly across both commands.
        return {
            "ok": "true" if self.ok else "false",
            "project_dir": self.project_dir,
            "checks": [c.to_dict() for c in self.checks],
        }


# The probe that runs *inside* the generated project directory. It is executed
# as a child process so the generated `helpui_app` package never enters this
# interpreter's `sys.modules`.
_PROBE = r'''
import json
import sys

root = sys.argv[1]
sys.path.insert(0, root)

report = {"checks": []}


def check(name, fn):
    """Run one check, recording an exception as a failure instead of raising."""
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a smoke test must not crash
        report["checks"].append({"name": name, "ok": False,
                                 "detail": f"{type(exc).__name__}: {exc}"})
        return False
    report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


# -- spec ------------------------------------------------------------------
state = {}


def load_spec_check():
    from helpui_app.spec import load_spec

    spec = load_spec(__import__("pathlib").Path(root) / "spec.json")
    state["spec"] = spec
    return True, f"tool_name={spec.tool_name!r} commands={len(spec.commands)}"


check("spec_loads", load_spec_check)

# -- app import + boot -----------------------------------------------------
def import_check():
    from helpui_app.app import create_app

    state["create_app"] = create_app
    return True, "imported helpui_app.app"


if check("app_imports", import_check):
    def boot():
        return state["create_app"](), "create_app() returned an application"

    check("app_boots_factory", boot)

# -- HTTP checks -----------------------------------------------------------
client_holder = {}


def build_client():
    from fastapi.testclient import TestClient

    app = state["create_app"]()
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    state["app"] = app
    client_holder["client"] = client
    return True, "TestClient started"


if "create_app" in state:
    booted = check("app_boots", build_client)
else:
    booted = False

client = client_holder.get("client")


def http_get(path):
    return lambda: (client.get(path).status_code == 200,
                    f"GET {path} -> {client.get(path).status_code}")


if booted:
    check("index_responds", http_get("/"))
    check("history_responds", http_get("/history"))
    check("api_spec_responds", http_get("/api/spec"))
    check("static_assets", http_get("/static/style.css"))

    # -- command pages ------------------------------------------------------
    def form_pages():
        slugs = []
        for command in state["spec"].commands:
            name = command.name.strip().replace(" ", "_").lower() or "_root"
            status = client.get(f"/command/{name}").status_code
            slugs.append((name, status))
        bad = [s for s in slugs if s[1] != 200]
        if not slugs:
            return False, "the spec exposes no commands to render a form for"
        if bad:
            return False, f"non-200 command pages: {bad}"
        return True, f"{len(slugs)} command page(s) returned 200"

    check("form_renders", form_pages)

    # -- submit a fake job ---------------------------------------------------
    def submit_job():
        """POST a synthetic run for the first command that takes input.

        Field names come from `CLIOption.dest`, never the flag spelling: a
        positional rendered as `INPUT_FILE` is submitted as `input_file`.
        Required fields must be filled or the app correctly answers 400.
        """
        spec = state["spec"]
        candidates = [c for c in spec.commands if c.options or c.positionals]
        if not candidates:
            return None, "no command takes input; nothing to submit"

        command = candidates[0]
        slug = command.name.strip().replace(" ", "_").lower() or "_root"

        data = {}
        files = {}
        skipped = []
        for option in [*command.options, *command.positionals]:
            dest = option.dest
            if not dest or option.is_flag:
                continue
            # Only required fields are filled. Optional ones are left to the
            # target tool's own defaults: the phase 1 argparse parser types
            # `--retries` as `str`, so inventing a value for it would make the
            # tool reject the run for reasons that say nothing about the
            # generated project.
            if not option.required:
                continue
            if getattr(option, "type", "str") == "file":
                # A `file` field is only satisfiable by a multipart upload.
                files[dest] = (f"{dest}-selftest.txt", b"helpui selftest\n", "text/plain")
                continue
            if option.type == "choice" and option.choices:
                data[dest] = option.choices[0]
            elif option.type == "int":
                data[dest] = option.default or "1"
            elif option.type == "float":
                data[dest] = option.default or "1.0"
            elif option.type == "dir":
                data[dest] = "."
            else:
                data[dest] = option.default or "selftest"

        # Every generated fixture's tool demands --token, but only the click
        # parser surfaces `required`, so supply it when the spec knows about it.
        for option in command.options:
            if option.dest in {"token", "api_key", "secret"} and not option.is_flag:
                data.setdefault(option.dest, "selftest-token")

        target = f"/run/{slug}"
        response = client.post(target, data=data, files=files or None)
        state["submitted_slug"] = slug
        if response.status_code != 200:
            body = response.text[:300].replace("\n", " ")
            if skipped:
                return None, f"POST {target} -> {response.status_code} ({body}); skipped={skipped}"
            return False, f"POST {target} -> {response.status_code}: {body}"

        # The run must also have been recorded.
        page = client.get("/history")
        recorded = state["app"].state.history.count()
        if recorded < 1:
            return False, f"POST {target} -> 200 but history has {recorded} row(s)"
        return True, (f"POST {target} -> 200 with data={sorted(data)} "
                      f"files={sorted(files)}; history rows={recorded}; "
                      f"history page -> {page.status_code}")

    check("submit_job", submit_job)

    # -- history recorded ----------------------------------------------------
    def history_recorded():
        history = state["app"].state.history
        count = history.count()
        if count < 1:
            return False, f"history contains {count} runs after submitting a job"
        latest = history.list(limit=1)
        if not latest:
            return False, "history reports rows but list() returned none"
        record = latest[0]
        return True, (f"{count} run(s); latest id={record.id} "
                      f"slug={record.command_slug} status={record.status}")

    check("history_recorded", history_recorded)

    # -- API round trip ------------------------------------------------------
    def api_run():
        records = state["app"].state.history.list(limit=1)
        if not records:
            return False, "no runs recorded, cannot fetch one over the API"
        response = client.get(f"/api/runs/{records[0].id}")
        if response.status_code != 200:
            return False, f"GET /api/runs/{records[0].id} -> {response.status_code}"
        payload = response.json()
        return True, f"run {payload.get('id')} status={payload.get('status')}"

    check("api_run_roundtrip", api_run)

    try:
        client.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - never let teardown fail the command
        pass

print("REPORT" + json.dumps(report))
'''


def _check(name: str, ok: bool, detail: str) -> Check:
    return Check(name=name, ok=ok, detail=detail)


def _failed_everything(names: list[str] | tuple[str, ...], detail: str) -> list[Check]:
    """Mark every check as failed with one shared explanation.

    Used for the pre-flight failures (missing directory, bad spec) where none of
    the probes could run. Reporting the full list keeps the output shape stable,
    so a consumer always sees the same check names.
    """
    return [_check(name, False, detail) for name in names]


#: Every check name the probe reports, in order.
PROBE_CHECKS: tuple[str, ...] = (
    "spec_loads",
    "app_imports",
    "app_boots_factory",
    "app_boots",
    "index_responds",
    "history_responds",
    "api_spec_responds",
    "static_assets",
    "form_renders",
    "submit_job",
    "history_recorded",
    "api_run_roundtrip",
)


def run_project_tests(project_dir: str | Path, *, timeout: float = 60.0) -> SelfTestResult:
    """Run the smoke test suite against a generated project.

    Never raises: every failure mode -- a missing directory, an unreadable
    ``spec.json``, a generated project that will not import, a check that times
    out -- is reported as ``ok=False`` with an explanatory check, so
    ``helpui test`` can always print its JSON envelope and exit 1.

    ``timeout`` bounds the child process that drives the project.
    """
    target = Path(project_dir).expanduser()
    result = SelfTestResult(ok=False, project_dir=str(target))

    # -- pre-flight: is there even a project here? --------------------------
    if not target.exists():
        result.checks = _failed_everything(
            ["project_exists", *PROBE_CHECKS],
            f"{target} does not exist",
        )
        return result

    if not target.is_dir():
        result.checks = _failed_everything(
            ["project_exists", *PROBE_CHECKS],
            f"{target} is not a directory",
        )
        return result

    missing = [name for name in REQUIRED_FILES if not (target / name).is_file()]
    if missing:
        result.checks = [
            _check("project_exists", False, f"missing required file(s): {', '.join(missing)}"),
            *_failed_everything(PROBE_CHECKS, "not run: the project is incomplete"),
        ]
        return result

    result.checks = [_check("project_exists", True, f"{target} contains {' and '.join(REQUIRED_FILES)}")]

    # -- drive the project in a child process -------------------------------
    env = dict(os.environ)
    # The generated project prints box-drawing characters and may echo non-ASCII
    # tool output; the default Windows console encoding (GBK) would corrupt it.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("NO_COLOR", "1")

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE, str(target)],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result.checks.extend(
            _failed_everything(PROBE_CHECKS, f"the smoke test exceeded its {timeout:g}s timeout")
        )
        return result
    except OSError as exc:  # pragma: no cover - interpreter missing
        result.checks.extend(
            _failed_everything(PROBE_CHECKS, f"could not start the test process: {exc}")
        )
        return result

    payload = _parse_probe_output(completed.stdout)
    if payload is None:
        detail = (
            f"the project could not be tested (child exit {completed.returncode})"
        )
        tail = (completed.stderr or "").strip().splitlines()
        if tail:
            detail = f"{detail}: {tail[-1]}"
        result.checks.extend(_failed_everything(PROBE_CHECKS, detail))
        return result

    reported = {item["name"]: item for item in payload.get("checks", []) if isinstance(item, dict)}
    for name in PROBE_CHECKS:
        item = reported.get(name)
        if item is None:
            result.checks.append(_check(name, False, "check did not run"))
            continue
        result.checks.append(_check(name, bool(item.get("ok")), str(item.get("detail", ""))))

    result.ok = all(check.ok for check in result.checks)
    return result


def _parse_probe_output(stdout: str) -> dict[str, Any] | None:
    """Extract the probe's JSON report, tolerating stray output before it."""
    for line in reversed(stdout.splitlines()):
        if line.startswith("REPORT"):
            try:
                loaded: object = json.loads(line[len("REPORT") :])
            except json.JSONDecodeError:
                return None
            if isinstance(loaded, dict) and isinstance(loaded.get("checks"), list):
                return loaded
            return None
    return None

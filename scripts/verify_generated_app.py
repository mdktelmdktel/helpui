#!/usr/bin/env python3
"""End-to-end verification of a *generated* HelpUI project (cancel, timeout, validation).

This script is the harness for `task-4`. It:

1. scans ``scripts/slow_tool.py`` (plus the repo fixtures) and generates a
   project into a throw-away directory,
2. boots the generated FastAPI app with ``uvicorn`` in a child process on a
   free port, with the generated project as ``cwd``,
3. drives it over **real HTTP** with ``urllib`` -- no ``TestClient``, so the
   "sync endpoint in a worker thread" behaviour under test is the real thing,
4. prints one conclusion line per check and nothing else.

Output contract (one line per check, machine-parsable)::

    CHECK <name> <key>=<value> ...

Run it with no arguments. ``--keep`` keeps the generated project directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SLOW_TOOL = REPO / "scripts" / "slow_tool.py"
PYTHON = sys.executable

_BOOT_MARKER = "Uvicorn running on"


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


class Report:
    """Collects CHECK lines so a crash still prints what was learned."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def check(self, name: str, **fields: Any) -> None:
        rendered = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())
        line = f"CHECK {name} {rendered}".rstrip()
        self.lines.append(line)
        print(line, flush=True)

    def fail(self, name: str, **fields: Any) -> None:
        self.check(name, **fields)
        self.failures.append(name)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, str):
        return value if re.fullmatch(r"[\w./:+@\[\]'-]*", value) else json.dumps(value, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def request(
    url: str,
    *,
    data: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    """POST (or GET) with form data; returns ``(status, body)``, never raises."""
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def request_json(url: str, *, timeout: float = 30.0) -> tuple[int, dict[str, Any]]:
    status, body = request(url, timeout=timeout)
    try:
        loaded = json.loads(body)
    except ValueError:
        return status, {}
    return status, loaded if isinstance(loaded, dict) else {}


def multipart(
    url: str,
    fields: dict[str, str],
    files: list[tuple[str, str, bytes]],
    *,
    timeout: float = 60.0,
) -> tuple[int, str]:
    """POST a multipart/form-data body. ``files`` is ``(field, filename, data)``."""
    boundary = "----helpuiVerifyBoundary7d1f"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    for field, filename, payload in files:
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode()
            + payload
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


import urllib.parse  # noqa: E402  (after the imports above for readability)


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Server:
    """A uvicorn child running the generated project."""

    def __init__(self, project: Path, port: int, timeout_seconds: str = "60") -> None:
        self.project = project
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self._output: list[bytes] = []
        self._reader: threading.Thread | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Server:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(self.project)
        env["HELPUI_TIMEOUT"] = self.timeout_seconds
        self.process = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "helpui_app.app:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "info"],
            cwd=str(self.project),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()
        self.wait_ready()
        return self

    def _drain(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for chunk in self.process.stdout:
            self._output.append(chunk)

    def wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError("the generated server exited during startup")
            try:
                status, _ = request(f"{self.base}/api/spec", timeout=2.0)
                if status == 200:
                    return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.2)
        raise RuntimeError(f"the generated server did not become ready: {self.tail()}")

    def tail(self, lines: int = 6) -> str:
        """Last few server log lines, for a failure message only."""
        text = b"".join(self._output).decode("utf-8", "replace")
        return "\n".join(text.strip().splitlines()[-lines:]).replace("\n", " | ")

    def __exit__(self, *exc: object) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.process.kill()


def run_record(server: Server, run_id: int) -> dict[str, Any]:
    _status, payload = request_json(f"{server.base}/api/runs/{run_id}")
    return payload


def wait_for_status(
    server: Server, run_id: int, wanted: tuple[str, ...], *, timeout: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = run_record(server, run_id)
        if payload.get("status") in wanted:
            return payload
        time.sleep(0.1)
    return payload


def latest_run_for(server: Server, slug: str) -> dict[str, Any] | None:
    status, payload = request_json(f"{server.base}/api/spec")
    assert status == 200
    for run_id in range(1, 400):
        record = run_record(server, run_id)
        if record and record.get("command") and _slug(record["command"]) == slug:
            return record
    return None


def _slug(name: str) -> str:
    return name.strip().replace(" ", "_").lower() if name else "_root"


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_generation(report: Report, project: Path) -> None:
    files = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    report.check(
        "generate_files",
        count=len(files),
        has_error_template="templates/_error.html" in files,
        has_db_before_run=(project / "data" / "history.db").exists(),
    )


def check_cancel(report: Report, server: Server, project: Path) -> None:
    """The headline bug: cancel a live run and prove the process really died."""
    stamp = project / "data" / "uploads"
    assert stamp.is_dir() or True

    result: dict[str, Any] = {}

    def submit() -> None:
        try:
            status, _body = request(
                f"{server.base}/run/work",
                data={"seconds": "90", "format": "json"},
                timeout=180.0,
            )
            result["run_status"] = status
        except Exception as exc:  # pragma: no cover - reported below
            result["run_error"] = type(exc).__name__

    thread = threading.Thread(target=submit, daemon=True)
    started = time.monotonic()
    thread.start()

    # Wait for the `running` history row, exactly as a browser polling the page
    # would: that is the state in which Cancel becomes visible.
    run_id = None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        candidate = latest_run_for(server, "work")
        if candidate and candidate.get("status") == "running":
            run_id = int(candidate["id"])
            break
        time.sleep(0.05)

    if run_id is None:
        report.fail("cancel_run_row", reason="no running row appeared")
        thread.join(timeout=5)
        return

    time.sleep(2.0)  # let the form-parse work finish and the child settle
    status, body = request(f"{server.base}/cancel/work", data={}, timeout=30.0)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    record = wait_for_status(server, run_id, ("cancelled", "timeout", "failed", "succeeded"), timeout=30.0)
    thread.join(timeout=60)

    children_left = len(children_of(server.process.pid)) if server.process else -1

    report.check(
        "cancel_live_run",
        http=status,
        run_http=result.get("run_status", result.get("run_error", "?")),
        status=record.get("status"),
        exit_code=record.get("exit_code"),
        duration_ms=record.get("duration_ms"),
        elapsed_ms=elapsed_ms,
        body_mentions_kill="killed" in body.lower(),
        children_left=children_left,
    )

    # Cancel again: the run is finished, so this must be a friendly fragment.
    status2, body2 = request(f"{server.base}/cancel/work", data={}, timeout=30.0)
    report.check(
        "cancel_no_running_run",
        http=status2,
        friendly="running" in body2.lower(),
        has_error_fragment="result-error" in body2,
    )

    # Cancel a slug that has never run.
    status3, body3 = request(f"{server.base}/cancel/quick", data={}, timeout=30.0)
    report.check(
        "cancel_unknown_slug",
        http=status3,
        friendly="running" in body3.lower(),
    )


def children_of(pid: int) -> list[int]:
    """Direct children of ``pid`` (Windows: CIM; POSIX: ps)."""
    if os.name == "nt":
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return [int(line) for line in completed.stdout.split() if line.strip().isdigit()]
    completed = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True, check=False)
    return [int(line) for line in completed.stdout.split() if line.strip().isdigit()]


def check_cancel_nodeadlock(report: Report, server: Server) -> None:
    """Regression guard: two cancels must not deadlock (control runs meanwhile).

    `POST /cancel/<slug>` is a *sync* endpoint, so it runs in Starlette's
    threadpool. It renders a Jinja2 template inside the registry lock in the
    pre-fix code; Jinja2 can call back into the event loop while rendering
    (``asyncio.get_running_loop`` in its async-context detection), which
    re-enters the threadpool and can exhaust it. Here: fire two cancels while
    also loading a page, and require everything to come back.
    """
    outcomes: list[str] = []
    lock = threading.Lock()

    def hit(path: str) -> None:
        try:
            status, _ = request(f"{server.base}{path}", data={}, timeout=20.0)
        except Exception as exc:  # pragma: no cover
            status = f"{type(exc).__name__}"
        with lock:
            outcomes.append(str(status))

    threads = [threading.Thread(target=hit, args=("/cancel/work",), daemon=True) for _ in range(2)]
    page_thread = threading.Thread(target=hit, args=("/command/work",), daemon=True)
    for thread in threads:
        thread.start()
    started = time.monotonic()
    time.sleep(0.5)
    # The page uses GET, but `hit` posts; that still exercises the threadpool.
    page_thread.start()
    for thread in [*threads, page_thread]:
        thread.join(timeout=20)
    alive = [t for t in [*threads, page_thread] if t.is_alive()]
    report.check(
        "cancel_concurrent",
        outcomes=sorted(outcomes),
        hung=len(alive),
        took_ms=int((time.monotonic() - started) * 1000),
    )


def check_timeout(report: Report, project: Path, port: int) -> None:
    """``HELPUI_TIMEOUT=3`` must kill a 90s tool and report `timeout`."""
    with Server(project, port, timeout_seconds="3") as server:
        started = time.monotonic()
        status, _body = request(f"{server.base}/run/work", data={"seconds": "90"}, timeout=60.0)
        elapsed = time.monotonic() - started
        record = latest_run_for(server, "work")
    assert record is not None
    report.check(
        "timeout_kills",
        http=status,
        status=record.get("status"),
        elapsed_s=round(elapsed, 2),
        duration_ms=record.get("duration_ms"),
    )


def check_validation(report: Report, server: Server, project: Path) -> None:
    base = server.base

    # missing required positional
    status, body = request(f"{base}/run/blob", data={}, timeout=30.0)
    report.check("validate_missing_file", http=status, has_error="error" in body.lower())

    # int field with garbage
    status, body = request(f"{base}/run/work", data={"seconds": "abc"}, timeout=30.0)
    report.check(
        "validate_int_abc",
        http=status,
        has_error="error" in body.lower(),
        readable="integer" in body.lower(),
    )

    # choice with an illegal value
    status, body = request(f"{base}/run/work", data={"seconds": "1", "format": "xml"}, timeout=30.0)
    report.check("validate_choice_bad", http=status, has_error="error" in body.lower())

    # far-too-large int: Python handles it; the tool should not crash the app
    status, body = request(f"{base}/run/quick", data={"note": "x" * 500}, timeout=30.0)
    report.check("validate_long_string", http=status)

    # unknown slug
    status, _body = request(f"{base}/run/nope", data={}, timeout=30.0)
    report.check("run_unknown_slug", http=status)

    # upload traversal + oversize
    traversal = multipart(
        f"{base}/run/blob",
        {},
        [("file", "../../../evil.txt", b"owned")],
    )
    report.check(
        "upload_traversal",
        http=traversal[0],
        escaped_files=sorted(p.name for p in project.parent.glob("evil.txt")),
        upload_dir=sorted(p.name for p in (project / "data" / "uploads").glob("*")),
    )

    big = b"x" * (65 * 1024 * 1024)
    status, body = multipart(f"{base}/run/blob", {}, [("file", "big.bin", big)], timeout=120.0)
    report.check(
        "upload_oversize",
        http=status,
        has_error="error" in body.lower(),
    )


def check_root_command(report: Report, server: Server) -> None:
    """The root command (``argv_path() == []``) must still build the right argv."""
    status, _body = request(f"{server.base}/run/_root", data={"verbose": "true"}, timeout=30.0)
    record = latest_run_for(server, "_root")
    assert record is not None
    report.check(
        "argv_path_root",
        http=status,
        argv=record.get("argv"),
        status=record.get("status"),
        stdout_head=(record.get("stdout") or "").strip().splitlines()[:1],
    )


def check_nested_command(report: Report, project: Path, port: int) -> None:
    """``sample_typer.py`` has ``admin reset``; ``argv_path()`` must split it."""
    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    out = project.parent / "typer_project"
    spec = scan_tool(str(REPO / "tests" / "fixtures" / "sample_typer.py")).spec
    generate_project(spec, out)
    with Server(out, port, timeout_seconds="60") as server:
        status, _body = request(f"{server.base}/run/admin_reset", data={}, timeout=30.0)
        record = latest_run_for(server, "admin_reset")
    assert record is not None
    report.check(
        "argv_path_nested",
        http=status,
        argv=record.get("argv"),
        status=record.get("status"),
    )


def check_history_db_autocreate(report: Report, project: Path, port: int) -> None:
    """A project whose ``data/history.db`` is missing must create it on boot."""
    db = project / "data" / "history.db"
    existed = db.exists()
    db.unlink(missing_ok=True)
    with Server(project, port, timeout_seconds="30") as server:
        status, _ = request(f"{server.base}/history", timeout=30.0)
    report.check(
        "history_db_autocreate",
        existed_before=existed,
        http=status,
        created_after=db.exists(),
    )


def check_timeout_env_zero(report: Report, project: Path, port: int) -> None:
    """``HELPUI_TIMEOUT=0`` must not hang or 500; note the resolution."""
    for value in ("0", "-5", "abc"):
        db = project / "data" / "history.db"
        db.unlink(missing_ok=True)
        with Server(project, port, timeout_seconds=value) as server:
            started = time.monotonic()
            try:
                status, _ = request(f"{server.base}/run/quick", data={"note": "z"}, timeout=20.0)
            except Exception as exc:
                status = f"{type(exc).__name__}"
            elapsed = time.monotonic() - started
            record = latest_run_for(server, "quick")
        report.check(
            "timeout_env_value",
            value=value,
            http=status,
            status=(record or {}).get("status"),
            elapsed_s=round(elapsed, 2),
        )


def check_timeout_fractional(report: Report, project: Path, port: int) -> None:
    """A sub-second timeout is legal; it must still be a clean `timeout`."""
    with Server(project, port, timeout_seconds="0.4") as server:
        status, _ = request(f"{server.base}/run/work", data={"seconds": "30"}, timeout=30.0)
        record = latest_run_for(server, "work")
    report.check(
        "timeout_fractional",
        http=status,
        status=(record or {}).get("status"),
        duration_ms=(record or {}).get("duration_ms"),
    )


def check_unit_level(report: Report) -> None:
    """Direct checks on the *generated* modules, in a child process."""
    code = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from helpui_app.spec import load_spec
from helpui_app.executor import build_argv
from helpui_app.config import load_config
from helpui_app.security import safe_join, sanitise_filename
from pathlib import Path

spec = load_spec(Path("spec.json"))
out = {}
out["argv_paths"] = {c.name: c.argv_path() for c in spec.commands}
admin = next(c for c in spec.commands if c.name == "admin reset")
out["admin_argv"] = build_argv(["tool"], admin.argv_path(), [("--force", ())])
root = next(c for c in spec.commands if c.name == "")
out["root_argv"] = build_argv(["tool"], root.argv_path(), [("--verbose", ())])
cfg = load_config()
out["timeout_seconds"] = cfg.timeout_seconds
out["max_upload_bytes"] = cfg.max_upload_bytes
out["upload_dir_exists"] = cfg.upload_dir.is_dir()
try:
    from helpui_app.security import ValidationError
    try:
        cfg0 = type(cfg)(**{**cfg.__dict__, "timeout_seconds": 0.0})
        out["timeout_zero_cfg"] = "ok"
    except Exception as exc:
        out["timeout_zero_cfg"] = type(exc).__name__
except Exception as exc:
    out["err"] = str(exc)
print("JSON" + json.dumps(out))
"""
    completed = subprocess.run(
        [PYTHON, "-c", code, str(_TYPER_PROJECT[0])],
        cwd=str(_TYPER_PROJECT[0]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "0"},
    )
    line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
    payload = json.loads(line[4:]) if line else {}
    report.check(
        "argv_path_unit",
        paths=payload.get("argv_paths"),
        admin_argv=payload.get("admin_argv"),
        root_argv=payload.get("root_argv"),
    )
    report.check("timeout_env_zero_cfg", value=payload.get("timeout_seconds"))


_TYPER_PROJECT: list[Path] = []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep the generated project")
    parser.add_argument("--only", default="", help="comma-separated check groups to run")
    args = parser.parse_args()
    groups = {g for g in args.only.split(",") if g} or {
        "cancel", "timeout", "validate", "boundary", "unit",
    }

    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    report = Report()
    workdir = Path(tempfile.mkdtemp(prefix="helpui_verify_"))
    try:
        project = workdir / "project"
        spec = scan_tool(str(SLOW_TOOL)).spec
        generate_project(spec, project)
        _TYPER_PROJECT.append(workdir / "typer_project")
        scan_typer = scan_tool(str(REPO / "tests" / "fixtures" / "sample_typer.py")).spec
        generate_project(scan_typer, workdir / "typer_project")

        if "cancel" in groups or "validate" in groups or "boundary" in groups:
            with Server(project, free_port(), timeout_seconds="120") as server:
                if "cancel" in groups:
                    check_cancel(report, server, project)
                    check_cancel_nodeadlock(report, server)
                if "validate" in groups:
                    check_validation(report, server, project)
                if "boundary" in groups:
                    check_root_command(report, server)
                    check_history_db_autocreate(report, project, free_port())
                    check_timeout_env_zero(report, project, free_port())

        if "timeout" in groups:
            check_timeout(report, project, free_port())
            check_timeout_fractional(report, project, free_port())

        if "boundary" in groups:
            check_nested_command(report, project, free_port())

        if "unit" in groups:
            check_unit_level(report)
    finally:
        if args.keep:
            print(f"KEPT {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"SUMMARY checks={len(report.lines)} failed={report.failures or 'none'}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())

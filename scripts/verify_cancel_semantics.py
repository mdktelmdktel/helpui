#!/usr/bin/env python3
"""Task-4 verification: cancel semantics + the event-loop probe.

Covers:
  * cancel a live run: HTTP status, resulting history status, exit code, and
    whether the child process is really gone (checked via its own PID, read
    from the tool's stdout so we are not guessing);
  * the concurrency probe the leader asked for (routes stay responsive while a
    long run is in flight);
  * cancel with nothing running / an unknown slug -> friendly fragment, not 500.

Prints conclusion lines only.
"""

from __future__ import annotations

import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_generated_app import Server, free_port, request  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

from helpui.generator import generate_project  # noqa: E402
from helpui.scanner_service import scan_tool  # noqa: E402

project = Path(sys.argv[1])
generate_project(scan_tool(str(REPO / "scripts" / "slow_tool.py")).spec, project, force=True)


def record(server: Server, run_id: int) -> dict:
    code, body = request(f"{server.base}/api/runs/{run_id}", timeout=8)
    return json.loads(body) if code == 200 else {}


def _child_pid(parent: int) -> int | None:
    """The sleep child's PID, found via the parent's children (not guessed)."""
    import os
    import subprocess

    if parent <= 0:
        return None
    if os.name == "nt":
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={parent}' | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        pids = [int(x) for x in completed.stdout.split() if x.strip().isdigit()]
        return pids[0] if pids else None
    completed = subprocess.run(
        ["ps", "-o", "pid=", "--ppid", str(parent)], capture_output=True, text=True, check=False
    )
    pids = [int(x) for x in completed.stdout.split() if x.strip().isdigit()]
    return pids[0] if pids else None


with Server(project, free_port(), timeout_seconds="120") as server:
    holder: dict = {}

    def submit() -> None:
        try:
            code, _ = request(f"{server.base}/run/work", data={"seconds": "90"}, timeout=180)
            holder["run_http"] = code
        except Exception as exc:
            holder["run_err"] = type(exc).__name__

    thread = threading.Thread(target=submit, daemon=True)
    thread.start()

    # ---- concurrency probe: loop stays free while the tool runs ------------
    time.sleep(1.5)
    latencies: list[float] = []
    statuses: list[int] = []
    for _ in range(10):
        started = time.monotonic()
        try:
            code, _ = request(f"{server.base}/api/runs/1", timeout=6)
        except Exception:
            code = 0
        latencies.append((time.monotonic() - started) * 1000)
        statuses.append(code)
        time.sleep(0.1)
    inflight = thread.is_alive()
    print(
        f"concurrent_probe: run_in_flight={inflight} api_runs_status={statuses[-1]} "
        f"latency_ms={round(statistics.median(latencies))} max_ms={round(max(latencies))} "
        f"ok={statuses.count(200)}/{len(statuses)}"
    )

    # ---- cancel the live run ----------------------------------------------
    before = record(server, 1)
    child_pid = _child_pid(server.process.pid if server.process else 0)
    started = time.monotonic()
    code, body = request(f"{server.base}/cancel/work", data={}, timeout=30)
    cancel_ms = int((time.monotonic() - started) * 1000)

    # Wait for the run thread to return and the row to settle.
    thread.join(timeout=40)
    after = record(server, 1)
    time.sleep(0.5)
    child_after = _child_pid(server.process.pid if server.process else 0)

    print(
        f"cancel: http={code} status={after.get('status')} exit_code={after.get('exit_code')} "
        f"duration_ms={after.get('duration_ms')} cancel_ms={cancel_ms} "
        f"child_before={child_pid} child_after={child_after} killed={child_after is None} "
        f"says_killed={'killed' in body.lower()}"
    )
    print(f"cancel_run_http: {holder.get('run_http', holder.get('run_err'))}")

    # ---- cancel with nothing running --------------------------------------
    code2, body2 = request(f"{server.base}/cancel/work", data={}, timeout=15)
    print(f"cancel_no_run: http={code2} friendly={'running' in body2.lower()} fragment={'result-error' in body2}")

    code3, body3 = request(f"{server.base}/cancel/quick", data={}, timeout=15)
    print(f"cancel_unknown_slug: http={code3} friendly={'running' in body3.lower()}")

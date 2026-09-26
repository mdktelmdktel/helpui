#!/usr/bin/env python3
"""Task-4 verification, fully in-process (``TestClient`` + threads, no uvicorn).

Proves the two things the leader asked for:

1. ``POST /cancel/<slug>`` no longer freezes the server: while a long run is in
   flight, ``/api/runs/*`` and ``/history`` keep answering.
2. cancel semantics: the child really dies, and the history row lands on
   ``cancelled`` with a sane ``duration_ms``.

Runs the generated app in a **child process** (so the generated ``helpui_app``
package never lands in this interpreter's ``sys.modules``), but inside that child
there is no uvicorn -- just ``TestClient`` and threads.

Prints conclusion lines only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

PROBE = r'''
import json, statistics, sys, threading, time
from pathlib import Path

from fastapi.testclient import TestClient
from helpui_app.app import create_app

app = create_app()
out = {}

with TestClient(app, raise_server_exceptions=False) as client:
    holder = {}

    def submit():
        holder["http"] = client.post("/run/work", data={"seconds": "90"}).status_code

    thread = threading.Thread(target=submit, daemon=True)
    thread.start()

    # Wait until the run row exists and is `running`.
    run_id = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        payload = client.get("/api/runs/1")
        if payload.status_code == 200 and payload.json()["status"] == "running":
            run_id = payload.json()["id"]
            break
        time.sleep(0.05)
    out["run_id"] = run_id

    # --- the leader's concurrency probe -----------------------------------
    latencies, statuses = [], []
    for _ in range(10):
        started = time.monotonic()
        response = client.get(f"/api/runs/{run_id}")
        latencies.append((time.monotonic() - started) * 1000)
        statuses.append(response.status_code)
        time.sleep(0.05)
    hist_ms = None
    started = time.monotonic()
    hist_status = client.get("/history").status_code
    hist_ms = (time.monotonic() - started) * 1000
    out["probe"] = {
        "in_flight": thread.is_alive(),
        "status": statuses[-1],
        "median_ms": round(statistics.median(latencies)),
        "max_ms": round(max(latencies)),
        "ok": statuses.count(200),
        "n": len(statuses),
        "history_status": hist_status,
        "history_ms": round(hist_ms),
    }

    # --- cancel the live run ----------------------------------------------
    child_pid = None
    registry = app.state.registry
    with registry._lock:
        controller = registry._controllers.get(run_id)
    if controller is not None:
        with controller._lock:
            process = controller._process
        child_pid = process.pid if process is not None else None

    started = time.monotonic()
    cancelled = client.post("/cancel/work")
    cancel_ms = (time.monotonic() - started) * 1000

    thread.join(timeout=40)
    record = client.get(f"/api/runs/{run_id}").json()
    out["cancel"] = {
        "http": cancelled.status_code,
        "run_http": holder.get("http"),
        "status": record["status"],
        "exit_code": record["exit_code"],
        "duration_ms": record["duration_ms"],
        "cancel_ms": round(cancel_ms),
        "says_killed": "killed" in cancelled.text.lower(),
        "child_pid": child_pid,
    }

    # --- cancel with nothing running --------------------------------------
    again = client.post("/cancel/work")
    out["cancel_no_run"] = {
        "http": again.status_code,
        "friendly": "running" in again.text.lower(),
        "fragment": "result-error" in again.text,
    }
    unknown = client.post("/cancel/quick")
    out["cancel_unknown_slug"] = {"http": unknown.status_code, "friendly": "running" in unknown.text.lower()}

print("JSON" + json.dumps(out))
'''


def main() -> int:
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    tmp = Path(tempfile.mkdtemp(prefix="helpui_t4_"))
    project = tmp / "project"
    generate_project(scan_tool(str(REPO / "scripts" / "_slow_tool.py")).spec, project)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "120"}
    completed = subprocess.run(
        [PYTHON, "-c", PROBE],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
        check=False,
    )
    line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
    if not line:
        print("FAIL probe produced no JSON")
        print(f"FAIL stderr_tail={completed.stderr.strip().splitlines()[-1][:160] if completed.stderr.strip() else 'none'}")
        return 1

    payload = json.loads(line[4:])
    probe = payload["probe"]
    cancel = payload["cancel"]
    again = payload["cancel_no_run"]
    unknown = payload["cancel_unknown_slug"]

    print(
        f"concurrent_probe: run_in_flight={probe['in_flight']} api_runs_status={probe['status']} "
        f"latency_ms={probe['median_ms']} max_ms={probe['max_ms']} ok={probe['ok']}/{probe['n']} "
        f"history_status={probe['history_status']} history_ms={probe['history_ms']}"
    )
    print(
        f"cancel_live_run: http={cancel['http']} run_http={cancel['run_http']} status={cancel['status']} "
        f"exit_code={cancel['exit_code']} duration_ms={cancel['duration_ms']} "
        f"cancel_ms={cancel['cancel_ms']} killed={cancel['says_killed']}"
    )
    print(f"cancel_child_dead: child_pid={cancel['child_pid']} alive={_alive(cancel['child_pid'])}")
    print(f"cancel_no_running: http={again['http']} friendly={again['friendly']} fragment={again['fragment']}")
    print(f"cancel_unknown_slug: http={unknown['http']} friendly={unknown['friendly']}")

    ok = (
        probe["ok"] == probe["n"]
        and probe["max_ms"] < 2000
        and cancel["status"] == "cancelled"
        and cancel["http"] == 200
        and not _alive(cancel["child_pid"])
    )
    print(f"VERDICT {'pass' if ok else 'FAIL'}")
    return 0 if ok else 1


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    import os
    import subprocess

    if os.name == "nt":
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return bool(completed.stdout.strip())
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())

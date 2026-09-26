#!/usr/bin/env python3
"""Prove the task-4 fix is load-bearing: same probe, blocking call re-introduced.

Takes the *fixed* generator, rewrites the generated `app.py` source to put the
blocking `execute()` back on the event loop (i.e. undoes only that one change),
regenerates, and re-runs the probe. The probe must then fail.

This isolates the fix from the Lead's other generator work, which a plain
`git checkout` cannot do (the committed generator is a pre-Phase-2 stub).

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

PROBE = (REPO / "scripts" / "verify_task4.py").read_text(encoding="utf-8")


def main() -> int:
    import os
    import re

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import helpui.generator as gen
    from helpui.scanner_service import scan_tool

    # Undo ONLY the threadpool offload: call the blocking function directly.
    original = gen._fastapi_app_source
    patched_source = original()

    blocking_call = (
        "outcome = execute(cfg, tool, command.argv_path(), values, controller=controller)"
    )
    threadpool_call_marker = "await run_in_threadpool(\n                execute, cfg, tool, command.argv_path(), values, controller=controller\n            )"
    if threadpool_call_marker not in patched_source:
        print("SKIP could not find the threadpool call in the generated source")
        return 2

    def blocking_source() -> str:
        text = original()
        text = text.replace(threadpool_call_marker, blocking_call)
        return text

    gen._fastapi_app_source = blocking_source  # type: ignore[assignment]

    tmp = Path(tempfile.mkdtemp(prefix="helpui_t4_baseline_"))
    project = tmp / "project"
    gen.generate_project(scan_tool(str(REPO / "scripts" / "_slow_tool.py")).spec, project)

    probe_code = PROBE.split('PROBE = r\'\'\'', 1)[1].split("'''", 1)[0]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "120"}
    completed = subprocess.run(
        [PYTHON, "-c", probe_code],
        cwd=str(project),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, check=False,
    )
    line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
    if not line:
        print("BASELINE probe produced no JSON (server was likely frozen)")
        return 1
    payload = json.loads(line[4:])
    probe = payload["probe"]
    print(
        f"baseline_concurrent_probe: run_in_flight={probe['in_flight']} "
        f"api_runs_status={probe['status']} latency_ms={probe['median_ms']} "
        f"max_ms={probe['max_ms']} ok={probe['ok']}/{probe['n']}"
    )
    print(f"baseline_cancel_ms={payload['cancel']['cancel_ms']} status={payload['cancel']['status']}")
    print(f"BASELINE {'blocked_as_expected' if probe['ok'] < probe['n'] else 'NOT blocked (fix not load-bearing!)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

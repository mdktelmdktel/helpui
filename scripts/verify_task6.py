#!/usr/bin/env python3
"""Task-6 verification, in-process (``TestClient``, no uvicorn).

Covers the required conclusion lines:
  file_positional_upload / file_positional_missing / upload_size_limit /
  upload_traversal / no_regression_text_positional / options_upload_still_works

The generated app runs in a child process so the generated ``helpui_app``
package never enters this interpreter's ``sys.modules``.

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
import json, sys
from fastapi.testclient import TestClient
from helpui_app.app import create_app

app = create_app()
out = {}

# Field names are read from the generated spec rather than guessed: a positional
#'s dest comes from its metavar ("FILE" -> "file"), not from the source name.
spec_commands = {c["name"]: c for c in app.state.spec.to_dict()["commands"]}
send = spec_commands["send"]
send_field = send["positionals"][0]["dest"]
blob = spec_commands["blob"]
blob_field = next(o["dest"] for o in blob["options"] if o["type"] == "file")
out["send_field"] = send_field
out["blob_field"] = blob_field

with TestClient(app, raise_server_exceptions=False) as client:
    # 1. file *positional* upload must succeed and reach argv as a stored path.
    r = client.post("/run/send", files={send_field: ("hello.txt", b"hello world", "text/plain")})
    out["file_positional_upload"] = {"status": r.status_code}
    if r.status_code == 200:
        run = client.get("/api/runs/1").json()
        out["file_positional_upload"]["argv"] = run["argv"]
        out["file_positional_upload"]["stdout"] = run["stdout"].strip()
        out["file_positional_upload"]["status_field"] = run["status"]

    # 2. missing required file positional -> 400 naming the field
    r = client.post("/run/send", data={})
    out["file_positional_missing"] = {"status": r.status_code, "mentions": send_field in r.text}

    # 3. file *option* upload still works (no regression from the refactor)
    r = client.post("/run/blob", files={blob_field: ("opt.txt", b"opt data", "text/plain")})
    out["options_upload"] = {"status": r.status_code}
    if r.status_code == 200:
        run = client.get("/api/runs/2").json()
        out["options_upload"]["argv"] = run["argv"]

    # 4. text positional still works (a str positional is not an upload)
    r = client.post("/run/quick", data={"note": "plain text"})
    out["text_positional"] = {"status": r.status_code}

    # 5. traversal filename must not escape upload_dir
    r = client.post("/run/send", files={send_field: ("../../evil.txt", b"pwned", "text/plain")})
    out["traversal"] = {"status": r.status_code}

print("JSON" + json.dumps(out))
'''

SIZE_PROBE = r'''
import json, os
from helpui_app.app import create_app
from fastapi.testclient import TestClient

app = create_app()
command = next(c for c in app.state.spec.to_dict()["commands"] if c["name"] == "send")
field = command["positionals"][0]["dest"]
out = {}
with TestClient(app, raise_server_exceptions=False) as client:
    cap = int(os.environ["HELPUI_MAX_UPLOAD_BYTES"])
    big = b"x" * (cap + 1024)
    r = client.post("/run/send", files={field: ("big.bin", big, "application/octet-stream")})
    out["status"] = r.status_code
    out["mentions_limit"] = "upload limit" in r.text
print("JSON" + json.dumps(out))
'''


def _run(project: Path, code: str, env_extra: dict[str, str]) -> dict:
    import os

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "60", **env_extra}
    completed = subprocess.run(
        [PYTHON, "-c", code], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, env=env, check=False,
    )
    line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
    if not line:
        return {"error": "no JSON", "stderr": completed.stderr.strip().splitlines()[-1][:160] if completed.stderr.strip() else ""}
    return json.loads(line[4:])


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    tmp = Path(tempfile.mkdtemp(prefix="helpui_t6_"))
    project = tmp / "project"
    generate_project(scan_tool(str(REPO / "scripts" / "slow_tool.py")).spec, project)

    result = _run(project, PROBE, {})
    if "error" in result:
        print(f"FAIL {result}")
        return 1

    up = result["file_positional_upload"]
    argv = up.get("argv") or []
    stored = [a for a in argv if "uploads" in a]
    print(
        f"file_positional_upload: status={up['status']} "
        f"argv_has_upload_path={bool(stored)} run_status={up.get('status_field')} "
        f"stdout={up.get('stdout','')!r}"
    )
    opt_argv = result["options_upload"].get("argv") or []
    print(
        f"options_upload_still_works: status={result['options_upload']['status']} "
        f"argv_has_upload_path={bool([a for a in opt_argv if 'uploads' in a])}"
    )
    print(f"file_positional_missing: status={result['file_positional_missing']['status']} mentions_field={result['file_positional_missing']['mentions']}")
    print(f"no_regression_text_positional: status={result['text_positional']['status']}")
    print(f"fields: send={result['send_field']!r} blob={result['blob_field']!r}")

    trav = result["traversal"]
    leaked = list(tmp.rglob("evil.txt"))
    escaped = [p for p in leaked if "uploads" not in p.parts]
    print(f"upload_traversal: status={trav['status']} rejected={not escaped} stored_in_uploads={bool(leaked)}")

    # Size limit, with a deliberately tiny cap via env.
    small = tmp / "project_small"
    generate_project(scan_tool(str(REPO / "scripts" / "slow_tool.py")).spec, small)
    sized = _run(small, SIZE_PROBE, {"HELPUI_MAX_UPLOAD_BYTES": "1024"})
    if "error" in sized:
        print(f"upload_size_limit: FAIL {sized}")
    else:
        print(f"upload_size_limit: status={sized['status']} mentions_limit={sized['mentions_limit']}")

    ok = (
        up["status"] == 200
        and bool(stored)
        and up.get("status_field") == "succeeded"
        and result["file_positional_missing"]["status"] == 400
        and result["options_upload"]["status"] == 200
        and result["text_positional"]["status"] == 200
        and not escaped
        and sized.get("status") == 400
    )
    print(f"VERDICT {'pass' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

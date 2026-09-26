#!/usr/bin/env python3
"""Task-6: browser-realistic check on the three real fixtures.

For each fixture's `convert` command, submit exactly what the rendered form
would submit: text for text fields, an upload for `file`-typed fields. Reports
per-fixture status and whether argv carried the stored upload path.

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
spec = json.loads(open("spec.json", encoding="utf-8").read())
convert = next(c for c in spec["commands"] if c["name"] == "convert")

def value_for(o):
    if o["type"] == "choice" and o["choices"]:
        return o["choices"][0]
    if o["type"] == "int":
        return "7"
    if o["type"] == "float":
        return "1.5"
    if o["type"] == "dir":
        return "."
    return "probe-value"

fields = {}
files = {}
for p in convert["positionals"]:
    if p["type"] == "file":
        files[p["dest"]] = (f"{p['dest']}.txt", b"payload", "text/plain")
    else:
        fields[p["dest"]] = "probe-input.txt"
for o in convert["options"]:
    if o["is_flag"]:
        continue
    if o["type"] == "file":
        files[o["dest"]] = (f"{o['dest']}.txt", b"payload", "text/plain")
    elif o["required"] or o["dest"] in {"token", "api_key", "secret"}:
        fields[o["dest"]] = value_for(o)

out = {"file_fields": sorted(files), "text_fields": sorted(fields)}
with TestClient(app, raise_server_exceptions=False) as client:
    r = client.post("/run/convert", data=fields, files=files)
    out["status"] = r.status_code
    if r.status_code == 200:
        run = client.get("/api/runs/1").json()
        out["run_status"] = run["status"]
        out["argv"] = run["argv"]
print("JSON" + json.dumps(out))
'''


def main() -> int:
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    tmp = Path(tempfile.mkdtemp(prefix="helpui_t6fx_"))
    failures = []
    for kind in ("argparse", "click", "typer"):
        project = tmp / kind
        generate_project(scan_tool(str(REPO / "tests" / "fixtures" / f"sample_{kind}.py")).spec, project)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "60"}
        completed = subprocess.run(
            [PYTHON, "-c", PROBE], cwd=str(project), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, env=env, check=False,
        )
        line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
        if not line:
            print(f"{kind}: FAIL no JSON")
            failures.append(kind)
            continue
        payload = json.loads(line[4:])
        argv = payload.get("argv") or []
        upload_ok = any("uploads" in a for a in argv)
        status = payload["status"]
        print(
            f"{kind:9}: status={status} run_status={payload.get('run_status')} "
            f"file_fields={payload['file_fields']} argv_has_upload={upload_ok}"
        )
        if status != 200 or payload.get("run_status") != "succeeded":
            failures.append(kind)

    print(f"VERDICT {'pass' if not failures else 'fail:' + ','.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

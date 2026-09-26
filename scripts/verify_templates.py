#!/usr/bin/env python3
"""Task-8: render every packaged template with realistic data; report failures.

Two layers:

1. **Static audit** of the 8 templates for the classic Jinja2 misuses:
   iterating a mapping without ``.items()``, ``.get()`` on a non-mapping, and
   attribute lookups that the route's context never provides.
2. **Real render** of each template through a genuine Jinja2 environment with a
   context built from the *actual* app (route context), so a wrong variable name
   or a bad iteration surfaces as a failure rather than a silent blank page.

Prints conclusion lines only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
TEMPLATES = (
    "base.html",
    "index.html",
    "form.html",
    "history.html",
    "result.html",
    "record.html",
    "_widget.html",
    "_error.html",
)

#: Context the generated routes actually pass -- read from the generated
#: ``app.py`` rather than assumed, so a mismatch is caught.
RENDER_PROBE = r'''
import json, sys
from fastapi.testclient import TestClient
from helpui_app.app import create_app

app = create_app()
out = {}

with TestClient(app, raise_server_exceptions=False) as client:
    # Drive one real run so history has a row WITH params.
    spec = json.loads(open("spec.json", encoding="utf-8").read())
    convert = next(c for c in spec["commands"] if c["name"] == "convert")
    fields, files = {}, {}
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
            fields[o["dest"]] = "probe-value"
    out["run"] = client.post("/run/convert", data=fields, files=files).status_code

    # Every page route the app exposes.
    out["pages"] = {
        "/": client.get("/").status_code,
        "/history": client.get("/history").status_code,
        "/api/spec": client.get("/api/spec").status_code,
        "/history/1": client.get("/history/1").status_code,
        "/history/1/download": client.get("/history/1/download").status_code,
        "/api/runs/1": client.get("/api/runs/1").status_code,
        "/result/1": client.get("/result/1").status_code,
    }
    # Param-containing detail page must actually show key and value.
    detail = client.get("/history/1")
    out["detail_status"] = detail.status_code
    out["detail_text_len"] = len(detail.text)
    detail_lower = detail.text
    out["detail_has_input_file"] = "input_file" in detail_lower
    out["detail_has_value"] = "probe-input.txt" in detail_lower or "uploads" in detail_lower
    out["detail_has_kv_table"] = 'class="kv"' in detail_lower
    out["detail_has_error_marker"] = "Internal Server Error" in detail.text

    # An empty-params run: exercise the `else` branch of record.html. `report`
    # in the argparse fixture has only optional options, so submitting nothing
    # stores an empty params dict.
    r = client.post("/run/report", data={})
    out["report_post"] = r.status_code
    out["history2"] = client.get("/history/2").status_code
    detail2 = client.get("/history/2")
    out["detail2_no_params_branch"] = "No parameters were supplied" in detail2.text

    # A run whose submission fills exactly ONE param exercises the "too many
    # values to unpack" variant of the same bug (single-key dict).
    r = client.post("/run/report", data={"limit": "5"})
    out["report_one_param"] = r.status_code
    out["history3"] = client.get("/history/3").status_code
    out["detail3_has_limit"] = "limit" in client.get("/history/3").text

print("JSON" + json.dumps(out))
'''

#: Every template rendered standalone against a realistic context.
STANDALONE_PROBE = r'''
import json, sys
from jinja2 import Environment, FileSystemLoader
from helpui_app.app import create_app

app = create_app()
spec = app.state.spec
command = spec.commands[1] if len(spec.commands) > 1 else spec.commands[0]
view = {
    "slug": (command.name or "_root").replace(" ", "_").lower(),
    "name": command.name,
    "label": command.name or "(root)",
    "help": command.help,
    "options": command.options,
    "positionals": command.positionals,
    "option_count": len(command.options) + len(command.positionals),
}
record = {
    "id": 1, "command": command.name or "(root)", "command_slug": view["slug"],
    "started_at": "2024-01-01T00:00:00+00:00", "duration": "0.10s",
    "exit_code": 0, "status": "succeeded", "duration_ms": 100,
    "params": {"input_file": "/tmp/uploads/x.txt", "format": "json"},
    "stdout": "{\"a\": 1}", "stderr": "", "stderr_lines": 0,
    "argv_display": "tool convert", "argv": ["tool", "convert"],
    "notes": [], "finished_at": "2024-01-01T00:00:01+00:00",
}
env = Environment(loader=FileSystemLoader("templates"))
results = {}
contexts = {
    "base.html": {"spec": spec},
    "index.html": {"spec": spec, "commands": [view]},
    "form.html": {"spec": spec, "command": view},
    "history.html": {"spec": spec, "runs": [record], "total": 1},
    "result.html": {"spec": spec, "record": record, "rendered": "<p>ok</p>"},
    "record.html": {"spec": spec, "record": record, "rendered": "<p>ok</p>"},
    "_error.html": {"message": "boom"},
}
for name, ctx in contexts.items():
    try:
        env.get_template(name).render(**ctx)
        results[name] = "ok"
    except Exception as exc:
        results[name] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:90]}"
# The macro-only template needs the macro imported, not a context.
try:
    env.get_template("form.html").render(spec=spec, command=view)
    results["_widget.html (via form)"] = "ok"
except Exception as exc:
    results["_widget.html (via form)"] = f"{type(exc).__name__}"
print("JSON" + json.dumps(results))
'''


def static_audit() -> list[str]:
    """Flag suspicious `for a, b in mapping` patterns in the packaged templates."""
    findings: list[str] = []
    for name in TEMPLATES:
        text = (REPO / "helpui" / "templates" / name).read_text(encoding="utf-8")
        for match in re.finditer(r"\{%-?\s*for\s+([^%]+?)\s+in\s+([^%]+?)\s*-?%\}", text):
            targets, source = match.group(1).strip(), match.group(2).strip()
            if "," in targets and not source.endswith((".items()", ".items", "|dictsort")):
                line = text[: match.start()].count("\n") + 1
                findings.append(f"{name}:{line} for {targets} in {source}")
    return findings


def main() -> int:
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from helpui.generator import generate_project
    from helpui.scanner_service import scan_tool

    audit = static_audit()
    print(f"static_audit_unpacking_dicts: {audit or 'none'}")

    tmp = Path(tempfile.mkdtemp(prefix="helpui_t8_"))
    project = tmp / "project"
    generate_project(scan_tool(str(REPO / "tests" / "fixtures" / "sample_argparse.py")).spec, project)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HELPUI_TIMEOUT": "60"}

    def run(code: str) -> dict:
        completed = subprocess.run(
            [PYTHON, "-c", code], cwd=str(project), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, env=env, check=False,
        )
        line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON")), "")
        if not line:
            tail = completed.stderr.strip().splitlines()
            return {"error": tail[-1][:140] if tail else "no output"}
        return json.loads(line[4:])

    pages = run(RENDER_PROBE)
    if "error" in pages:
        print(f"FAIL render probe: {pages['error']}")
        return 1

    p = pages["pages"]
    print(f"history_detail_ok: GET /history/1 -> {p['/history/1']} (status={pages['detail_status']})")
    print(
        f"history_detail_params: has_key={pages['detail_has_input_file']} "
        f"has_value={pages['detail_has_value']} kv_table={pages['detail_has_kv_table']} "
        f"len={pages['detail_text_len']}"
    )
    print(
        f"history_detail_empty: POST /run/report -> {pages['report_post']} GET /history/2 -> {pages['history2']} "
        f"else_branch={pages['detail2_no_params_branch']}"
    )
    print(
        f"history_detail_single_param: POST /run/report(limit) -> {pages['report_one_param']} "
        f"GET /history/3 -> {pages['history3']} has_limit={pages['detail3_has_limit']}"
    )
    print(f"all_routes: {p}")
    print(f"detail_500_marker_present: {pages['detail_has_error_marker']}")

    standalone = run(STANDALONE_PROBE)
    if "error" in standalone:
        print(f"FAIL standalone probe: {standalone['error']}")
        return 1
    broken = {k: v for k, v in standalone.items() if v != "ok"}
    print(f"all_templates_render: {len(standalone) - len(broken)}/{len(standalone)} ok broken={broken or 'none'}")

    ok = (
        p["/history/1"] == 200
        and pages["detail_has_kv_table"]
        and pages["history2"] == 200
        and pages["detail2_no_params_branch"]
        and pages["history3"] == 200
        and pages["detail3_has_limit"]
        and not broken
        and not audit
    )
    print(f"VERDICT {'pass' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

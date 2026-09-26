#!/usr/bin/env python3
"""Validate .github/workflows/ci.yml without needing the GitHub runner.

Checks:
  1. The file parses as YAML.
  2. The `Smoke test the CLI contract` step's `run:` body is byte-identical to
     the body embedded in scripts/verify_ci_locally.sh (the block I executed).
  3. That body is valid bash: `bash -n` sees no syntax error.
  4. Matrix / branch / encoding settings are the ones we intend to ship.

Prints conclusion lines only.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "ci.yml"
CI_SH = ROOT / "scripts" / "verify_ci_locally.sh"
failures: list[str] = []


def check(key: str, ok: bool, detail: str = "") -> None:
    print(f"yaml_{key}: {'ok' if ok else 'FAIL'}{(' ' + detail) if detail else ''}")
    if not ok:
        failures.append(key)


try:
    import yaml
except ImportError:
    yaml = None

text = WF.read_bytes().decode("utf-8")

# ---- 1. YAML parses -----------------------------------------------------------
if yaml is None:
    check("parses", False, "PyYAML not installed")
    doc = None
else:
    try:
        doc = yaml.safe_load(text)
        check("parses", True)
    except Exception as exc:  # noqa: BLE001
        check("parses", False, f"{type(exc).__name__}: {exc}")
        doc = None

# ---- 2. run: body identical to the locally executed copy ----------------------
# Pull the block scalar under `- name: Smoke test the CLI contract`.
m = re.search(
    r"- name: Smoke test the CLI contract\n\s+shell: bash\n\s+run: \|\n((?:[ \t]+.*\n|\n)+)",
    text,
)
if not m:
    check("smoke_block_found", False, "could not locate the smoke step in ci.yml")
else:
    check("smoke_block_found", True)
    wf_body = "\n".join(
        line[10:] if line.startswith(" " * 10) else line
        for line in m.group(1).rstrip("\n").split("\n")
    )
    sh_text = CI_SH.read_text(encoding="utf-8")
    m2 = re.search(r"ci_smoke_body\(\) \{\n(.*?)\n\}\n", sh_text, re.S)
    sh_body = "\n".join(
        line[2:] if line.startswith("  ") else line for line in m2.group(1).split("\n")
    )
    same = wf_body.strip() == sh_body.strip()
    check("smoke_block_matches_tested_copy", same, "" if same else "ci.yml drifted from scripts/_rp_ci.sh")

    # ---- 3. it is valid bash --------------------------------------------------
    probe = ROOT / ".ci_syntax_probe.sh"
    probe.write_bytes(b"set -euo pipefail\n" + m.group(1).encode("utf-8"))
    try:
        r = subprocess.run(
            ["D:/Git/bin/bash.exe", "-n", str(probe)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        check("bash_syntax", r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1][:80])
    finally:
        probe.unlink(missing_ok=True)

# ---- 4. shipped settings ------------------------------------------------------
if doc:
    job = doc["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    check("matrix_os", matrix["os"] == ["ubuntu-latest", "windows-latest"], str(matrix["os"]))
    check("matrix_python", matrix["python-version"] == ["3.11", "3.12", "3.13"], str(matrix["python-version"]))
    env = job.get("env", {})
    check("windows_utf8_env", str(env.get("PYTHONIOENCODING")) == "utf-8", f"PYTHONIOENCODING={env.get('PYTHONIOENCODING')}")
    triggers = doc.get("on") or doc.get(True)
    check("push_branches", triggers["push"]["branches"] == ["main", "master"], str(triggers["push"]["branches"]))
    steps = job["steps"]
    bash_steps = [s for s in steps if s.get("shell") == "bash"]
    check("bash_steps_count", len(bash_steps) >= 1, f"{len(bash_steps)} step(s) use shell: bash")
    install = next(s for s in steps if s.get("name") == "Install")
    check("install_dev_extra", 'pip install -e ".[dev]"' in install["run"], "editable dev install present")
    names = [s.get("name") for s in steps]
    check("step_order", names.index("Lint (ruff)") < names.index("Type check (mypy)") < names.index("Test (pytest)"), " -> ".join(n for n in names if n))

print(f"summary: {len(failures)} failing -> {failures if failures else 'none'}")
sys.exit(1 if failures else 0)

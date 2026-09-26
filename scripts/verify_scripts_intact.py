"""Confirm renamed scripts still work: the slow_tool fixture + every reference."""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# 1. slow_tool.py still answers --help and is scannable by helpui.
r = subprocess.run([sys.executable, "scripts/slow_tool.py", "--help"],
                   cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=ENV)
print(f"slow_tool_help: rc={r.returncode} has_usage={'usage' in r.stdout.lower()}")

r2 = subprocess.run([sys.executable, "-m", "helpui.cli", "scan", "scripts/slow_tool.py", "--json"],
                    cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=ENV)
import json
ok = False
try:
    d = json.loads(r2.stdout)
    ok = d["ok"] == "true"
except Exception:
    pass
print(f"slow_tool_scan: rc={r2.returncode} ok={ok}")

# 2. No script still points at a file that does not exist.
stale = []
for f in sorted((REPO / "scripts").glob("*")):
    if not f.is_file() or f.suffix not in {".py", ".sh"}:
        continue
    txt = f.read_text(encoding="utf-8", errors="replace")
    for ref in re.findall(r'"([A-Za-z0-9_./-]+\.py)"', txt) + re.findall(r"scripts/([A-Za-z0-9_-]+\.py)", txt):
        name = Path(ref).name
        if name in {f.name, "app.py", "config.py", "spec.py"}:
            continue
        # refs to helpui package modules or repo fixtures/texts are fine
        if (REPO / "scripts" / name).exists() or (REPO / name).exists() or (REPO / "helpui" / name).exists() or (REPO / "tests" / "fixtures" / name).exists():
            continue
        stale.append(f"{f.name} -> {name}")
print(f"stale_script_refs: {len(stale)} {stale if stale else ''}")

# 3. The scripts/ dir has no leftover private-prefixed probe names.
leftovers = [f.name for f in (REPO / "scripts").iterdir() if f.is_file() and f.name.startswith("_")]
print(f"underscore_prefixed_leftovers: {leftovers if leftovers else 'none'}")

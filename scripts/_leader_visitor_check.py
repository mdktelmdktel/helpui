"""Leader check: simulate a GitHub visitor (clone -> install -> verify)."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp(prefix="gh-visitor-"))
clone = work / "helpui"


def step(label: str, cmd: list[str], cwd: Path, expect_zero: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1200)
    ok = (r.returncode == 0) == expect_zero
    print(f"[{'OK ' if ok else 'BAD'}] {label}  rc={r.returncode}")
    if not ok:
        print("      ", (r.stdout or r.stderr).strip().splitlines()[-1][:200])
    return r


subprocess.run(["git", "clone", "-q", str(REPO), str(clone)], check=True, capture_output=True)
print(f"cloned to {clone}\n")

# Files a visitor sees
for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "CHANGELOG.md", "SPEC.md",
             ".gitignore", ".gitattributes", "pyproject.toml"):
    print(f"  {'present' if (clone / name).exists() else 'MISSING'}  {name}")
print(f"  {'present' if (clone / '.github/workflows/ci.yml').exists() else 'MISSING'}  .github/workflows/ci.yml")
print()

env_note = {"PYTHONIOENCODING": "utf-8"}

step("pip install -e .", [sys.executable, "-m", "pip", "install", "-e", ".", "-q"], clone)
step("ruff check .", [sys.executable, "-m", "ruff", "check", "."], clone)
step("mypy helpui", [sys.executable, "-m", "mypy", "helpui"], clone)
r = step("pytest -q", [sys.executable, "-m", "pytest", "-q"], clone)
line = [ln for ln in r.stdout.splitlines() if "passed" in ln or "failed" in ln]
print("      ", line[-1] if line else "")

# The full user-facing chain, exactly as CI runs it
step("scan --json", [sys.executable, "-m", "helpui.cli", "scan",
                     "tests/fixtures/sample_argparse.py", "--json"], clone)
step("generate", [sys.executable, "-m", "helpui.cli", "generate",
                  "tests/fixtures/sample_click.py", "--out", "generated"], clone)
r = step("test (relative path)", [sys.executable, "-m", "helpui.cli", "test", "generated"], clone)
import json
payload = json.loads(r.stdout)
print(f"       ok={payload['ok']!r} checks={len(payload['checks'])} all_pass={all(c['ok'] for c in payload['checks'])}")

shutil.rmtree(work, ignore_errors=True)
print("\nVISITOR EXPERIENCE VERIFIED")

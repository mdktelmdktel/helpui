"""Leader check: does a fresh clone produce LF templates (golden must hold)?"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
clone = Path(tempfile.mkdtemp(prefix="helpui-clone-")) / "repo"

subprocess.run(
    ["git", "clone", "-q", str(REPO), str(clone)],
    check=True,
    capture_output=True,
)

for rel in ("helpui/templates/record.html", "helpui/static/style.css", "README.md"):
    data = (clone / rel).read_bytes()
    crlf = data.count(b"\r\n")
    print(f"clone {rel:32} CRLF={crlf:3}  LF-only={data.count(chr(10).encode()) - crlf:3}")

# The decisive test: run the golden comparison against the clone's own assets.
print()
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_generator.py", "-q",
     "-k", "golden or byte"],
    cwd=clone,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=900,
)
print("clone pytest (golden subset) rc =", result.returncode)
tail = [ln for ln in result.stdout.splitlines() if "passed" in ln or "failed" in ln]
print(tail[-1] if tail else result.stdout[-400:])
shutil.rmtree(clone.parent, ignore_errors=True)

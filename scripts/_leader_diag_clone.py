"""Diagnose: why does a fresh clone produce CRLF despite eol=lf?"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
clone = Path(tempfile.mkdtemp(prefix="diag-")) / "repo"
subprocess.run(["git", "clone", "-q", str(REPO), str(clone)], check=True, capture_output=True)

print("=== 1. blob in the clone's object store (size bypasses smudge) ===")
print("  blob size:", subprocess.run(["git", "cat-file", "-s", "HEAD:helpui/templates/record.html"],
      cwd=clone, capture_output=True, text=True).stdout.strip())
print("  worktree size:", (clone / "helpui/templates/record.html").stat().st_size)

print("\n=== 2. is .gitattributes present in the clone? ===")
ga = clone / ".gitattributes"
print("  exists:", ga.exists())
if ga.exists():
    print("  first line:", ga.read_text(encoding="utf-8").splitlines()[0][:60])

print("\n=== 3. what does git think the attributes are in the clone? ===")
r = subprocess.run(["git", "check-attr", "-a", "--", "helpui/templates/record.html"],
                   cwd=clone, capture_output=True, text=True)
print(" ", r.stdout.strip())

print("\n=== 4. clone's effective config ===")
for key in ("core.autocrlf", "core.eol"):
    r = subprocess.run(["git", "config", "--get", key], cwd=clone, capture_output=True, text=True)
    print(f"  {key} = {r.stdout.strip()!r}")

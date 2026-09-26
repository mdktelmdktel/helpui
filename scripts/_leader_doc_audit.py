"""Audit docs for non-ASCII characters (emoji, CJK, typographic marks)."""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

for name in ("README.md", "SPEC.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE"):
    path = REPO / name
    if not path.is_file():
        print(f"{name}: MISSING")
        continue
    text = path.read_text(encoding="utf-8")
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    non_ascii = sorted({c for c in text if ord(c) > 127})
    print(f"--- {name}: {len(cjk)} CJK, {len(non_ascii)} distinct non-ASCII")
    if non_ascii:
        print("    chars:", " ".join(repr(c) for c in non_ascii))
    for i, line in enumerate(text.splitlines(), 1):
        if any(ord(c) > 127 for c in line):
            print(f"    L{i}: {line[:110]}")

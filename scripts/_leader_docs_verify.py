"""Verify all docs are Chinese, intact, and lint/test-clean."""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

CJK = re.compile(r"[\u4e00-\u9fff]")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2705\u274C\u26A0\u2B50]")

print("=== 文档语言与完整性（按字节读，不用 Get-Content）===")
for name in ("README.md", "SPEC.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE"):
    p = REPO / name
    data = p.read_bytes()
    text = data.decode("utf-8")
    print(
        f"  {name:18} {len(data):6} bytes  {len(text.splitlines()):4} lines  "
        f"CRLF={data.count(chr(13).encode()) - 0:3}  "
        f"CJK={len(CJK.findall(text)):5}  emoji={len(EMOJI.findall(text))}"
    )

print("\n=== 代码块闭合（应为偶数）===")
for name in ("README.md", "SPEC.md", "CONTRIBUTING.md"):
    text = (REPO / name).read_text(encoding="utf-8")
    n = text.count("```")
    print(f"  {name:18} ``` 数 = {n}  {'OK' if n % 2 == 0 else 'UNCLOSED'}")

print("\n=== SPEC 章节编号（应与英文版一致：51 个）===")
spec = (REPO / "SPEC.md").read_text(encoding="utf-8")
heads = [ln.strip("# ").split()[0] for ln in spec.splitlines() if ln.startswith("#")]
print(f"  章节数 = {len(heads)}")
print(f"  {heads[:8]} ...")

print("\n=== 检查 ===")
for label, cmd in (
    ("ruff check .", [sys.executable, "-m", "ruff", "check", "."]),
    ("mypy helpui", [sys.executable, "-m", "mypy", "helpui"]),
):
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    last = (r.stdout or r.stderr).strip().splitlines()[-1]
    print(f"  {label:16} rc={r.returncode}  {last}")

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent

CJK = re.compile(r"[\u4e00-\u9fff]")

for name in ("README.md", "SPEC.md", "CONTRIBUTING.md", "CHANGELOG.md"):
    p = REPO / name
    if not p.is_file():
        print(f"{name:20} 不存在")
        continue
    text = p.read_text(encoding="utf-8")
    print(f"{name:20} 中文字符 {len(CJK.findall(text)):6} 行数 {len(text.splitlines()):5}")

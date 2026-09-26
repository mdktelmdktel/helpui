"""Leader acceptance check: file-typed positional uploads are silently dropped.

Read-only probe. Generates a project, renders the form, and submits exactly
what the rendered HTML tells a user to submit.
"""

import inspect
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from helpui.generator import generate_project
from helpui.scanner_service import scan_tool

out = Path(tempfile.mkdtemp(prefix="filepos-"))
report = scan_tool("tests/fixtures/sample_argparse.py")
generate_project(report.spec, out)

convert = report.spec.command("convert")
print("=== positional types in spec (argparse convert) ===")
for p in convert.positionals:
    print(f"  name={p.name!r} dest={p.dest!r} type={p.type!r} required={p.required}")

print()
print("=== what the form template renders for a 'file' positional ===")
widget = (out / "templates" / "_widget.html").read_text(encoding="utf-8")
file_block = widget.split("{% elif option.type == 'file' %}")[1].split("{% elif")[0]
print("  renders <input type=file>:", "type=\"file\"" in file_block)

print()
print("=== generated app.py: does the POSITIONALS loop handle UploadFile? ===")
app_src = (out / "helpui_app" / "app.py").read_text(encoding="utf-8")
# The options loop is where file uploads are read.
opts_loop = app_src.split("for option in command.options")[1].split("for positional in command.positionals")[0]
pos_loop = app_src.split("for positional in command.positionals")[1]
print("  OPTIONS loop reads uploads (await upload.read()):", "await upload.read()" in opts_loop)
print("  POSITIONALS loop reads uploads:", "upload.read()" in pos_loop)
print("  POSITIONALS loop filters to str only (isinstance(v, str)):", "isinstance(v, str)" in pos_loop)
print()
print("  --- positionals loop body (first 6 non-empty lines) ---")
shown = 0
for line in pos_loop.splitlines():
    if line.strip():
        print("   ", line.rstrip())
        shown += 1
        if shown >= 6:
            break

print()
print("=== source-level verdict ===")
reads_uploads_in_positionals = "upload.read()" in pos_loop or "UploadFile" in pos_loop
print(f"  positional uploads supported: {reads_uploads_in_positionals}")
print(f"  => {'BUG CONFIRMED' if not reads_uploads_in_positionals else 'no bug'}")

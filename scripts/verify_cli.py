"""Verify `helpui generate` CLI end to end, printing only conclusions."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "helpui.cli", *args],
        cwd=cwd or REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


out = Path(tempfile.mkdtemp(prefix="cli-gen-"))

# 1. generate a project via the CLI
r = run(["generate", "tests/fixtures/sample_argparse.py", "--out", str(out), "--port", "9100"])
print(f"generate rc={r.returncode}")
print(f"  stdout_first_line={r.stdout.splitlines()[0] if r.stdout else ''!r}")
print(f"  out_has_app_py={(out / 'app.py').is_file()}")
print(f"  out_has_spec_json={(out / 'spec.json').is_file()}")

# 2. the generated project must be importable and config must carry --port 9100
probe = (
    "import sys; sys.path.insert(0, '.');"
    "from helpui_app.config import load_config;"
    "c = load_config(); print('port', c.port, 'host', c.host, 'timeout', c.timeout_seconds)"
)
r2 = subprocess.run(
    [sys.executable, "-c", probe],
    cwd=out,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=120,
    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
)
print(f"config_probe rc={r2.returncode} {r2.stdout.strip()} {r2.stderr.strip()[:200]}")

# README must reflect the port passed to --port (check BEFORE the force rerun,
# which legitimately resets it to the default).
readme_9100 = (out / "README.md").read_text(encoding="utf-8")
print(f"readme_mentions_port_9100={'9100' in readme_9100}")

# 3. running generate again without force must refuse
r3 = run(["generate", "tests/fixtures/sample_argparse.py", "--out", str(out)])
print(f"regenerate_no_force rc={r3.returncode} refused={r3.returncode == 1}")
print(f"  stderr={r3.stderr.strip().splitlines()[0] if r3.stderr.strip() else ''!r}")

# 4. with --force it must succeed
r4 = run(["generate", "tests/fixtures/sample_argparse.py", "--out", str(out), "--force"])
print(f"regenerate_force rc={r4.returncode}")

# 5. spec.json must round-trip through helpui's own model
from helpui.model import CLISpec  # noqa: E402

sys.path.insert(0, str(REPO))
restored = CLISpec.from_json((out / "spec.json").read_text(encoding="utf-8"))
print(f"spec_roundtrip tool={restored.tool_name} commands={[c.name for c in restored.commands]}")

# 6. generated README mentions the tool, and after --force the default port
readme = (out / "README.md").read_text(encoding="utf-8")
print(f"readme_after_force_has_default_port={'8000' in readme}")
print(f"readme_mentions_tool={'sample_argparse' in readme}")

# 7. no CRLF anywhere in generated text files
crlf = [
    str(p.relative_to(out))
    for p in out.rglob("*")
    if p.is_file() and p.suffix in {".py", ".html", ".css", ".md", ".json"} and b"\r\n" in p.read_bytes()
]
print(f"files_with_crlf={crlf if crlf else 'none'}")

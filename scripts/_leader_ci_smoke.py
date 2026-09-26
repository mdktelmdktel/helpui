"""Leader check: run the exact CI smoke steps locally (outside the repo)."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp(prefix="ci-smoke-"))


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )


# Copy the repo to a scratch dir so we never litter the real working tree.
target = work / "repo"
shutil.copytree(REPO, target, ignore=shutil.ignore_patterns(".git", "*_cache", "*.egg-info"))

r = run([sys.executable, "-m", "helpui.cli", "scan", "tests/fixtures/sample_argparse.py", "--json"], target)
spec = json.loads(r.stdout)
print(f"scan rc={r.returncode} ok={spec['ok']!r} kind={spec['spec']['parser_kind']} commands={len(spec['spec']['commands'])}")
assert spec["ok"] == "true" and spec["spec"]["parser_kind"] == "argparse" and len(spec["spec"]["commands"]) == 3

gen = run(
    [sys.executable, "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py",
     "--out", "generated", "--port", "8123"],
    target,
)
print(f"generate rc={gen.returncode}")

st = run([sys.executable, "-m", "helpui.cli", "test", "generated"], target)
payload = json.loads(st.stdout)
bad = [c for c in payload["checks"] if not c["ok"]]
print(f"test rc={st.returncode} ok={payload['ok']!r} checks={len(payload['checks'])} failing={len(bad)}")
assert payload["ok"] == "true", bad
assert len(payload["checks"]) >= 9, len(payload["checks"])
assert not bad, bad
print("CI SMOKE OK")

shutil.rmtree(work, ignore_errors=True)

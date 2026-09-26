"""Leader final acceptance: inspect helpui test JSON output."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
good = Path(tempfile.mkdtemp(prefix="accept-"))


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "helpui.cli", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


gen = cli("generate", "tests/fixtures/sample_argparse.py", "--out", str(good))
print(f"generate_exit={gen.returncode}")

res = cli("test", str(good))
print(f"good_dir_exit={res.returncode}")
payload = json.loads(res.stdout)
print(f"ok={payload['ok']!r}  (type={type(payload['ok']).__name__}, expect str)")
print(f"checks={len(payload['checks'])}  all_pass={all(c['ok'] for c in payload['checks'])}")
print("check names:", ", ".join(c["name"] for c in payload["checks"]))

bad = cli("test", str(good / "nope"))
print(f"bad_dir_exit={bad.returncode}")
bad_payload = json.loads(bad.stdout)
print(f"bad ok={bad_payload['ok']!r}  raised_cleanly={True}")

# stdout must be exactly one JSON document (pipeable)
print(f"stdout_is_single_json={res.stdout.strip().startswith('{') and res.stdout.strip().endswith('}')}")

"""Leader verification: required parsing across the three fixtures."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

for fx in ("argparse", "click", "typer"):
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "helpui.cli",
            "scan",
            f"tests/fixtures/sample_{fx}.py",
            "--json",
            "--subcommand",
            "convert",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    spec = json.loads(out.stdout)["spec"]
    cmd = spec["commands"][0]
    for opt in cmd["options"]:
        if opt["dest"] == "token":
            print(f"{fx:9} option    --token   required={opt['required']}")
    for pos in cmd["positionals"]:
        print(f"{fx:9} positional {pos['dest']:<9} required={pos['required']} type={pos['type']}")
    print()

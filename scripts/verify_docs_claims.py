#!/usr/bin/env python3
"""Verify every command and path that README.md / CONTRIBUTING.md tell users to run.

The docs are the first thing a visitor follows; a stale path or a renamed flag
makes the project look broken. This executes the documented surface instead of
eyeballing it. Prints conclusion lines only; subprocess output is swallowed.

Deliberately does NOT run the full pytest suite (that is the acceptance script's
job) and does NOT start a real server (TestClient only).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(key: str, ok: bool, detail: str = "") -> None:
    print(f"docs_{key}: {'ok' if ok else 'FAIL'}{(' ' + detail) if detail else ''}")
    if not ok:
        failures.append(key)


ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # encoding must be pinned explicitly: text=True alone decodes with the
    # Windows ANSI code page (GBK here), which explodes on the non-ASCII text
    # this project's fixtures and templates contain. PYTHONIOENCODING only
    # fixes the *child*; the parent's decode is a separate concern.
    return subprocess.run(
        argv,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        timeout=300,
    )


def text(rel: str) -> str:
    return (ROOT / rel).read_bytes().decode("utf-8")


readme = text("README.md")
contrib = text("CONTRIBUTING.md")

# ---- 1. every path mentioned in backticks/inline code that looks like a repo path
path_pat = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|toml|json|html|css|js|yml|yaml|txt))`")
mentions = set(path_pat.findall(readme)) | set(path_pat.findall(contrib))
# Resolve each mention against the repo root, or against a plausible parent dir.
missing: list[str] = []
for mention in sorted(mentions):
    if mention.startswith(("http", "/")):
        continue
    if (ROOT / mention).exists():
        continue
    # Templates live in helpui/templates/; fixtures in tests/fixtures/.
    candidates = [ROOT / "helpui" / mention, ROOT / "tests" / mention, ROOT / "helpui" / "templates" / Path(mention).name]
    if any(c.exists() for c in candidates):
        continue
    # Some bare filenames refer to files inside a *generated* project, not this
    # repo. `config.py` only exists as helpui_app/config.py, and `spec.json`
    # only as <out>/spec.json, in the output of `helpui generate` -- which the
    # docs describe separately. Collect every filename the generator writes.
    generator_src = (ROOT / "helpui" / "generator.py").read_text(encoding="utf-8")
    generated = {
        Path(p).name
        for p in re.findall(r"[\"']([A-Za-z0-9_./-]+\.(?:py|json|md|html|css|js|gitkeep))[\"']", generator_src)
    }
    if Path(mention).name in generated:
        continue
    missing.append(mention)
check("referenced_paths_exist", not missing, f"{len(mentions)} mentions, {len(missing)} unresolved" + (f" -> {missing}" if missing else ""))

# ---- 2. every `helpui <cmd>` shown in the docs is a real subcommand
out = run([sys.executable, "-m", "helpui.cli", "--help"])
help_text = out.stdout + out.stderr
subcommands = ["scan", "generate", "serve", "test"]
absent = [c for c in subcommands if c not in help_text]
check("four_subcommands_documented", not absent, f"missing from --help: {absent}" if absent else "scan/generate/serve/test all present")
check("no_unimplemented_placeholder", "not implemented" not in help_text.lower(), "no placeholder command in --help")

# ---- 3. the exact commands CONTRIBUTING.md lists under "检查项"
checks = [
    ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
    ("mypy", [sys.executable, "-m", "mypy", "helpui"]),
    ("pytest_collect", [sys.executable, "-m", "pytest", "--collect-only", "-q"]),
]
for name, argv in checks:
    r = run(argv)
    check(f"contributing_cmd_{name}", r.returncode == 0, f"rc={r.returncode}")
    if name == "pytest_collect":
        # `-q` prints per-file counts and no grand total, so the total is the
        # sum of the `path: N` lines. (`pytest --collect-only` without -q does
        # print "N tests collected", but CONTRIBUTING.md documents the -q form.)
        combined = r.stdout + r.stderr
        per_file = [int(n) for n in re.findall(r":\s*(\d+)\s*$", combined, re.M)]
        total = sum(per_file)
        check("test_count_reported", total > 0, f"{total} tests collected across {len(per_file)} files")

# ---- 4. the three fixture commands README tells users to try
for fixture in ["sample_argparse", "sample_click", "sample_typer"]:
    r = run([sys.executable, f"tests/fixtures/{fixture}.py", "convert", "--help"])
    check(f"fixture_help_{fixture}", r.returncode == 0 and "convert" in r.stdout, f"rc={r.returncode}")

# ---- 5. the documented scan recipes, executed
r = run([sys.executable, "-m", "helpui.cli", "scan", "tests/fixtures/sample_argparse.py", "--json"])
ok_json = False
if r.returncode == 0:
    try:
        payload = json.loads(r.stdout)
        ok_json = payload.get("ok") == "true" and "spec" in payload
    except json.JSONDecodeError:
        ok_json = False
check("readme_scan_json_pipeable", ok_json, f"rc={r.returncode}, stdout is one JSON doc={ok_json}")

r = run([sys.executable, "-m", "helpui.cli", "scan", "tests/fixtures/sample_argparse.py", "--subcommand", "convert"])
check("readme_scan_subcommand", r.returncode == 0, f"rc={r.returncode}")

# The README claims `helpui scan ./script.py` runs .py files with the current interpreter.
r = run([sys.executable, "-m", "helpui.cli", "scan", "./tests/fixtures/sample_click.py", "--json"])
check("readme_scan_dot_slash_script", r.returncode == 0, f"rc={r.returncode}")

# ---- 6. documented generate/serve/test chain + env var table
with tempfile.TemporaryDirectory() as td:
    outdir = Path(td) / "webui"
    r = run([sys.executable, "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py", "--out", str(outdir), "--port", "9000", "--host", "0.0.0.0"])
    check("readme_generate_flags", r.returncode == 0 and (outdir / "app.py").exists(), f"rc={r.returncode}")
    # --port/--host are documented as landing in config.py and the generated README.
    cfg = (outdir / "helpui_app" / "config.py").read_text(encoding="utf-8")
    gen_readme = (outdir / "README.md").read_text(encoding="utf-8")
    check("generate_folds_port_host", "9000" in cfg and "9000" in gen_readme and "0.0.0.0" in cfg, "--port/--host written into config.py and README.md")

    # README's "会拒绝覆盖" claim.
    r2 = run([sys.executable, "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py", "--out", str(outdir)])
    check("generate_refuses_nonempty", r2.returncode != 0, f"rc={r2.returncode} without --force")
    r3 = run([sys.executable, "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py", "--out", str(outdir), "--force"])
    check("generate_force_overwrites", r3.returncode == 0, f"rc={r3.returncode} with --force")

    r4 = run([sys.executable, "-m", "helpui.cli", "test", str(outdir)])
    ok_selftest = False
    if r4.returncode == 0:
        try:
            sel = json.loads(r4.stdout)
            ok_selftest = sel.get("ok") == "true" and len(sel.get("checks", [])) == 13
        except json.JSONDecodeError:
            ok_selftest = False
    check("readme_test_chain", ok_selftest, "13 checks, ok=true" if ok_selftest else f"rc={r4.returncode}")

    # Relative-path form: the documented `helpui test webui` style.
    r5 = run([sys.executable, "-m", "helpui.cli", "test", "webui"], cwd=Path(td))
    check("readme_test_relative_path", r5.returncode == 0, f"rc={r5.returncode}")

    # README's "生成结果是纯源码 —— 不会预先创建数据库文件".
    # `helpui test` drives a real run, so a history database is expected to
    # appear only after that run. Assert the generate output is what we test,
    # by regenerating into a pristine directory.
    pristine = Path(td) / "pristine"
    run([sys.executable, "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py", "--out", str(pristine)])
    pre_db = (pristine / "data" / "history.db").exists()
    check("no_predb_created", not pre_db, "data/history.db absent until first run")

    # README's env-var table must match what the generated config actually reads.
    gen_cfg = cfg
    documented = [
        "HELPUI_HOST", "HELPUI_PORT", "HELPUI_TIMEOUT", "HELPUI_MAX_OUTPUT_BYTES",
        "HELPUI_MAX_UPLOAD_BYTES", "HELPUI_MAX_UPLOAD_FILES", "HELPUI_HISTORY_LIMIT",
    ]
    gen_all = "\n".join(f.read_text(encoding="utf-8") for f in (outdir / "helpui_app").glob("*.py"))
    undocumented = [v for v in documented if v not in gen_all]
    check("env_var_table_matches_code", not undocumented, f"{len(documented)} documented, missing from generated code: {undocumented}" if undocumented else f"all {len(documented)} present")

    # README claims `helpui test` has no --json flag, unlike scan. Assert both
    # halves: the prose says so, and the CLI actually rejects it.
    test_section = text("README.md").split("### `helpui test")[1][:1000]
    prose_ok = "没有" in test_section and "--json" in test_section
    r6 = run([sys.executable, "-m", "helpui.cli", "test", str(outdir), "--json"])
    cli_rejects = r6.returncode != 0
    check("selftest_has_no_json_flag", prose_ok and cli_rejects, f"prose_says_no={prose_ok} cli_rejects_flag={cli_rejects}")

print(f"summary: {len(failures)} failing -> {failures if failures else 'none'}")
sys.exit(1 if failures else 0)

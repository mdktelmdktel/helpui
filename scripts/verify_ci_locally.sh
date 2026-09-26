#!/usr/bin/env bash
# Replay .github/workflows/ci.yml step-for-step, locally, with bash semantics.
#
# Why a script file and not an inline command: the CI smoke step is a multi-line
# `shell: bash` block containing `python -c "..."` with embedded quotes and
# newlines. Reproducing it here proves the *exact* YAML `run:` payload is valid
# bash -- which is the thing that has never actually executed on GitHub.
#
# Prints one `ci_local: <step> ...` conclusion line per step. Full command output
# is captured to a log file, never to stdout, so this stays cheap to read.
#
# Usage:  bash scripts/verify_ci_locally.sh
# Env:    PY  (python executable, default: python)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
LOG="$ROOT/.ci_local.log"
: > "$LOG"

PY="${PY:-python}"
PASS=0
FAIL=0
FAILED_STEPS=()

step() {  # step <name> <command...>
  local name="$1"; shift
  echo "=== $name ===" >> "$LOG"
  if "$@" >> "$LOG" 2>&1; then
    echo "ci_local: $name PASSED"
    PASS=$((PASS + 1))
  else
    echo "ci_local: $name FAILED (see .ci_local.log)"
    FAIL=$((FAIL + 1))
    FAILED_STEPS+=("$name")
  fi
}

echo "ci_local: bash=$BASH_VERSION pwd=$ROOT"
echo "ci_local: python=$($PY --version 2>&1)"

# --- GitHub Actions sets up a UTF-8 environment; the Windows runner's default
# --- console is GBK and this repo's fixtures/templates contain non-ASCII text.
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# --- Step: Install (ci.yml lines 36-38) ---------------------------------------
step "upgrade_pip" "$PY" -m pip install --quiet --upgrade pip
step "pip_install_editable_dev" "$PY" -m pip install --quiet -e ".[dev]"

# --- Step: Lint (ci.yml line 41) ----------------------------------------------
step "ruff_check" "$PY" -m ruff check .

# --- Step: Type check (ci.yml line 44) ----------------------------------------
step "mypy" "$PY" -m mypy helpui

# --- Step: Test (ci.yml line 47) ----------------------------------------------
step "pytest" "$PY" -m pytest -q

# --- Step: Smoke test the CLI contract (ci.yml lines 49-67) -------------------
# Verbatim body of the YAML `run:` block below this line, so a syntax error in
# the workflow is a syntax error here.

ci_smoke_body() {
  # `scan --json` must be pipeable: exactly one JSON document on stdout.
  helpui scan tests/fixtures/sample_argparse.py --json > spec.json
  python -c "import json;d=json.load(open('spec.json'));assert d['ok']=='true',d"
  python -c "import json;d=json.load(open('spec.json'));assert d['spec']['parser_kind']=='argparse',d"
  python -c "import json;d=json.load(open('spec.json'));assert len(d['spec']['commands'])==3,d"

  # The full chain: generate, then smoke-test the generated project.
  helpui generate tests/fixtures/sample_click.py --out generated --port 8123
  helpui test generated | tee selftest.json
  python -c "
  import json
  d = json.load(open('selftest.json'))
  assert d['ok'] == 'true', d
  assert len(d['checks']) >= 9, d
  assert all(c['ok'] for c in d['checks']), [c for c in d['checks'] if not c['ok']]
  "
}

smoke() {
  set -euo pipefail
  rm -rf generated spec.json selftest.json
  ci_smoke_body
}

step "ci_smoke_block" smoke

# --- Extra assertions the workflow implies but does not state -----------------
extra() {
  set -euo pipefail
  python - <<'PY'
import json, pathlib, sys
d = json.loads(pathlib.Path("spec.json").read_text(encoding="utf-8"))
assert d["ok"] == "true", d
assert d["schema_version"] == 1, d
assert d["spec"]["tool_name"] == "sample_argparse", d
assert len(d["spec"]["commands"]) == 3, d
s = json.loads(pathlib.Path("selftest.json").read_text(encoding="utf-8"))
assert s["ok"] == "true", s
assert len(s["checks"]) == 13, len(s["checks"])
print("scan_json_shape_ok", len(d["spec"]["commands"]), "commands;", len(s["checks"]), "selftest checks")
PY
  # The generated tree must be self-contained and complete.
  python - <<'PY'
import pathlib
p = pathlib.Path("generated")
files = sorted(f for f in p.rglob("*") if f.is_file())
mods = sorted(f.name for f in (p / "helpui_app").glob("*.py"))
tpls = sorted(f.name for f in (p / "templates").glob("*.html"))
assert len(files) == 24, (len(files), [str(f) for f in files])
assert len(mods) == 10, mods
assert len(tpls) == 8, tpls
assert (p / "data" / ".gitkeep").exists(), "data/.gitkeep missing"
print(f"generated_tree_ok files={len(files)} modules={len(mods)} templates={len(tpls)}")
PY
  # Generated files must be LF even on a Windows runner.
  python - <<'PY'
import pathlib
bad = []
for f in sorted(pathlib.Path("generated").rglob("*")):
    if f.is_file() and f.suffix in {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".gitkeep"}:
        if f.name == "htmx.min.js":
            continue
        if b"\r\n" in f.read_bytes():
            bad.append(str(f))
assert not bad, bad
print("generated_lf_ok all generated text files use LF")
PY
}
step "extra_contract_assertions" extra

# --- Summary ------------------------------------------------------------------
echo "ci_local_run: ${PASS}/${PASS} steps passed"
if [ "$FAIL" -ne 0 ]; then
  echo "ci_local_run: FAILED steps -> ${FAILED_STEPS[*]}"
  exit 1
fi
echo "ci_local_run: all steps passed (log: .ci_local.log)"

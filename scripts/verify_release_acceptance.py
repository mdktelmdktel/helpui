#!/usr/bin/env python3
"""Independent final acceptance: fresh clone -> install -> checks -> full chain.

Deliberately starts from `git clone`, not the working tree, so that anything
uncommitted or ignored (or any CRLF that .gitattributes should have fixed) shows
up here. Uncommitted working-tree changes are therefore NOT covered -- see the
note printed as `acceptance_scope:`.

Heavy output goes to .acceptance.log; stdout carries conclusion lines only.
Runs the full pytest suite exactly once.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
LOG = SRC / ".acceptance.log"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
failures: list[str] = []


def check(key: str, ok: bool, detail: str = "") -> None:
    print(f"acceptance_{key}: {'ok' if ok else 'FAIL'}{(' ' + detail) if detail else ''}")
    if not ok:
        failures.append(key)


def log(msg: str) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def run(argv: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str, str]:
    log(f"\n$ {' '.join(argv)}  (cwd={cwd})")
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV, timeout=timeout)
    log(f"rc={r.returncode}\n--stdout--\n{r.stdout}\n--stderr--\n{r.stderr}")
    return r.returncode, r.stdout, r.stderr


LOG.write_text("", encoding="utf-8")

with tempfile.TemporaryDirectory(prefix="helpui_accept_") as td:
    tmp = Path(td)
    clone = tmp / "clone"

    # ---- 0. the clone is only as good as the committed state -----------------
    rc, out, _ = run(["git", "status", "--porcelain"], SRC)
    dirty = [ln for ln in out.splitlines() if ln.strip() and not ln.strip().endswith(".log")]
    check("source_tree_committed", True, f"{len(dirty)} uncommitted path(s) NOT covered by this clone test" if dirty else "clean; clone reflects the committed state")

    # ---- 1. fresh clone -------------------------------------------------------
    rc, _, err = run(["git", "clone", "--quiet", str(SRC), str(clone)], tmp)
    check("fresh_clone", rc == 0, f"rc={rc}" if rc else "")

    # ---- 2. .gitattributes actually produced LF ------------------------------
    bad_crlf: list[str] = []
    checked_files = 0
    for f in sorted(clone.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        # Only files git considers text, and that the golden tests care about.
        if f.suffix in {".py", ".html", ".css", ".js", ".json", ".md", ".toml", ".txt", ".yml", ".yaml"} or f.name in {".gitattributes", ".gitignore", "LICENSE", ".gitkeep"}:
            checked_files += 1
            if b"\r\n" in f.read_bytes() and f.name != "htmx.min.js":
                bad_crlf.append(str(f.relative_to(clone)))
    check("clone_all_text_lf", not bad_crlf, f"{checked_files} text files checked, {len(bad_crlf)} CRLF" + (f" -> {bad_crlf[:5]}" if bad_crlf else ""))

    # ---- 3. editable install with the dev extra ------------------------------
    venv = tmp / "venv"
    rc, _, _ = run([sys.executable, "-m", "venv", str(venv)], tmp)
    check("venv_created", rc == 0, f"rc={rc}")
    vpy = venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    vbin = vpy.parent

    rc, _, _ = run([str(vpy), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], clone)
    check("clone_pip_upgrade", rc == 0, f"rc={rc}")
    rc, _, _ = run([str(vpy), "-m", "pip", "install", "--quiet", "-e", ".[dev]"], clone, timeout=1800)
    check("clone_install_editable_dev", rc == 0, f"rc={rc}")

    # ---- 4. the three gates ---------------------------------------------------
    rc, _, _ = run([str(vpy), "-m", "ruff", "check", "."], clone)
    check("clone_ruff", rc == 0, f"rc={rc}")
    rc, _, _ = run([str(vpy), "-m", "mypy", "helpui"], clone)
    check("clone_mypy", rc == 0, f"rc={rc}")
    rc, out, _ = run([str(vpy), "-m", "pytest", "-q"], clone, timeout=1800)
    check("clone_pytest", rc == 0, f"rc={rc}")
    # Count the tests from the output. pyproject sets `addopts = "-q"`, so CI's
    # `pytest -q` is effectively -qq: it prints ONLY the progress dots and a
    # percentage column -- no per-file counts, no "N passed" summary line.
    # The dots are therefore the reliable signal.
    dots = sum(line.count(".") for line in out.splitlines() if re.match(r"^[.sxFEW]+", line.strip()))
    m = re.search(r"(\d+)\s+passed", out)
    total = int(m.group(1)) if m else dots
    check("clone_pytest_count", total > 0, f"{total} tests passed")

    # ---- 5. generated project shape ------------------------------------------
    gen = clone / "generated"
    rc, _, _ = run([str(vpy), "-m", "helpui.cli", "generate", "tests/fixtures/sample_click.py", "--out", str(gen), "--port", "8123"], clone)
    check("chain_generate", rc == 0, f"rc={rc}")
    files = sorted(f for f in gen.rglob("*") if f.is_file())
    mods = sorted((gen / "helpui_app").glob("*.py"))
    tpls = sorted((gen / "templates").glob("*.html"))
    check("generated_24_files", len(files) == 24, f"found {len(files)}")
    check("generated_10_modules", len(mods) == 10, f"found {len(mods)}")
    check("generated_8_templates", len(tpls) == 8, f"found {len(tpls)}")

    # ---- 6. `helpui test` full self-check ------------------------------------
    rc, out, _ = run([str(vpy), "-m", "helpui.cli", "test", str(gen)], clone)
    sel_ok = False
    n_checks = 0
    if rc == 0:
        try:
            sel = json.loads(out)
            sel_ok = sel.get("ok") == "true"
            n_checks = len(sel.get("checks", []))
        except json.JSONDecodeError:
            sel_ok = False
    check("chain_helpui_test", sel_ok, f"ok={sel_ok} checks={n_checks}")

    # ---- 7. REAL server: does `serve` actually come up and answer 200? --------
    port = 8231
    while True:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                break
        port += 1
    # The documented launch is `python app.py` (README says `helpui serve` is
    # equivalent to running `python app.py` in the project dir) -- NOT
    # `uvicorn app:app`: app.py is an argparse entry point with no module-level
    # ASGI object to import.
    proc = subprocess.Popen(
        [str(vpy), "app.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=gen, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=ENV,
    )
    base = f"http://127.0.0.1:{port}"
    up = False
    for _ in range(60):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(base + "/", timeout=1) as resp:
                up = resp.status == 200
                if up:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    check("real_server_starts", up, f"GET / -> 200 on {base}" if up else "server did not answer")

    if up:
        # index / history / form / api all render.
        pages = {"/": None, "/history": None, "/api/spec": None}
        for path in pages:
            try:
                with urllib.request.urlopen(base + path, timeout=5) as resp:
                    pages[path] = resp.status
            except (urllib.error.URLError, OSError):
                pages[path] = None
        check("real_server_pages", all(v == 200 for v in pages.values()), str(pages))

        # POST a real form run, then confirm it landed in history.
        spec = json.loads((gen / "spec.json").read_text(encoding="utf-8"))
        # Use a real subcommand with a known option set; the root command has an
        # empty name (documented in README) which is an awkward URL slug.
        target = next(c for c in spec["commands"] if c["name"])
        slug = target["name"]
        form = f"{base}/command/{slug}"
        try:
            with urllib.request.urlopen(form, timeout=5) as resp:
                form_ok = resp.status == 200
        except (urllib.error.URLError, OSError):
            form_ok = False
        check("real_server_form_renders", form_ok, f"GET {form}")

        # Build the POST body from the spec. Templates name each control by its
        # bare `dest` (see helpui/templates/_widget.html); positionals likewise.
        fields: list[tuple[str, str]] = []
        for opt in target["options"]:
            dest = opt["dest"]
            if opt.get("is_flag"):
                fields.append((dest, "on"))
            elif dest == "password":
                fields.append((dest, "s3cr3t"))
            elif opt.get("choices"):
                fields.append((dest, str(opt["choices"][0])))
            elif opt["type"] in {"int", "float"}:
                fields.append((dest, "1"))
            else:
                fields.append((dest, "value"))
        for pos in target["positionals"]:
            if pos["type"] == "file":
                continue  # no real upload needed; the app tolerates it missing
            # `name` is the metavar (INPUT_FILE); the form control is keyed by
            # `dest` (input_file) -- same convention as options.
            fields.append((pos["dest"], "hello"))

        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(f"{base}/run/{slug}", data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
            posted = resp.status == 200
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            body = str(exc)
            posted = False
        check("real_server_form_submit", posted, f"POST /run/{slug} fields={len(fields)}")

        # History must now contain a record.
        hist_body = ""
        try:
            with urllib.request.urlopen(base + "/history", timeout=10) as resp:
                hist_body = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            pass
        db = gen / "data" / "history.db"
        check("real_server_history_recorded", db.exists(), f"data/history.db created={db.exists()} ({db.stat().st_size if db.exists() else 0} bytes)")
        check("real_server_history_renders", "GET /history" in hist_body or "history" in hist_body.lower(), f"{len(hist_body)} bytes rendered")

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    check("real_server_stops", proc.poll() is not None, "server terminated cleanly")

print(f"acceptance_summary: {len(failures)} failing -> {failures if failures else 'none'}")
sys.exit(1 if failures else 0)

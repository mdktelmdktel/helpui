# HelpUI

**Turn any non-interactive CLI tool into a local web UI — just from its `--help` output.**

HelpUI reads a tool's `--help`, understands its arguments, and generates a
standalone web application with a real form, a run button, live logs, result
rendering, history and downloads. No wrapper code to write, no frontend build.

```console
$ helpui scan ./mytool.py
mytool  [argparse]  (D:\work\mytool.py)
  Convert and inspect files.
  detection: high - argparse section headers detected

  (root): Convert and inspect files.
    opt --verbose          bool
        Enable verbose logging

  convert: Convert an input file to another format.
    opt --output           str      [default: out.txt]
        Output path
    opt --format           choice   [default: json] choices=['json', 'csv', 'tsv']
        Output format
    arg input_file         file     (required)
        File to convert
```

---

## Status

**All five phases are implemented.** The CLI, the parser layer, the generator and
the runtime are all in place.

| Phase | Scope | Status |
|---|---|---|
| 1 | Model, parsers (argparse/click/typer), detector, `scan` CLI | ✅ Complete |
| 2 | Generator, golden tests | ✅ Complete |
| 3 | Runtime: FastAPI app, forms, live logs, result rendering | ✅ Complete |
| 4 | History, downloads, cancel, timeout, security layer | ✅ Complete |
| 5 | `helpui test`, end-to-end suite, docs | ✅ Complete |

The four commands are all live: `scan`, `generate`, `serve` and `test`. Nothing
in `--help` is a stub any more.

---

## Installation

Requires **Python 3.11+**.

```console
# Clone, then install in editable mode
pip install -e .

# With the development/test tooling (pytest, httpx, ruff, mypy, click, typer)
pip install -e ".[dev]"
```

`click` and `typer` are **test-only** dependencies: they are the frameworks
HelpUI *parses*, not libraries HelpUI needs at runtime. Keeping them in the
`dev` extra is why `helpui scan` itself runs on a minimal install.

---

## The four commands

### `helpui scan <tool> [--json] [--subcommand NAME]`

Runs `<tool> --help`, parses it, and prints the resulting `CLISpec`.

```console
helpui scan mytool                      # human-readable summary
helpui scan mytool --json               # machine-readable envelope
helpui scan mytool --json | jq .spec    # just the spec
helpui scan mytool --subcommand convert # only one subcommand's page
helpui scan ./script.py --json          # a .py file runs via the current interpreter
helpui scan "python -m mypackage"       # a full command line is accepted too
```

`--json` prints exactly one JSON document on stdout — safe to pipe. Use
`--indent 0` for compact output. Excerpt of a real
`helpui scan tests/fixtures/sample_click.py --json` response, trimmed for
length (`--indent 1` to keep it readable here; the default is `2`):

```json
{
  "ok": "true",
  "schema_version": 1,
  "spec": {
    "tool_name": "sample_click",
    "tool_path": "D:\\path\\tests\\fixtures\\sample_click.py",
    "description": "Sample click tool used by HelpUI tests.",
    "commands": [
      {
        "name": "",
        "help": "Sample click tool used by HelpUI tests.",
        "options": [],
        "positionals": [],
        "subcommands": []
      },
      {
        "name": "convert",
        "help": "Convert an input file to another format.",
        "options": [
          {
            "name": "--output",
            "dest": "output",
            "type": "str",
            "required": false,
            "default": "out.txt",
            "choices": null,
            "help": "Output path. [default: out.txt]",
            "is_flag": false,
            "multiple": false,
            "value_name": "TEXT",
            "aliases": ["-o", "--output"]
          }
        ],
        "positionals": [],
        "subcommands": []
      }
    ],
    "parser_kind": "click"
  },
  "detection": {
    "parser_kind": "click",
    "confidence": "high",
    "reason": "click section headers detected"
  },
  "warnings": []
}
```

Three details worth knowing before you consume this JSON:

* `ok` is the **string** `"true"`/`"false"`, not a JSON boolean — see
  [SPEC.md](SPEC.md) §6.2. Test with `payload["ok"] == "false"`.
* The **root command's `name` is the empty string**, not `"(root)"`; `"(root)"`
  is only a display label the human-readable output adds. Root `help` repeats
  the tool description.
* With `--subcommand NAME`, `spec.description` is **that subcommand's help**, not
  the parent tool's description (verified: `--subcommand convert` gives
  `"Convert an input file to another format."`).

When a scan fails, the same envelope carries a stable error code and exits `1`
(real output of `helpui scan <tool-that-is-not-a-CLI> --json`):

```json
{
  "ok": "false",
  "schema_version": 1,
  "code": "unknown_framework",
  "error": "captured help output but no `usage:` line was found",
  "hint": "Supported frameworks: argparse, click, typer. See SPEC.md for the coverage matrix."
}
```

Note that a page with a lower-case `usage:` line but no section headers is **not**
an error: it resolves to `argparse` at `confidence: "low"` (SPEC §4.1). Only
output with no recognisable `usage:` line at all becomes `unknown_framework`.

Stable codes: `tool_not_found`, `no_help_output`, `unknown_framework`,
`subcommand_not_found`, `parse_failed`, `internal_error`.

### `helpui generate <tool> --out <dir> [--port 8000] [--host 127.0.0.1] [--force]`

Scans the tool and writes a self-contained web project into `--out`: an `app.py`
entry point, the `helpui_app/` package (10 modules), `spec.json`, the templates
and static assets it needs, a `data/` directory (with `.gitkeep`) and a
project-local `README.md`. `--force` overwrites an existing directory.

The generated project is plain source code — the generator does **not** write a
database. `history.db` is created at runtime by the app on first use, so a
generated project is fully described by its committed files and reproduces
byte-for-byte across platforms (all generated text is written with LF endings).

### `helpui serve <dir> [--port 8000] [--host 127.0.0.1] [--reload]`

Starts the generated project.

### `helpui test <dir> [--timeout 60]`

Smoke-tests a generated project end to end: boots the app, then walks the real
surfaces — `GET /`, `/history`, `/api/spec`, the static assets, every command's
form page, a real form submission, the recorded history row and the run API
round-trip. It prints one JSON document on stdout and exits `0` only when every
check passed:

```json
{
  "ok": "true",
  "project_dir": "/abs/path/to/project",
  "checks": [{"name": "index_responds", "ok": true, "detail": "GET / -> 200"}]
}
```

There are **13 checks**:

```
project_exists      spec_loads          app_imports        app_boots_factory
app_boots           index_responds      history_responds   api_spec_responds
static_assets       form_renders        submit_job         history_recorded
api_run_roundtrip
```

As with `scan`, `ok` is the **string** `"true"`/`"false"` (SPEC §6.2), and a
failed project exits `1` with the same envelope. Note that `test` takes no
`--json` flag — unlike `scan`, JSON *is* its only output format.

---

## Supported tools

Detection is based on the help output's structure. Three frameworks are
supported:

| Framework | Recognised by | Notes |
|---|---|---|
| **typer** | rich table panels (box-drawing **or** ASCII) | Checked first, before argparse/click |
| **argparse** | lower-case `usage:` plus lower-case `positional arguments:` / `optional arguments:` / `options:`, and **no** capitalised click header | Handles wrapped usage lines, epilogs, nested subparsers and `{a,b}` choice metavars |
| **click** | capitalised `Usage:` plus at least one of `Options:` / `Commands:` / `Arguments:` | Handles `[required]`, `[default: x]`, `[env var: X]`, `[a\|b\|c]` choices, `multiple=True` |

Detection runs in that order. Typer is tested first because rich panels would
otherwise be mis-parsed; the argparse test explicitly requires the *absence* of
click's capitalised headers, so a page carrying both is not mistaken for
argparse. A bare usage line with no section headers at all still resolves to
`argparse` or `click` at `confidence: low` rather than failing. Anything else —
man-page style help, docopt, fire, a hand-rolled parser — is reported as
`unknown_framework` with exit code 1. HelpUI deliberately does not attempt a
generic help parse (see [SPEC.md](SPEC.md) §4.5).

Option types map onto form widgets (`CLIOption.type`, checked at construction
time against a closed vocabulary — anything unrecognised degrades to `str`):

| Detected type | Widget |
|---|---|
| `str` | text input (also the fallback for unrecognised types) |
| `int`, `float` | number input |
| `bool` | checkbox |
| `choice` | select |
| `file` | file upload |
| `dir` | text input with a path hint |
| `password` | password input |

`multiple` is a **separate boolean field** on `CLIOption`, not one of the eight
types — an option can be `type: "str"` *and* `multiple: true`, which renders as a
repeatable multi-value input. `value_name` (the metavar) drives the input
placeholder, and `aliases` lists every spelling (`["-o", "--output"]`).

---

## Known limitations

These are deliberate, documented trade-offs. Full reasoning is in
[SPEC.md](SPEC.md).

1. **argparse numeric types are invisible in help output.** `type=int` renders
   as `--retries RETRIES` with no textual hint, so HelpUI reports `str`. Use an
   explicit `metavar="INT"` or `ArgumentDefaultsHelpFormatter` to get `int`
   detection. (SPEC §4.6.1)
2. **`--token` is not treated as a password.** Only the words *password* /
   *passphrase* (or `PASSWORD`/`SECRET` metavars) select the password widget;
   guessing wrong would hide data the user needs to verify. A credential named
   `--token` is therefore a plain text input, while `--password` **is** a
   password input in all three fixtures. (SPEC §4.6.2)
3. **Typer without `rich` is labelled `click`.** Its output is byte-identical to
   click's; the parse is still correct, only the `parser_kind` label differs.
   (SPEC §4.2)
4. **`PATH` metavars are ambiguous.** HelpUI reads the help prose to choose
   between a file and a directory input, defaulting to file. (SPEC §4.6.3)
5. **`--subcommand "a b"` is split on whitespace** into two argv entries. There
   is no shell involved, so nothing is *executed*, but a value that looks like a
   shell snippet becomes several literal arguments. (SPEC §5.2)
6. **Colourised help** is suppressed by forcing `NO_COLOR=1`, `TERM=dumb` and
   `COLUMNS=100`; a tool that ignores these may emit ANSI escapes. (SPEC §4.6.5)
7. **Option help text is shown verbatim**, including click's `[default: x]`
   annotation. (SPEC §4.6.4)
8. **argparse required options are reported as optional.** argparse signals
   "required" through the *usage line* (`--token TOKEN` unbracketed), not in the
   option's help cell, and `argparse_parser` only looks for the literal word
   "required" in that cell — so `required` is always `False` for argparse
   options. Click appends a `[required]` annotation to the help cell and is
   parsed correctly; typer's rich option rows carry no such annotation, so typer
   options are `False` too (only typer's `*`-marked *arguments* are detected as
   required). This is a **fixable parser defect, not a framework difference**; it
   is recorded rather than worked around. (SPEC §4.6.7)

---

## Development

```console
pip install -e ".[dev]"

pytest -q              # the whole suite
ruff check .           # lint
mypy helpui            # types (strict)
```

These three are the CI checks; the suite is a few hundred tests, so no fixed
number is quoted here — run `pytest --collect-only -q` for the current figure.

### How the tests work

Three layers, each driving real artifacts rather than hand-written expectations:

* **Unit / parser tests** (`test_model`, `test_scanner`, `test_detector`,
  `test_parsers_*`, `test_scan_cli`) run the **real** fixture scripts in
  `tests/fixtures/` through the scanner. Hand-written help strings drift from
  what argparse/click/typer actually emit; driving the real fixtures caught five
  genuine parsing bugs in Phase 1, including wrapped usage lines, argparse
  epilogs, and typer's panel layout.
* **`test_generator.py`** compares the generator's output against golden files
  and asserts generated projects load and run.
* **`test_e2e.py`** boots a generated project for each fixture via
  `helpui.selftest` (the same code path as `helpui test`), submits a real job and
  walks the resulting pages and API. This is what catches template-level defects:
  a page that only breaks once a run with parameters exists (as
  `templates/record.html` once did) still returns `200` from a plain `GET /`
  smoke test.

Fixtures are runnable tools, not just help text, which is why the same scripts
serve the parser, executor and end-to-end tests:

```console
python tests/fixtures/sample_argparse.py convert --help
python tests/fixtures/sample_click.py convert --help
python tests/fixtures/sample_typer.py convert --help
```

Fixture subprocesses always run with `COLUMNS=100`, `LINES=50`, `TERM=dumb`,
`NO_COLOR=1` and `PYTHONIOENCODING=utf-8`, so help text does not reflow
differently on an 80-column CI runner and a 120-column developer terminal.

### Project layout

```
helpui/
  pyproject.toml
  SPEC.md                  # every self-made decision, with rationale
  helpui/
    cli.py                 # helpui command entry point
    model.py               # CLIOption / CLICommand / CLISpec
    scanner.py             # runs <tool> --help (shell=False, argv list)
    scanner_service.py     # scan_tool(): detect + parse + expand subcommands
    generator.py           # writes the generated project (helpui_app/ + assets)
    selftest.py            # run_project_tests(): backs `helpui test`
    parsers/
      base.py              # parser interface + text utilities
      argparse_parser.py
      click_parser.py
      typer_parser.py      # rich-panel normalisation + click reuse
      detector.py          # framework detection
    runtime/
      server.py            # serve_project(): the `helpui serve` entry point
    templates/             # 8 Jinja2 templates copied into generated projects
      _error.html          # self-contained fragment for HTMX error swaps
    static/                # htmx.min.js + style.css bundled into generated apps
  scripts/                 # one-off probes kept as fix evidence (lint-excluded)
  tests/
    conftest.py            # real-fixture harness
    fixtures/sample_{argparse,click,typer}.py
    test_model.py          test_scanner.py       test_detector.py
    test_parsers_argparse.py  test_parsers_click.py  test_parsers_typer.py
    test_scan_cli.py       test_generator.py     test_e2e.py
```

A **generated project** (what `helpui generate` writes, 24 files) looks like:

```
<out>/
  app.py                   # entry point: `python app.py`
  spec.json                # the scanned CLISpec
  README.md                # how to run this project
  helpui_app/              # 10 modules: app, config, executor, history,
                           #   paths, rendering, security, server, spec, __init__
  templates/               # 8 templates (incl. _error.html)
  static/                  # htmx.min.js, style.css
  data/                    # .gitkeep; history.db appears here at runtime
```

---

## Design principles

* **Few dependencies.** `scan` needs only the standard library; FastAPI,
  Jinja2 and uvicorn are required only by the generated app.
* **No shell, ever.** Subprocess arguments are always passed as a list with
  `shell=False`, and tests assert that injection-shaped input stays literal.
* **Fail cleanly.** Every failure path produces a stable error code, not a
  traceback — `unknown_framework` is a first-class result, not an afterthought.
* **A partially useful form beats a crash.** Parsers never raise on malformed
  help; they extract what they can and ignore the rest.

## License

MIT

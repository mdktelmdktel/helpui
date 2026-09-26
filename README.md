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

**Phase 1 of 5 is complete**: the parser layer and the `scan` command.

| Phase | Scope | Status |
|---|---|---|
| 1 | Model, parsers (argparse/click/typer), detector, `scan` CLI | ✅ Complete — 146 tests |
| 2 | Generator, golden tests | ⏳ Planned |
| 3 | Runtime: FastAPI app, forms, live logs, result rendering | ⏳ Planned |
| 4 | History, downloads, cancel, timeout, security layer | ⏳ Planned |
| 5 | `helpui test`, end-to-end suite, docs | ⏳ Planned |

`helpui generate`, `helpui serve` and `helpui test` are already declared in
`--help` but exit with `error: ... is not implemented yet (planned for phase N)`
until their phases land. This keeps the CLI contract stable from the start.

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

`--json` prints exactly one JSON document on stdout — safe to pipe. Warnings and
errors always go to **stderr**. Excerpt of a real response, trimmed for length:

```json
{
  "ok": "true",
  "schema_version": 1,
  "spec": {
    "tool_name": "sample_click",
    "tool_path": "/abs/path/sample_click.py",
    "description": "Convert an input file to another format.",
    "commands": [
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

When a scan fails, the same envelope carries a stable error code and exits `1`:

```json
{
  "ok": "false",
  "schema_version": 1,
  "code": "unknown_framework",
  "error": "could not identify the CLI framework from the help output",
  "hint": "Supported frameworks: argparse, click, typer."
}
```

Stable codes: `tool_not_found`, `no_help_output`, `unknown_framework`,
`subcommand_not_found`, `parse_failed`, `internal_error`.

### `helpui generate <tool> --out <dir> [--port 8000]`

*(Phase 2)* Scans the tool and writes a self-contained web project.

### `helpui serve <dir> [--port 8000] [--host 127.0.0.1]`

*(Phase 3)* Starts the generated project.

### `helpui test <dir>`

*(Phase 5)* Smoke-tests a generated project: starts it, `GET /`, submits one
dummy job, asserts `200`. Prints
`{"ok": ..., "project_dir": ..., "checks": [{"name": ..., "ok": ..., "detail": ...}]}`.

---

## Supported tools

Detection is based on the help output's structure. Three frameworks are
supported:

| Framework | Recognised by | Notes |
|---|---|---|
| **argparse** | lower-case `usage:` plus `positional arguments:` / `options:` | Handles wrapped usage lines, epilogs, nested subparsers and `{a,b}` choice metavars |
| **click** | capitalised `Usage:` plus `Options:` / `Commands:` | Handles `[required]`, `[default: x]`, `[env var: X]`, `[a\|b\|c]` choices, `multiple=True` |
| **typer** | rich table panels (box-drawing **or** ASCII) | Reuses the click parser; also recovers nested sub-apps such as `admin reset` |

Anything else — man-page style help, docopt, fire, a hand-rolled parser — is
reported as `unknown_framework` with exit code 1. HelpUI deliberately does not
attempt a generic help parse (see [SPEC.md](SPEC.md) §4.5).

Option types map onto form widgets:

| Detected type | Widget |
|---|---|
| `str` | text input |
| `int`, `float` | number input |
| `bool` | checkbox |
| `choice` | select |
| `file` | file upload |
| `dir` | text input with a path hint |
| `password` | password input |
| `multiple` | multi-value input |

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
   guessing wrong would hide data the user needs to verify. (SPEC §4.6.2)
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

---

## Development

```console
pip install -e ".[dev]"

pytest -q              # 146 tests
ruff check .           # lint
mypy helpui            # types (strict)
```

### How the tests work

Parser tests run the **real** fixture scripts in `tests/fixtures/` through the
scanner rather than asserting against hand-written help strings. Hand-written
strings drift from what argparse/click/typer actually emit; driving the real
fixtures caught six genuine parsing bugs in Phase 1, including wrapped usage
lines, argparse epilogs, and typer's panel layout.

Fixtures are runnable tools, so the same scripts will serve the executor tests
(Phase 3) and the end-to-end test (Phase 5):

```console
python tests/fixtures/sample_argparse.py convert --help
python tests/fixtures/sample_click.py convert --help
python tests/fixtures/sample_typer.py convert --help
```

Fixture subprocesses always run with `COLUMNS=100`, `TERM=dumb` and
`NO_COLOR=1`, so help text does not reflow differently on an 80-column CI runner
and a 120-column developer terminal.

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
    generator.py           # phase 2
    selftest.py            # phase 5
    parsers/
      base.py              # parser interface + text utilities
      argparse_parser.py
      click_parser.py
      typer_parser.py      # rich-panel normalisation + click reuse
      detector.py          # framework detection
    runtime/               # phase 3-4
  tests/
    conftest.py            # real-fixture harness
    fixtures/sample_{argparse,click,typer}.py
    test_model.py          test_scanner.py       test_detector.py
    test_parsers_argparse.py  test_parsers_click.py  test_parsers_typer.py
    test_scan_cli.py
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

# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-26

First working release. All five planned phases are complete: parsing,
generation, runtime, history/security, and the self-test command.

### Added

**Parsing (`helpui scan`)**
- `CLIOption` / `CLICommand` / `CLISpec` data model with stable JSON
  round-tripping.
- Parsers for **argparse**, **click** and **typer**. Typer reuses the click
  parser and only normalises rich table panels (both box-drawing and ASCII
  renderings).
- Feature-based framework detection with a reported confidence level;
  unsupported help output yields `unknown` rather than a wrong guess.
- Scanner that runs `<tool> --help` with `shell=False`, a list argv, a timeout,
  stdin closed, and a fixed `NO_COLOR`/`TERM`/`COLUMNS` environment.
- Subcommand expansion (nested paths such as `admin reset`) with bounded depth
  and fault tolerance.
- `helpui scan <tool> [--json] [--subcommand NAME]` with a versioned JSON
  envelope on stdout and diagnostics on stderr.
- Stable error codes: `tool_not_found`, `no_help_output`,
  `unknown_framework`, `subcommand_not_found`, `parse_failed`,
  `internal_error`.

**Generation (`helpui generate`)**
- Writes a standalone web project (`app.py`, `spec.json`, `helpui_app/`,
  `templates/`, `static/`, `data/`, `README.md`) that runs with
  `python app.py`.
- Templates and static assets are copied verbatim, so a generated project never
  requires HelpUI to be installed.
- Locally bundled htmx 2.0.4 — no CDN, no build step, no npm.

**Runtime (in generated projects)**
- Form rendering with one widget per option type: `str`, `int`, `float`,
  `bool`, `choice`, `file`, `dir`, `password`, plus multi-value inputs.
- Subprocess execution with `shell=False` and a list argv, streaming stdout and
  stderr, a configurable timeout, an output cap, and a cancel button that kills
  the process.
- Result rendering that detects JSON (array or object), CSV and plain text, and
  always HTML-escapes tool output.
- SQLite history with per-run parameters, exit code, duration, and downloads.
- Security layer: the tool path from `spec.json` is the whitelist, values are
  validated by type conversion and against the parsed choice list, uploaded
  filenames are reduced to a safe basename, and a POSIX address-space limit is
  applied where available.

**Self-test (`helpui test`)**
- 13 smoke checks per generated project, reporting
  `{"ok": "true"|"false", "checks": [...]}`. Never raises: a missing or broken
  project is reported as `ok=false` with an explanatory check.

### Fixed

Ten defects found by making the generated project actually work end to end. The
first nine were introduced during initial development; the tenth only appears on
a fresh clone.

1. The generated `app.py` referenced a non-existent `config.spec`.
2. `quote_argv_entry` was used but never imported, so every `POST /run/*`
   returned 500.
3. The `_error.html` fragment was referenced but did not exist, so validation
   failures and cancel requests returned 500.
4. `Path.write_text` translated newlines to CRLF on Windows, making generated
   bytes platform-dependent and breaking byte-for-byte golden comparison.
5. **The argv omitted the subcommand name**, so any tool with subcommands
   rejected every option (`no such option`). `build_argv` now takes the command
   path.
6. A timed-out run was reported as `failed`, because the killed child's non-zero
   exit code overwrote the status. Status precedence is now
   timeout > cancelled > failed > succeeded.
7. **click/typer `--password` degraded to a plaintext input** — two independent
   short-circuits in the type-inference `or` chain — leaking secrets in the
   rendered form.
8. File-typed positionals silently dropped uploads, so a user filling in the
   form exactly as rendered could never submit it.
9. `GET /history/{id}` always returned 500 (`record.html` iterated a dict
   without `.items()`).
10. `POST /cancel/*` froze the entire server for ~25s: the `async def` route
    called the blocking executor directly on the event loop. The executor now
    runs via `run_in_threadpool`.
11. A fresh clone produced CRLF files, which made the golden tests fail on any
    clean checkout. Fixed by committing `.gitattributes` with `eol=lf`.
12. `helpui test generated` (a relative path) could not find the project,
    because the probe child runs with `cwd=<target>`; the path is now resolved
    to an absolute one.

### Known limitations

- argparse numeric types (`type=int`) are invisible in help output, so they are
  reported as `str`. Use an explicit `metavar="INT"` or
  `ArgumentDefaultsHelpFormatter` to get `int` detection.
- **argparse's `required` is never set.** Required options are expressed by
  un-bracketed tokens in the usage line, but the parser looks for a literal
  `required` in the option's help cell, which is always absent. Click's
  `[required]` marker is parsed correctly.
- **typer's required *options* are not detected** for the same class of reason:
  typer prints `*` and `[required]` only on positional rows.
- Typer without `rich` installed is labelled `click`. Its output is identical
  to click's, so the parse is still correct — only the label differs.
- A `PATH` metavar is ambiguous; the help prose decides between a file and a
  directory widget, defaulting to file.
- A `--subcommand "a b"` value is split on whitespace into two argv entries.
  No shell is involved, so nothing is executed, but the value becomes several
  literal arguments.
- `HELPUI_TIMEOUT=0` or a negative value has undefined behaviour.
- Tools that ignore `NO_COLOR` / `TERM=dumb` may emit ANSI escapes.

[Unreleased]: https://github.com/OWNER/helpui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/helpui/releases/tag/v0.1.0

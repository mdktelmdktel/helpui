# HelpUI — Specification and Design Decisions

This document records **every trade-off HelpUI made on its own** in response to
ambiguity in the requirements, as required by the project brief. It is the
authoritative place to check "why does it behave like this?".

Status legend: **[D] Decided** (implemented and tested), **[L] Limitation**
(accepted, documented, not fixed), **[O] Open** (deferred to a later phase).

---

## 1. Scope

### 1.1 What HelpUI is

Given a non-interactive CLI tool, HelpUI runs `<tool> --help`, parses the output
into a structured `CLISpec`, and generates a standalone FastAPI + HTMX web
application that renders that spec as a form, executes the tool, and shows the
result.

### 1.2 What HelpUI is not (v1)

* No interactive TUI/REPL wrapping.
* No arbitrary help-format parsing (see §4.5).
* No authentication, multi-tenancy or RBAC.
* No Playwright/browser-automation tests.
* No remote or distributed execution.
* No frontend framework/build step.

---

## 2. Technology decisions

### [D] 2.1 Standard library over ORM

The brief allowed "SQLite (`sqlite3` or SQLModel), prefer fewer dependencies".
We chose **`sqlite3` from the standard library**. Rationale: the history table
schema is a handful of columns written by one process; an ORM adds a dependency
and a migration story for no benefit at this size. `sqlite3` also keeps the
generated project runnable with a minimal dependency set.

### [D] 2.2 `dataclasses` over Pydantic for the spec model

`CLISpec`/`CLICommand`/`CLIOption` are plain `@dataclass`es with hand-written
`to_dict`/`from_dict`. Pydantic would validate more, but FastAPI is the only
place that needs validation and it does not need the *spec* model validated —
the spec is produced by our own parser, not by a user. Using dataclasses avoids
a hard dependency in the `scan` code path entirely, so `helpui scan` works with
only the standard library available.

### [D] 2.3 `type` is a `str`, not an `Enum`

`CLIOption.type` is a plain string from the vocabulary
`str|int|float|bool|choice|file|dir|password`. Reasons:

1. `json.dumps` works with no custom encoder.
2. An unrecognised type from a future parser degrades to `str` (a text input)
   instead of raising during deserialisation — a partially useful form beats a
   crash.
3. `spec.json` stays human-readable and forward-compatible.

`CLIOption.__post_init__` enforces the vocabulary by rewriting unknown values to
`str`.

### [D] 2.4 `default` is `str | None`, not a typed value

The help text is the only source of truth, and it prints defaults as text
(`[default: out.txt]`). Parsing `"3"` into the int `3` would invent type
information the CLI never declared in its help output. The runtime
(Phase 3) converts to the declared type at submission time, where a conversion
failure is a user-visible validation error rather than a silent misparse.

---

## 3. Data model

### [D] 3.1 Frozen field names

The required model is implemented verbatim. Two **additive** fields were added
in Phase 1 because the form renderer needs them and adding them later would
change `spec.json`:

| Field | Type | Why |
|---|---|---|
| `CLIOption.value_name` | `str` | The metavar as spelled in help (`FILE`), used as the input placeholder so users see what the tool expects. |
| `CLIOption.aliases` | `list[str]` | Every spelling (`["-o", "--output"]`). Needed to *build* the argv list (Phase 3) and to show both forms in the UI. Without it, `primary_flag` cannot prefer the long form. |

Both are optional with defaults, so a `spec.json` written by an older version
still loads.

### [D] 3.2 `CLICommand.subcommands` is a flat list of names

Nested subcommands (typer's `admin reset`) are represented as:

* the root command listing `["admin"]` in `subcommands`, and
* a **separate top-level `CLICommand`** named `"admin reset"`.

Rationale: a recursive `CLICommand` tree would make `spec.json` and the
generated routes recursive, complicating both. A dotted-name flat list keeps
one command = one form = one route, preserves the hierarchy in the name, and
renders as a simple list of links.

### [D] 3.3 `name` for positionals is the metavar

Positionals have no `--flag`. `name` holds the metavar exactly as argparse/click
printed it (`input_file`, `INPUT_FILE`), and `dest` holds the Python-safe form.
The runtime normalises case when building argv.

### [D] 3.4 `required` on positionals

argparse and click positionals are required unless declared otherwise, so
HelpUI marks them `required=True` when they are not bracketed `[...]` in the
usage line.

---

## 4. Parsing

### [D] 4.1 Detection is feature-based and conservative, on help text only

`detect_parser` examines the captured help text in this order:

1. **typer** — rich table panels are present.
2. **argparse** — lower-case `usage:` *and* at least one of `positional
   arguments:` / `optional arguments:` / `options:`, and no capitalised click
   headers.
3. **click** — capitalised `Usage:` plus at least one of `Options:` /
   `Commands:` / `Arguments:`.
4. **unknown** — anything else.

A wrong guess produces a broken form, whereas `unknown` produces a clean,
actionable error, so the detector prefers `unknown` over a shaky guess.
`confidence` is reported as `high` (structural match) or `low` (heuristic) and
is surfaced by `helpui scan` for debugging.

### [D] 4.2 Typer detection needs both renderings

Typer draws its help with rich, which uses:

* **box-drawing** panels (`┌─ Options ────┐`) when colour is available, and
* **ASCII** panels (`+- Options ----+`) otherwise.

Verified against typer 0.27 + rich on Windows (GBK console). Both forms are
matched. **Typer without rich installed is textually identical to click**, so:

* the detector labels it `click`, and
* `_source_imports_typer` sniffs the target `.py` for `import typer` as a
  *tie-breaker only* — applied after the text test already said "click-shaped",
  and only for an existing `.py` file.

**[L]** A compiled/binary typer tool without rich is therefore reported as
`click`. This is harmless because typer *is* click at that layer: the parse is
correct, only the `parser_kind` label differs.

### [D] 4.3 Typer reuses the click parser

`TyperParser` subclasses `ClickParser` and only adds a normalisation step that
converts panels into click-shaped text (`typer_parser.normalise_rich_help`).
This is exactly what the brief asked for ("typer reuses click parsing logic,
differing only in detection"), and it means a click fix automatically fixes
typer.

The normaliser must run before descriptions are extracted, because
`first_paragraph` on raw panel text returns panel rows. `TyperParser.parse`
therefore re-derives `spec.description` from the normalised text.

### [D] 4.4 Framework-specific quirks handled explicitly

| Observation (verified against real fixture output) | Handling |
|---|---|
| argparse wraps its `usage:` line onto indented continuation lines | `first_paragraph` and `_usage_description` skip indented continuations, otherwise descriptions become `"COMMAND ..."` |
| argparse prints an unindented **epilog** after the last section | `split_sections` ends a section at an unindented line that follows a blank line, so the epilog is not parsed as a phantom option |
| argparse renders subcommands nested under a `COMMAND` metavar | `_subcommands_from_positionals` recovers them from the more-indented rows |
| argparse's `{json,csv}` choice metavar appears in `positional arguments:` | Treated as a choice positional, **not** as subcommand names |
| argparse's default formatter does **not** print `(default: 3)` for `type=int, default=3` | `default` is `None`. HelpUI does not invent defaults. **[L]** see §4.6 |
| click separates flags from help with **two or more spaces** | Rows split on the first multi-space run, so `-v, --verbose    Enable verbose logging` is not read as `metavar="Enable verbose logging"` |
| click appends `[default: x]`, `[required]`, `[env var: X]`, `[a\|b\|c]` at the end of the help cell | Parsed as annotations; choices come from the bracket form |
| typer splits the *flags cell itself* across columns (`--output  -o  <str>`) | `_split_row` scans leading cells while they look like flags or types, so both aliases and the type survive |
| typer marks required arguments with a leading `*` | Stripped and recorded as `required=True` |
| typer renders `[OPTIONS]`, `[ARGS]...`, `COMMAND` in the usage line | Recognised as structural placeholders, never as user-fillable arguments |
| a subcommand's usage line echoes the command path (`admin reset`) | Stripped for the known command name so no phantom positional appears |

### [D] 4.5 Only three frameworks are supported

`unknown` is a **first-class outcome**, not an error path bolted on. Nothing
attempts a generic/man-page parse, per the brief's "no arbitrary help-format
parsing". A `NAME`/`SYNOPSIS` style page yields `unknown_framework`.

### [L] 4.6 Known parsing limitations

1. **argparse numeric types are invisible.** `add_argument("--retries",
   type=int)` produces `--retries RETRIES` in help — there is no textual signal
   that it is an int. HelpUI reports `str`. *Mitigation:* the runtime accepts a
   value only if the target tool accepts it, and the resulting error surfaces in
   the log pane. A tool using
   `argparse.ArgumentDefaultsHelpFormatter` or an explicit `metavar="INT"`
   *is* typed correctly.
2. **`--token` is not a password field.** Type sniffing is conservative: only
   the words *password* / *passphrase* (in prose) or metavars
   `PASSWORD`/`SECRET`/`PASSPHRASE` select the password widget. A credential
   named `--token` renders as a plain text input, because guessing wrong *hides*
   data the user needs to verify. `--password` in the fixtures does render as a
   password input.
3. **`--config` vs `--config-file`.** A metavar of `PATH` is ambiguous; the
   prose decides (`directory|folder` → `dir`, otherwise `file`).
4. **Option descriptions are not reformatted.** `help` keeps the raw prose,
   including click's `[default: x]` annotation, so users can see exactly what
   the tool said.
5. **Multi-byte/colourised help.** `NO_COLOR=1`, `TERM=dumb` and `COLUMNS=100`
   are forced for every help invocation so output is stable and ANSI-free.
   A tool that ignores these may produce colour escapes in the UI.
6. **Windows path with spaces in a tool *string*.** `shlex` splitting of
   `--subcommand`-style strings follows the platform rules; pass a real file
   path (which is handled directly, without splitting) when the path contains
   spaces.

### [D] 4.7 Parsers never raise on malformed input

A parser that raises turns a partially usable tool into a hard failure. Each
parser returns whatever it could understand and ignores the rest. The
`scanner_service` still wraps calls defensively (`except Exception`) so a parser
bug surfaces as `parse_failed` rather than a traceback.

---

## 5. Scanner

### [D] 5.1 `shell=False` everywhere, argv as a list

Mandated by the brief and enforced by tests: `run_help` builds a list and passes
`shell=False`. Tests assert that `; rm -rf /`, `$(whoami)` and `` `id` `` reach
the child process **literally and unexpanded**.

### [D] 5.2 `subcommand` is split on whitespace into separate argv entries

`--subcommand "admin reset"` is a *path*, so it becomes two argv entries. The
consequence is documented and tested: `--subcommand "; rm -rf /"` becomes five
literal arguments rather than one. There is no shell involved, so this is a
semantics note, not a vulnerability.

### [D] 5.3 Help invocations cannot hang

Every help call:

* has a timeout (default 20s);
* forces `NO_COLOR`/`TERM=dumb`/`COLUMNS` for stable output;
* sets `stdin=DEVNULL`, so a tool that ignores `--help` and reads stdin exits
  immediately instead of hanging the scan.

### [D] 5.4 Subcommand expansion is bounded and fault-tolerant

`MAX_SUBCOMMAND_DEPTH = 2` (enough for `admin reset`). A subcommand whose help
cannot be fetched is recorded as a **warning** on `ScanReport` and printed to
stderr; the rest of the spec is still usable.

### [D] 5.5 Non-zero exit of `--help` is a failure

If the tool does not recognise a subcommand, click prints the *group's* usage
and exits non-zero. Treating that page as the subcommand's spec would silently
generate a form for the wrong command, so `_scan_one_subcommand` rejects it with
`subcommand_not_found`.

---

## 6. CLI contract

### [D] 6.1 JSON on stdout, diagnostics on stderr

`scan` and `test` emit exactly one JSON document on stdout so
`helpui scan tool --json | jq` is always safe. Warnings and human errors go to
stderr. Verified by a test asserting no extra output on stdout.

### [D] 6.2 A versioned envelope, and `ok` as a string

`scan --json` prints an *envelope*, not a bare `CLISpec`:

```json
{
  "ok": "true",
  "schema_version": 1,
  "spec": { ... CLISpec ... },
  "detection": {"parser_kind": "...", "confidence": "...", "reason": "..."},
  "warnings": []
}
```

The brief said "stable, machine-readable fields". The spec itself stays exactly
the required shape under `spec`, while the envelope carries detection
confidence and warnings — information `generate` needs and users debugging a
scan want. `schema_version` lets a future change be detected instead of
silently misread.

**`ok` is the string `"true"`/`"false"`, not a JSON boolean** — matching the
brief's literal `{"ok": true/false, "checks": [...]}` example for `test`. The
same convention is used by `scan` so the two commands are consistent. A caller
can safely test `payload["ok"] == "false"`. **[O]** If this proves awkward for
consumers, Phase 5 may emit a real boolean in both commands; the decision is
recorded here so the change is deliberate.

### [D] 6.3 Error codes are a closed, stable set

| Code | Meaning |
|---|---|
| `tool_not_found` | The tool does not exist / is not on PATH |
| `no_help_output` | Running the tool failed, or it printed nothing usable |
| `unknown_framework` | Help was captured but is not argparse/click/typer |
| `subcommand_not_found` | The named subcommand does not exist |
| `parse_failed` | A parser raised (defensive; should not happen) |
| `internal_error` | Anything unexpected, so a pipe never sees a traceback |

### [D] 6.4 Exit codes

`0` success, `1` operation failed, `2` bad CLI usage (argparse's default).

### [D] 6.5 Phase-1 stubs for later subcommands

`generate`, `serve` and `test` exist in `--help` from Phase 1 so the contract is
fixed early. Their modules are declared with correct type signatures but raise
`NotImplementedError`; the CLI catches the resulting `ImportError` path and
reports `error: ... is not implemented yet (planned for phase N)` with exit 1.
This keeps `mypy` honest and the CLI stable while later phases fill them in.

---

## 7. Testing decisions

### [D] 7.1 Tests drive the real fixtures, not hand-written help strings

Every parser test runs the actual `tests/fixtures/sample_*.py` through the
scanner (`tests/conftest.help_text`). Hand-written help strings drift from what
the libraries really emit — this approach caught six real bugs in Phase 1,
including:

* a wrapped argparse `usage:` line being parsed as the description;
* argparse's epilog being parsed as a phantom option;
* `{json,csv,tsv}` in a subcommand's usage line being expanded into fake
  subcommands (`convert json`, `convert csv`, ...);
* typer's box-drawing panels losing their section headers;
* typer's `--output -o` losing the short alias.

### [D] 7.2 A fixed width is forced for fixture subprocesses

`COLUMNS=100`, `LINES=50`, `TERM=dumb`, `NO_COLOR=1` are set for every fixture
subprocess. Without this, argparse/click/typer reflow usage text to the terminal
width and the parse (and the Phase 2 golden files) would differ between an
80-column CI runner and a 120-column developer terminal.

### [D] 7.3 Fixtures are runnable tools, not just help text

The fixtures accept real arguments and do real (harmless) work, so the same
scripts serve the Phase 3 executor tests and the Phase 5 end-to-end test.

---

## 8. Cross-phase contracts established in Phase 1

These are recorded now because later phases depend on them:

1. `CLISpec.to_dict()/from_dict()` is the `spec.json` format. Round-trip
   equality is tested, and unknown keys are ignored for forward compatibility.
2. Every parser implements `parse()` and `parse_subcommand()`. A subcommand page
   is parsed with the same code as a root page; the only difference is the
   removal of subcommand artifacts (echoed command name, `COMMAND` metavar).
3. `ScanFailure.code` values (§6.3) are the error vocabulary for `scan`.
4. `GenerationResult`, `SelfTestResult`/`Check` and `serve_project` are declared
   with final signatures, so phase 2/3/5 implement rather than redesign.

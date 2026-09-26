# Contributing

Thanks for taking a look at HelpUI. This document covers how to get set up, how
the code is organised, and the conventions the project follows.

## Setup

Requires **Python 3.11+**.

```console
git clone https://github.com/OWNER/helpui.git
cd helpui
python -m pip install -e ".[dev]"
```

`click` and `typer` live in the `dev` extra on purpose: they are the frameworks
HelpUI *parses*, not libraries it needs at runtime. As a result `helpui scan`
itself runs on a minimal install, with only the standard library.

## Checks

Everything CI runs, locally:

```console
ruff check .        # lint (helpui/ and tests/; scripts/ is excluded, see below)
mypy helpui         # strict type checking
pytest -q           # the full suite
```

On Windows, make sure your shell is UTF-8 (`PYTHONIOENCODING=utf-8`); the test
fixtures and generated templates contain characters a GBK console cannot print.

## How the code is organised

```
helpui/
  cli.py             the `helpui` entry point (thin: parse args, delegate)
  model.py           CLIOption / CLICommand / CLISpec — the frozen contract
  scanner.py         runs `<tool> --help`; the only place that executes a tool
  scanner_service.py detect + parse + expand subcommands; owns the error codes
  parsers/           one module per framework, plus shared text utilities
  generator.py       writes a standalone project from a CLISpec
  selftest.py        `helpui test`: drives a generated project end to end
  templates/         Jinja2 templates, copied verbatim into generated projects
  static/            style.css and a bundled htmx (no CDN)
```

### The two halves of the project

It helps to keep these mentally separate:

1. **HelpUI itself** (`helpui/`) — parses help output and writes projects.
2. **The generated project** (`helpui_app/`) — FastAPI app, executor, history,
   security. This code exists as *strings inside* `generator.py`
   (`_fastapi_app_source()`, `_executor_source()`, ...), because it has to be
   written into the user's directory.

**Editing a generated behaviour means editing the corresponding
`_*_source()` function in `generator.py`** — not a file under `helpui_app/`,
which only ever exists in a generated output directory. Likewise, editing
`templates/` changes both HelpUI and every project it generates.

## Test conventions

**Driving the real fixtures beats hand-written help strings.** The fixtures in
`tests/fixtures/` are runnable argparse/click/typer tools. Parser tests execute
them and parse the real output, because help output drifts from what the
libraries actually emit. Hand-written strings hide that drift; the real fixtures
have caught multiple genuine bugs.

**Keep fixtures deterministic.** Help text is reflowed to the terminal width, so
every fixture subprocess runs with `COLUMNS=100`, `TERM=dumb` and `NO_COLOR=1`
(see `tests/conftest.py`). Without this, an 80-column CI runner and a
120-column developer terminal would parse differently.

**Golden files are compared byte for byte.** The generator copies templates
verbatim; the tests assert the copies are byte-identical to the packaged source.
This is also why `.gitattributes` pins `eol=lf` — CRLF on checkout makes
generation non-reproducible.

**Never import a generated project into the test process.** Tests that exercise
a generated app run it in a subprocess (see `tests/test_e2e.py`), so its
`helpui_app` package cannot collide with anything in `sys.modules`.

**Probes: report conclusion lines, not logs.** `scripts/` holds one-off
verification probes written while chasing a specific bug. They print a few
`check: value` lines and exit — they are evidence for a fix, not shipped code,
so the directory is excluded from lint. When investigating a bug, prefer writing
one of these over pasting logs into a conversation.

## Conventions

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(scope): ...`, `fix(scope): ...`, `docs: ...`, `chore: ...`.
- **Docstrings** explain *why*, not *what*. Where a decision is non-obvious,
  record the reasoning — a future reader should not have to re-derive it.
- **No new heavyweight dependencies.** HelpUI deliberately keeps `scan` on the
  standard library. If a dependency is genuinely needed, add it to the `dev`
  extra or the runtime list in `pyproject.toml` with a note in the PR
  description, and justify it in `SPEC.md`.
- **Type annotations** on all code under `helpui/`; `mypy` runs in strict mode.
- **Never build a shell string.** Subprocess arguments are always passed as a
  list with `shell=False`. Tests assert that injection-shaped input stays
  literal.

## Where decisions live

`SPEC.md` is the record of every trade-off the project made on its own: what is
supported, what is deliberately not, and the reasoning behind each generator
decision. If you change a behaviour, update `SPEC.md` in the same PR.
`CHANGELOG.md` tracks user-visible changes.

## Reporting a bug

Include:

- the tool you pointed HelpUI at (or a minimal reproduction of its `--help`),
- `helpui scan <tool> --json` output, which records the detected framework and
  any warnings,
- what you expected instead.

A help output that HelpUI mis-parses is the most valuable kind of report — it is
exactly what the fixture-driven parser tests exist to catch.

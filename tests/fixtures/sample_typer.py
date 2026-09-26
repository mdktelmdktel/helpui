#!/usr/bin/env python3
"""Fixture: a ``typer`` CLI used by HelpUI's parser tests.

Typer renders help through click, so the *parsing* logic is shared with the
click parser; only detection differs (typer may add rich panels).  This fixture
covers typer's own idioms: ``Annotated`` options, ``Enum`` choices, ``bool``
flags, ``list[str]`` multi-values, and sub-apps.

Run ``python tests/fixtures/sample_typer.py --help`` to inspect the output.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Sample typer tool used by HelpUI tests.", no_args_is_help=True)
admin = typer.Typer(help="Administrative commands.")
app.add_typer(admin, name="admin")


class OutputFormat(str, Enum):  # noqa: UP042 - the (str, Enum) idiom is what typer documents
    """Output format choices.

    Typer renders this as a choice list (``<json|csv|tsv>``). ``StrEnum`` would
    work too, but ``(str, Enum)`` is the form typer's documentation uses and it
    also works on the 3.11 floor this project targets.
    """

    json = "json"
    csv = "csv"
    tsv = "tsv"


@app.command()
def convert(
    input_file: Annotated[Path, typer.Argument(help="File to convert.")],
    output: Annotated[str, typer.Option("--output", "-o", help="Output path.")] = "out.txt",
    output_format: Annotated[OutputFormat, typer.Option("--format", help="Output format.")] = OutputFormat.json,
    token: Annotated[str, typer.Option(help="API token used for the upload.")] = "",
    retries: Annotated[int, typer.Option(help="Number of retries.")] = 3,
    ratio: Annotated[float, typer.Option(help="Scaling ratio.")] = 1.5,
    tag: Annotated[list[str] | None, typer.Option(help="Tag to attach (repeatable).")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose logging.")] = False,
    password: Annotated[str, typer.Option(help="Password for the archive.")] = "",
) -> None:
    """Convert an input file to another format."""
    payload = {
        "command": "convert",
        "input_file": str(input_file),
        "output": output,
        "format": output_format.value,
        "retries": retries,
        "ratio": ratio,
        "tag": tag or [],
        "verbose": verbose,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    typer.echo(json.dumps(payload, indent=2))


@app.command()
def report(
    limit: Annotated[int, typer.Option(help="Maximum number of rows.")] = 10,
    sort: Annotated[str, typer.Option(help="Sort key for the report.")] = "name",
    outdir: Annotated[str, typer.Option(help="Directory for generated reports.")] = "reports",
) -> None:
    """Generate a summary report."""
    typer.echo(json.dumps({"command": "report", "limit": limit, "sort": sort, "outdir": outdir}))


@admin.command("reset")
def admin_reset(
    force: Annotated[bool, typer.Option("--force", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Reset the local cache."""
    typer.echo(json.dumps({"command": "admin reset", "force": force}))


if __name__ == "__main__":
    sys.exit(app())

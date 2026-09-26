#!/usr/bin/env python3
"""Fixture: a ``click`` CLI used by HelpUI's parser tests.

Covers click-specific help features that the click parser must understand:
``Options:`` / ``Commands:`` sections, ``[required]``, ``[default: x]``,
``[env var: X]``, ``[possible values: ...]`` (click >= 8.2) or
``[a|b|c]`` choice rendering, ``count=True`` flags, and repeatable options.

Run ``python tests/fixtures/sample_click.py --help`` to inspect the output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging.")
@click.option("--config", type=click.Path(exists=False), default="config.toml", help="Path to the config file.")
@click.version_option("1.0.0")
def cli(verbose: bool, config: str) -> None:
    """Sample click tool used by HelpUI tests."""


@cli.command()
@click.argument("input_file", type=click.Path(exists=False))
@click.option("-o", "--output", default="out.txt", show_default=True, help="Output path.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv", "tsv"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.option("--token", required=True, envvar="HELPUI_TOKEN", help="API token used for the upload.")
@click.option("--retries", type=int, default=3, show_default=True, help="Number of retries.")
@click.option("--ratio", type=float, default=1.5, show_default=True, help="Scaling ratio.")
@click.option("--tag", multiple=True, help="Tag to attach (repeatable).")
@click.option("--password", prompt=False, default=None, help="Password for the archive.")
@click.option("--dry-run", is_flag=True, help="Do not write anything.")
def convert(
    input_file: str,
    output: str,
    output_format: str,
    token: str,
    retries: int,
    ratio: float,
    tag: tuple[str, ...],
    password: str | None,
    dry_run: bool,
) -> None:
    """Convert an input file to another format."""
    payload = {
        "command": "convert",
        "input_file": input_file,
        "output": output,
        "format": output_format,
        "token": token,
        "retries": retries,
        "ratio": ratio,
        "tag": list(tag),
        "dry_run": dry_run,
    }
    if not dry_run:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    click.echo(json.dumps(payload, indent=2))


@cli.command()
@click.option("--limit", type=int, default=10, show_default=True, help="Maximum number of rows.")
@click.option(
    "--sort",
    type=click.Choice(["name", "size", "date"]),
    default="name",
    show_default=True,
    help="Sort key for the report.",
)
@click.option("--outdir", type=click.Path(file_okay=False), default="reports", help="Directory for reports.")
@click.option("--verbose", "-V", count=True, help="Increase verbosity (repeatable).")
def report(limit: int, sort: str, outdir: str, verbose: int) -> None:
    """Generate a summary report."""
    click.echo(json.dumps({"command": "report", "limit": limit, "sort": sort, "outdir": outdir, "verbose": verbose}))


if __name__ == "__main__":
    sys.exit(cli())

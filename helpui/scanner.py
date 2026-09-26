"""Run ``<tool> --help`` and capture its output.

The scanner never uses a shell: the tool path plus arguments are handed to
:func:`subprocess.run` as a list with ``shell=False``.  Help output is captured
from *both* stdout and stderr because some tools (and argparse in certain
configurations) print usage on stderr.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Seconds allowed for a help invocation. ``--help`` must never hang a scan.
DEFAULT_HELP_TIMEOUT: float = 20.0


class ScanError(RuntimeError):
    """Raised when help output cannot be obtained at all."""


@dataclass
class HelpResult:
    """Raw result of a help invocation."""

    argv: list[str]
    stdout: str
    stderr: str
    returncode: int

    @property
    def text(self) -> str:
        """stdout when it has content, otherwise stderr.

        Used by the detector, which only needs "the help text".
        """
        return self.stdout if self.stdout.strip() else self.stderr

    @property
    def combined(self) -> str:
        """stdout followed by stderr, for parsers that must see everything."""
        if self.stdout and self.stderr:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr


def as_tool_argv(tool: str) -> list[str]:
    """Split a user-supplied tool string into an argv prefix.

    ``"mytool"`` -> ``["mytool"]``; ``"python demo.py"`` -> ``["python", "demo.py"]``.
    Quoting follows POSIX rules, which is a documented limitation on Windows
    for paths containing spaces -- pass such tools as ``tool_path=<file>`` via
    :func:`resolve_tool` instead (the CLI does this automatically for existing
    files).
    """
    tool = tool.strip()
    if not tool:
        raise ScanError("empty tool name")
    if os.path.exists(tool) and tool.lower().endswith((".py", ".pyw")):
        return [*_python_argv(), str(Path(tool).resolve())]
    parts = shlex.split(tool, posix=os.name != "nt")
    if not parts:
        raise ScanError(f"could not parse tool string: {tool!r}")
    return parts


def _python_argv() -> list[str]:
    """A python executable usable as a subprocess prefix."""
    import sys

    return [sys.executable or "python"]


def resolve_tool(tool: str) -> list[str]:
    """Resolve ``tool`` to an argv prefix that is safe to execute directly.

    Supports three forms:

    * a path to an existing ``.py`` file  -> ``[python, <abs path>]``
    * a path to an existing executable    -> ``[<abs path>]``
    * a bare command name                 -> ``[<abs path from PATH>]``

    Raising early (instead of letting ``subprocess`` fail) gives ``helpui scan``
    a clean error message on an unknown tool.
    """
    if not tool or not tool.strip():
        raise ScanError("empty tool name")

    # A single token that looks like a path to a Python script.
    if tool.lower().endswith((".py", ".pyw")):
        path = Path(tool)
        if not path.is_file():
            raise ScanError(f"python script not found: {tool}")
        return [*_python_argv(), str(path.resolve())]

    # A path to an existing file/dir, possibly containing spaces.
    candidate = Path(tool)
    if candidate.is_file():
        return [str(candidate.resolve())]

    # Otherwise: a command name that must exist on PATH.
    if len(tool.split()) == 1 and (found := shutil.which(tool)) is not None:
        return [found]

    # Last resort: a multi-token command line such as "python -m mytool".
    argv = as_tool_argv(tool)
    if shutil.which(argv[0]) is None and not Path(argv[0]).exists():
        raise ScanError(f"tool not found on PATH: {argv[0]!r}")
    return argv


def run_help(
    tool_argv: list[str],
    *,
    subcommand: str | None = None,
    timeout: float = DEFAULT_HELP_TIMEOUT,
    env: dict[str, str] | None = None,
) -> HelpResult:
    """Invoke ``tool_argv + [subcommand?, "--help"]`` and capture the output.

    ``shell=False`` is mandatory: the argument list is passed to the OS as-is.

    ``subcommand`` may name a nested path (``"admin reset"``); it is split on
    whitespace into separate argv entries.  Every resulting entry is a single
    literal argument, so a value such as ``"; rm -rf /"`` is never interpreted
    by a shell -- but note that it *is* split into several arguments, which is
    the documented behaviour for multi-word command paths.
    """
    argv = [*tool_argv]
    if subcommand:
        argv.extend(subcommand.split())
    argv.append("--help")

    child_env = dict(os.environ if env is None else env)
    # Keep help output stable: no colour escapes, no paging, fixed width.
    child_env.setdefault("NO_COLOR", "1")
    child_env.setdefault("TERM", "dumb")
    child_env.setdefault("COLUMNS", "100")
    # Never let a tool that ignores --help start an interactive REPL waiting on
    # stdin; closing stdin makes it exit instead of hanging the scan.
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScanError(f"`{' '.join(argv)}` timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise ScanError(f"could not run `{' '.join(argv)}`: {exc}") from exc

    return HelpResult(
        argv=argv,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        returncode=completed.returncode,
    )


def tool_display_name(tool_argv: list[str]) -> str:
    """A human-friendly tool name derived from an argv prefix.

    ``[python, /x/sample_argparse.py]`` -> ``"sample_argparse"``.
    """
    if not tool_argv:
        return "tool"
    last = tool_argv[-1]
    if last.endswith(".py") or last.endswith(".pyw"):
        return Path(last).stem
    return Path(last).name or last


def run_subcommand_help(
    tool_argv: list[str],
    command_path: list[str],
    *,
    timeout: float = DEFAULT_HELP_TIMEOUT,
) -> HelpResult:
    """Fetch help for a (possibly nested) subcommand path.

    ``run_subcommand_help(argv, ["admin", "reset"])`` runs
    ``tool admin reset --help``.
    """
    return run_help(tool_argv, subcommand=" ".join(command_path), timeout=timeout)

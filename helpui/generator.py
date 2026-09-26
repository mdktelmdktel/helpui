"""Project generator: turn a :class:`~helpui.model.CLISpec` into a WebUI directory.

Phase 1 establishes the module and its public error type so that ``helpui
generate`` can be wired up and type-checked; the actual template rendering
lands in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from helpui.model import CLISpec


class GenerationError(RuntimeError):
    """Raised when a project directory cannot be created."""


@dataclass
class GenerationResult:
    """Where a generated project lives and what it contains."""

    out_dir: Path
    files: list[Path]
    spec: CLISpec


def generate_project(
    spec: CLISpec,
    out_dir: str | Path,
    *,
    default_port: int = 8000,
    default_host: str = "127.0.0.1",
    force: bool = False,
) -> GenerationResult:
    """Write a standalone WebUI project for ``spec`` into ``out_dir``.

    Implemented in Phase 2.
    """
    raise NotImplementedError("generate_project is implemented in phase 2")

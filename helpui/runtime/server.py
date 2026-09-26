"""Uvicorn launcher for a generated WebUI project.

Phase 1 provides the typed entry point used by ``helpui serve``; the FastAPI
application it serves is built in Phase 3.
"""

from __future__ import annotations

from pathlib import Path


def serve_project(
    project_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> int:
    """Run the generated project in ``project_dir`` until interrupted.

    Implemented in Phase 3.
    """
    raise NotImplementedError("serve_project is implemented in phase 3")

"""Runtime package: the FastAPI app that a generated project runs.

Phase 1 declares the package so that ``helpui.cli`` can import the server
entry point and type-check; the app factory and executor arrive in Phase 3.
"""

from __future__ import annotations

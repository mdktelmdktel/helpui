"""``helpui test``: smoke-test a generated WebUI project.

Phase 1 defines the result shape (so ``helpui test`` output is a stable JSON
contract from the start); the actual checks land in Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Check:
    """A single smoke-test assertion."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class SelfTestResult:
    """Overall outcome of ``helpui test``."""

    ok: bool
    checks: list[Check] = field(default_factory=list)
    project_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_dir": self.project_dir,
            "checks": [c.to_dict() for c in self.checks],
        }


def run_project_tests(project_dir: str | Path, *, timeout: float = 60.0) -> SelfTestResult:
    """Run the smoke test suite against a generated project.

    Implemented in Phase 5.
    """
    raise NotImplementedError("run_project_tests is implemented in phase 5")

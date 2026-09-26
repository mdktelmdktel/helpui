"""HelpUI: turn any non-interactive CLI's ``--help`` into a local WebUI.

The public surface is intentionally small:

* :mod:`helpui.model` -- the :class:`~helpui.model.CLISpec` data model.
* :mod:`helpui.scanner` -- runs ``<tool> --help`` and captures its output.
* :mod:`helpui.parsers` -- turns captured help text into a ``CLISpec``.
* :mod:`helpui.generator` -- writes a standalone WebUI project directory.
* :mod:`helpui.runtime` -- the generated app's FastAPI runtime.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]

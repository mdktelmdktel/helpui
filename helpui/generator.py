"""Generate a standalone WebUI project from a :class:`~helpui.model.CLISpec`.

The generator is a *pure file writer*: it takes a spec and a destination
directory and produces a project that runs with ``python app.py``.

Generated layout (fixed by the brief)::

    <out>/
      app.py            # entry point: `python app.py`
      spec.json         # the scanned CLISpec
      helpui_app/       # the application package (importable, testable)
        __init__.py
        app.py          # FastAPI app factory
        config.py       # ports, timeouts, limits
        executor.py     # subprocess execution
        history.py      # SQLite history
        security.py     # whitelist, validation, limits
        rendering.py    # JSON/CSV/text result rendering
        runtime.py      # sitecustomize-free shim: locates the project dir
      templates/        # Jinja2 templates
      static/           # style.css + htmx.min.js (bundled, no CDN)
      data/             # created at runtime: history.db
      README.md

Decisions recorded in SPEC.md:

* The application lives in a **package** (``helpui_app``) rather than loose
  modules, so ``python app.py`` and ``pytest`` can both import it without
  ``sys.path`` hacks and without name clashes with the user's tools.
* ``data/history.db`` is **not** created by the generator. It is created on
  first run, so a freshly generated project is a set of plain source files.
  The ``data/`` directory *is* created (with a ``.gitkeep``) so the intent is
  visible and the path exists.
* Templates and static assets are copied verbatim from the installed package,
  so a generated project never depends on HelpUI being installed to *run* --
  only its own dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from helpui.model import CLISpec

#: Files copied verbatim from ``helpui/templates`` into the generated project.
TEMPLATES: tuple[str, ...] = (
    "base.html",
    "index.html",
    "form.html",
    "history.html",
    "result.html",
    "record.html",
    "_widget.html",
    "_error.html",
)

#: Files copied verbatim from ``helpui/static``.
STATIC_FILES: tuple[str, ...] = ("style.css", "htmx.min.js")

#: Default values baked into the generated ``config.py``.
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5 MiB of captured stdout+stderr
DEFAULT_MAX_MEMORY_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB address-space cap (POSIX only)
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB per uploaded file
DEFAULT_MAX_UPLOAD_FILES = 16
DEFAULT_HISTORY_LIMIT = 200


class GenerationError(RuntimeError):
    """Raised when a project directory cannot be created."""


@dataclass
class GenerationResult:
    """Where a generated project lives and what it contains."""

    out_dir: Path
    files: list[Path]
    spec: CLISpec

    def file_names(self) -> list[str]:
        """Relative POSIX paths of every generated file, sorted."""
        return sorted(p.relative_to(self.out_dir).as_posix() for p in self.files)


def generate_project(
    spec: CLISpec,
    out_dir: str | Path,
    *,
    default_port: int = 8000,
    default_host: str = "127.0.0.1",
    force: bool = False,
) -> GenerationResult:
    """Write a standalone WebUI project for ``spec`` into ``out_dir``.

    Raises :class:`GenerationError` if the directory exists and is not empty
    and ``force`` is not set -- refusing is safer than silently overwriting
    whatever the user pointed ``--out`` at.
    """
    target = Path(out_dir).expanduser()
    _prepare_directory(target, force=force)

    written: list[Path] = []

    # -- the specification -------------------------------------------------
    spec_path = target / "spec.json"
    _write_text_lf(spec_path, spec.to_json())
    written.append(spec_path)

    # -- executable entry point --------------------------------------------
    app_path = target / "app.py"
    _write_text_lf(app_path, _app_entry_source())
    written.append(app_path)

    # -- application package ------------------------------------------------
    package = target / "helpui_app"
    package.mkdir(parents=True, exist_ok=True)
    written.append(package / "__init__.py")
    _write_text_lf(package / "__init__.py", _package_init_source(spec))

    for name, source in (
        ("config.py", _config_source(default_port=default_port, default_host=default_host)),
        ("paths.py", _paths_source()),
        ("spec.py", _spec_source()),
        ("security.py", _security_source()),
        ("executor.py", _executor_source()),
        ("history.py", _history_source()),
        ("rendering.py", _rendering_source()),
        ("app.py", _fastapi_app_source()),
        ("server.py", _server_source()),
    ):
        path = package / name
        _write_text_lf(path, source)
        written.append(path)

    # -- templates and static assets ---------------------------------------
    templates_dir = target / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATES:
        path = templates_dir / name
        # Copied verbatim so the generated project is byte-identical to the
        # packaged source -- this is what makes it independent of HelpUI.
        _write_text_lf(path, _read_package_file("templates", name))
        written.append(path)

    static_dir = target / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        path = static_dir / name
        path.write_bytes(_read_package_bytes("static", name))
        written.append(path)

    # -- data directory (history.db is created at runtime) ------------------
    data_dir = target / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    keep = data_dir / ".gitkeep"
    _write_text_lf(keep, "")
    written.append(keep)

    # -- project README -----------------------------------------------------
    readme = target / "README.md"
    _write_text_lf(
        readme,
        _project_readme(spec, default_port=default_port, default_host=default_host),
    )
    written.append(readme)

    return GenerationResult(out_dir=target, files=written, spec=spec)


# -- helpers -----------------------------------------------------------------


def _prepare_directory(target: Path, *, force: bool) -> None:
    """Create ``target``, refusing to clobber a non-empty directory."""
    if target.exists():
        if not target.is_dir():
            raise GenerationError(f"{target} exists and is not a directory")
        existing = [p for p in target.iterdir() if p.name not in {".git", ".gitignore"}]
        if existing and not force:
            raise GenerationError(
                f"{target} is not empty (contains {len(existing)} entries); "
                "pass force=True (CLI: --force) to overwrite"
            )
    else:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GenerationError(f"could not create {target}: {exc}") from exc


def _read_package_file(folder: str, name: str) -> str:
    """Read a UTF-8 text asset that ships inside the helpui package.

    Newlines are normalised to ``\\n`` so the *source* of a generated file does
    not depend on how the package was checked out on disk.
    """
    try:
        return (
            resources.files("helpui").joinpath(folder, name).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise GenerationError(f"packaged asset {folder}/{name} is missing: {exc}") from exc


def _write_text_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with **LF** newlines, whatever the host platform.

    ``Path.write_text`` translates ``\\n`` to ``os.linesep``, which would make
    the generated bytes differ between Windows and Linux for identical input.
    That breaks golden comparison and means a project generated on Windows
    carries CRLF throughout. Writing with ``newline=""`` after normalising to
    ``\\n`` pins the output to LF everywhere, so generation is reproducible.
    """
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="")


def _read_package_bytes(folder: str, name: str) -> bytes:
    """Read a binary asset that ships inside the helpui package."""
    try:
        return resources.files("helpui").joinpath(folder, name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise GenerationError(f"packaged asset {folder}/{name} is missing: {exc}") from exc


def _json(value: object) -> str:
    """Render a Python literal as JSON, for embedding into generated source."""
    return json.dumps(value, indent=4, ensure_ascii=False)


def _app_entry_source() -> str:
    """The ``app.py`` users run with ``python app.py``."""
    return '''#!/usr/bin/env python3
"""Entry point for this generated HelpUI project.

Run it::

    python app.py                 # uses the host/port from config.py
    python app.py --port 9000     # override
    python app.py --host 0.0.0.0  # override

This file is generated by HelpUI. Edit it only if you accept that regenerating
will overwrite it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project importable when run as `python app.py` from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpui_app.config import load_config  # noqa: E402
from helpui_app.server import run  # noqa: E402
from helpui_app.spec import load_spec  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    config = load_config()
    spec = load_spec(config.spec_path)
    parser = argparse.ArgumentParser(
        prog="app.py",
        description=f"WebUI for {spec.tool_name}, generated by HelpUI.",
    )
    parser.add_argument("--host", default=config.host, help=f"host to bind (default: {config.host})")
    parser.add_argument("--port", type=int, default=config.port, help=f"port to bind (default: {config.port})")
    parser.add_argument("--reload", action="store_true", help="auto-reload on source changes")
    args = parser.parse_args(argv)

    run(config, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _package_init_source(spec: CLISpec) -> str:
    name = spec.tool_name
    return f'''"""WebUI application generated by HelpUI for the `{name}` tool.

Modules:

* :mod:`helpui_app.config`   -- paths, ports, limits loaded from config.toml
* :mod:`helpui_app.paths`    -- project-relative path helpers
* :mod:`helpui_app.security` -- command whitelist and argument validation
* :mod:`helpui_app.executor` -- subprocess execution with streaming output
* :mod:`helpui_app.history`  -- SQLite run history
* :mod:`helpui_app.rendering`-- JSON / CSV / text result rendering
* :mod:`helpui_app.app`      -- the FastAPI application factory
* :mod:`helpui_app.server`   -- uvicorn launcher

Generated code -- see README.md in this directory.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
'''


def _config_source(*, default_port: int, default_host: str) -> str:
    return f'''"""Configuration for this generated HelpUI project.

Values may be overridden with environment variables, which keeps the project
deployable without editing generated files::

    HELPUI_PORT=9000 HELPUI_TIMEOUT=60 python app.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from helpui_app.paths import data_dir, project_dir

#: Defaults baked in at generation time (see the `--port` / `--host` flags of
#: `helpui generate`).
DEFAULT_PORT = {default_port}
DEFAULT_HOST = {default_host!r}

#: Kill the subprocess after this many seconds. The brief's default is 300.
DEFAULT_TIMEOUT_SECONDS = {DEFAULT_TIMEOUT_SECONDS!r}

#: Cap on captured stdout+stderr, to bound memory on a chatty tool.
DEFAULT_MAX_OUTPUT_BYTES = {DEFAULT_MAX_OUTPUT_BYTES!r}

#: Address-space limit applied to the child on POSIX (RLIMIT_AS). Skipped with
#: a recorded note on Windows, where no equivalent exists.
DEFAULT_MAX_MEMORY_BYTES = {DEFAULT_MAX_MEMORY_BYTES!r}

#: Cap on a single uploaded file, and on how many files one run may upload.
DEFAULT_MAX_UPLOAD_BYTES = {DEFAULT_MAX_UPLOAD_BYTES!r}
DEFAULT_MAX_UPLOAD_FILES = {DEFAULT_MAX_UPLOAD_FILES!r}

#: How many history rows the history page shows.
DEFAULT_HISTORY_LIMIT = {DEFAULT_HISTORY_LIMIT!r}


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    project_dir: Path
    data_dir: Path
    templates_dir: Path
    static_dir: Path
    db_path: Path
    upload_dir: Path
    host: str
    port: int
    timeout_seconds: float
    max_output_bytes: int
    max_memory_bytes: int
    max_upload_bytes: int
    max_upload_files: int
    history_limit: int

    @property
    def spec_path(self) -> Path:
        return self.project_dir / "spec.json"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_config() -> Config:
    """Build the runtime configuration, honouring HELPUI_* environment vars."""
    root = project_dir()
    uploads = data_dir() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return Config(
        project_dir=root,
        data_dir=data_dir(),
        templates_dir=root / "templates",
        static_dir=root / "static",
        db_path=data_dir() / "history.db",
        upload_dir=uploads,
        host=os.environ.get("HELPUI_HOST", DEFAULT_HOST),
        port=_env_int("HELPUI_PORT", DEFAULT_PORT),
        timeout_seconds=_env_float("HELPUI_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        max_output_bytes=_env_int("HELPUI_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES),
        max_memory_bytes=_env_int("HELPUI_MAX_MEMORY_BYTES", DEFAULT_MAX_MEMORY_BYTES),
        max_upload_bytes=_env_int("HELPUI_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
        max_upload_files=_env_int("HELPUI_MAX_UPLOAD_FILES", DEFAULT_MAX_UPLOAD_FILES),
        history_limit=_env_int("HELPUI_HISTORY_LIMIT", DEFAULT_HISTORY_LIMIT),
    )
'''


def _paths_source() -> str:
    return '''"""Project-relative path helpers.

Everything is resolved relative to the *project directory* (the one containing
``app.py``), not the current working directory, so the app behaves the same
however it is launched.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def project_dir() -> Path:
    """The directory containing this generated project."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable state directory; created on demand."""
    target = project_dir() / "data"
    target.mkdir(parents=True, exist_ok=True)
    return target


def spec_path() -> Path:
    """Location of the scanned ``spec.json``."""
    return project_dir() / "spec.json"
'''


def _security_source() -> str:
    return '''"""Security layer: command whitelist, argument validation, resource limits.

Every rule here exists because the web UI accepts untrusted input and turns it
into a process launch. See SPEC.md for the rationale behind each decision.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from helpui_app.config import Config

#: Characters/tokens that must never reach a filename on disk.
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ValidationError(ValueError):
    """A submitted value was rejected before any process was started."""


@dataclass(frozen=True)
class ValidatedValue:
    """One validated parameter, ready to be placed in an argv list."""

    dest: str
    flag: str | None
    """The argv flag to emit, or None for positionals."""

    values: tuple[str, ...]
    """One entry per argv token. Always a tuple; never a shell string."""


def tool_argv(tool_path: str) -> list[str]:
    """Resolve the whitelisted tool path into an argv prefix.

    This is the **only** place a tool path is turned into something executable.
    The path comes from ``spec.json``, which was written by ``helpui scan`` --
    never from the request. That is the whitelist: whatever `scan` recorded is
    the single command this project may run.
    """
    if not tool_path:
        raise ValidationError("the project has no tool path recorded")

    path = Path(tool_path)
    if not path.exists():
        raise ValidationError(f"the whitelisted tool no longer exists: {tool_path}")

    if path.suffix in {".py", ".pyw"}:
        return [sys.executable or "python", str(path)]
    if os.access(path, os.X_OK):
        return [str(path)]
    # A non-executable file with no recognised suffix: try the interpreter so
    # a tool that lost its +x bit still works after an archive round-trip.
    return [sys.executable or "python", str(path)]


def sanitise_filename(name: str) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips directory components (``..``, ``/``, ``\\\\``), keeps only
    ``[A-Za-z0-9._-]``, refuses leading dots, and falls back to a generated
    name. The result never contains a path separator, so it cannot escape the
    upload directory however it is joined.
    """
    base = os.path.basename(name.replace("\\\\", "/"))
    base = _UNSAFE_FILENAME_RE.sub("_", base).lstrip("._-")
    if not base:
        return "upload"
    return base[:120]


def safe_join(directory: Path, filename: str) -> Path:
    """Join a sanitised filename onto ``directory``, asserting containment."""
    target = (directory / sanitise_filename(filename)).resolve()
    root = directory.resolve()
    if root != target and root not in target.parents:
        raise ValidationError(f"refusing to write outside {root}")
    return target


def validate_int(raw: str, *, dest: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{dest} must be an integer, got {raw!r}") from exc


def validate_float(raw: str, *, dest: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{dest} must be a number, got {raw!r}") from exc


def validate_choice(raw: str, choices: list[str], *, dest: str) -> str:
    if raw not in choices:
        allowed = ", ".join(repr(c) for c in choices)
        raise ValidationError(f"{dest} must be one of {allowed}, got {raw!r}")
    return raw


def apply_memory_limit(max_bytes: int) -> str:
    """Best-effort address-space limit for the child process.

    Only Linux and macOS expose ``resource.setrlimit`` for this; on other
    platforms (notably Windows) the limit is skipped and the reason is
    returned so the caller can record it in the run log. This is deliberately
    a soft failure: refusing to run a tool on Windows would be worse than
    running it without the limit.
    """
    if platform.system() not in {"Linux", "Darwin"}:
        return f"memory limit not supported on {platform.system()}; skipped"
    try:
        import resource  # noqa: PLC0415 - POSIX only
    except ImportError:  # pragma: no cover - POSIX without resource
        return "memory limit unavailable (no resource module); skipped"
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    except (OSError, ValueError):  # pragma: no cover - unusual platform
        return "memory limit could not be read; skipped"
    new_soft = max_bytes if soft == resource.RLIM_INFINITY else min(soft, max_bytes)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
    except (OSError, ValueError) as exc:  # pragma: no cover
        return f"memory limit could not be applied ({exc}); skipped"
    return f"memory limit set to {new_soft} bytes"


def which_first(commands: list[str]) -> str | None:
    """First command from ``commands`` found on PATH (diagnostics helper)."""
    for command in commands:
        if shutil.which(command):
            return command
    return None
'''


def _executor_source() -> str:
    return '''"""Run the whitelisted tool with streaming output, timeout and cancel.

`subprocess` is always called with an argv **list** and ``shell=False``. No
string is ever concatenated into a command line, so a value like
``; rm -rf /`` is passed as one inert argument.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from helpui_app.config import Config
from helpui_app.security import apply_memory_limit


@dataclass
class RunOutcome:
    """The result of one subprocess execution."""

    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    status: str
    """succeeded | failed | timeout | cancelled"""

    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def argv_display(self) -> str:
        """A copy-pasteable rendering of the argv, quoted for humans."""
        return " ".join(quote_argv_entry(a) for a in self.argv)


def quote_argv_entry(value: str) -> str:
    """Quote one argv entry for *display* (copy-pasteable into a shell).

    This is presentation only: the executed argv is always a list. A single
    quote inside the value is escaped as ``'\\''`` (close quote, escaped quote,
    reopen quote), which is the POSIX-correct way to embed one.
    """
    if value == "" or any(c in value for c in " \\t\\"'$;&|<>()*?[]#"):
        escaped = value.replace("'", "'\\\\''")
        return "'" + escaped + "'"
    return value


class ProcessController:
    """Tracks the currently running child so it can be cancelled.

    One controller per run. The web layer keeps a registry of them keyed by run
    id so the Cancel button can kill exactly one process.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._process = process
            if self._cancelled.is_set():
                # Cancelled between launch and attach: kill immediately.
                _terminate(process)

    def cancel(self) -> bool:
        """Request cancellation. Returns True if a live process was killed."""
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return False
        _terminate(process)
        return True


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Kill the child, escalating from terminate() to kill().

    On Windows ``terminate()`` maps to TerminateProcess, which is already
    forceful; on POSIX it sends SIGTERM first, so a tool that ignores SIGTERM
    gets SIGKILL after a short grace period.
    """
    try:
        process.terminate()
    except OSError:  # pragma: no cover - already gone
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:  # pragma: no cover
            pass


def build_argv(
    tool: list[str],
    command_path: list[str],
    values: list[tuple[str | None, tuple[str, ...]]],
) -> list[str]:
    """Assemble the final argv list.

    ``command_path`` is the subcommand name(s) to invoke, e.g. ``["convert"]``
    or ``["admin", "reset"]``; it is empty for a tool with no subcommands.
    Without it, ``click`` and ``argparse`` subparsers reject every option with
    "no such option", because the options belong to the subcommand.

    ``values`` is a list of ``(flag, values)`` pairs; ``flag=None`` marks a
    positional. Every entry is appended separately -- there is no string
    joining anywhere in this function, which is what makes the no-shell
    guarantee hold.
    """
    argv = [*tool, *command_path]
    for flag, items in values:
        if flag is None:
            argv.extend(items)
            continue
        for item in items:
            argv.append(flag)
            argv.append(item)
    return argv


def execute(
    config: Config,
    tool: list[str],
    command_path: list[str],
    values: list[tuple[str | None, tuple[str, ...]]],
    *,
    controller: ProcessController | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> RunOutcome:
    """Run the whitelisted tool, capturing stdout/stderr with limits.

    ``tool`` is the resolved argv prefix (see ``security.tool_argv``),
    ``command_path`` the subcommand tokens, and ``values`` the validated
    ``(flag, values)`` list. All three are already lists of literals, so this
    function never builds a shell string.

    Blocks until the process finishes, times out, or is cancelled.
    """
    argv = build_argv(tool, command_path, values)
    notes: list[str] = []

    # Memory limiting has to run *in the child* (RLIMIT_AS is inherited across
    # fork/exec), so it goes through preexec_fn. That argument is POSIX-only:
    # passing it on Windows raises, so the platform is checked first and the
    # skip is recorded in the run notes instead of failing the run.
    preexec = None
    if os.name == "posix":
        def _limit() -> None:  # pragma: no cover - POSIX only
            apply_memory_limit(config.max_memory_bytes)

        preexec = _limit
    else:
        notes.append(apply_memory_limit(0))

    started = time.monotonic()
    try:
        process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603 - argv list, shell=False
            argv,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd) if cwd else str(config.project_dir),
            env=env or _child_env(),
            **({"preexec_fn": preexec} if preexec is not None else {}),
        )
    except OSError as exc:
        duration = int((time.monotonic() - started) * 1000)
        return RunOutcome(
            argv=argv,
            exit_code=None,
            stdout="",
            stderr=f"could not start the tool: {exc}",
            duration_ms=duration,
            status="failed",
            notes=notes,
        )

    if controller is not None:
        controller.attach(process)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total = 0
    truncated = False
    lock = threading.Lock()

    def _reader(stream, sink: list[bytes]) -> None:
        nonlocal total, truncated
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with lock:
                    if total >= config.max_output_bytes:
                        truncated = True
                        continue
                    total += len(chunk)
                    sink.append(chunk)
        except (OSError, ValueError):  # pragma: no cover - pipe closed on kill
            pass
        finally:
            try:
                stream.close()
            except OSError:  # pragma: no cover
                pass

    readers = [
        threading.Thread(target=_reader, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for thread in readers:
        thread.start()

    # Status precedence matters: a process killed by the timeout also reports a
    # non-zero exit code, so "failed" must never overwrite "timeout"/"cancelled".
    exit_code: int | None = None
    timed_out = False
    try:
        exit_code = process.wait(timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if controller is not None:
            controller.cancel()
        _terminate(process)
        exit_code = process.returncode

    if timed_out:
        status = "timeout"
    elif controller is not None and controller.cancelled:
        status = "cancelled"
    elif exit_code not in (0, None):
        status = "failed"
    else:
        status = "succeeded"

    for thread in readers:
        thread.join(timeout=2)

    duration = int((time.monotonic() - started) * 1000)
    if timed_out:
        notes.append(f"killed after the {config.timeout_seconds:g}s timeout")
    if controller is not None and status == "cancelled":
        notes.append("cancelled by the user")
    if truncated:
        notes.append(f"output truncated at {config.max_output_bytes} bytes")

    out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    err = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if truncated:
        suffix = "\\n\\n[... output truncated ...]"
        out += suffix
        err += suffix if err else ""

    return RunOutcome(
        argv=argv,
        exit_code=exit_code,
        stdout=out,
        stderr=err,
        duration_ms=duration,
        status=status,
        truncated=truncated,
        notes=notes,
    )


def _child_env() -> dict[str, str]:
    """Environment for the child: stable, non-interactive, UTF-8."""
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env
'''


def _history_source() -> str:
    return '''"""SQLite run history.

Uses the standard library `sqlite3` (see SPEC.md: fewer dependencies beats an
ORM at this size). The schema is created on first use, so a freshly generated
project has no database file until it is actually run.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    command       TEXT    NOT NULL,
    command_slug  TEXT    NOT NULL,
    argv_json     TEXT    NOT NULL,
    argv_display  TEXT    NOT NULL,
    params_json   TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    exit_code     INTEGER,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    duration_ms   INTEGER,
    stdout        TEXT    NOT NULL DEFAULT '',
    stderr        TEXT    NOT NULL DEFAULT '',
    notes_json    TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS runs_started_at ON runs (started_at DESC);
"""


@dataclass
class RunRecord:
    """One row of the history table."""

    id: int
    command: str
    command_slug: str
    argv: list[str]
    argv_display: str
    params: dict[str, Any]
    status: str
    exit_code: int | None
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    stdout: str
    stderr: str
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> str:
        if self.duration_ms is None:
            return "—"
        return f"{self.duration_ms / 1000:.2f}s"

    @property
    def stderr_lines(self) -> int:
        return len([ln for ln in self.stderr.splitlines() if ln.strip()])

    def as_row_view(self) -> dict[str, Any]:
        """The shape templates use (keeps attribute access uniform)."""
        return {
            "id": self.id,
            "command": self.command,
            "started_at": self.started_at.replace("T", " ")[:19],
            "duration": self.duration,
            "exit_code": self.exit_code,
            "status": self.status,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class History:
    """Thread-safe history store.

    FastAPI runs sync endpoints in a thread pool, so one connection per thread
    is the simplest correct approach: SQLite connections are not shareable
    across threads, and a fresh connection per call is cheap at this scale.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(SCHEMA)

    def create(
        self,
        *,
        command: str,
        command_slug: str,
        argv: list[str],
        argv_display: str,
        params: dict[str, Any],
    ) -> int:
        """Insert a `running` row and return its id."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (command, command_slug, argv_json, argv_display,
                                  params_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    command,
                    command_slug,
                    json.dumps(argv),
                    argv_display,
                    json.dumps(params, default=str),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def finish(
        self,
        run_id: int,
        *,
        status: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        duration_ms: int,
        notes: list[str] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                   SET status = ?, exit_code = ?, stdout = ?, stderr = ?,
                       duration_ms = ?, finished_at = ?, notes_json = ?
                 WHERE id = ?
                """,
                (
                    status,
                    exit_code,
                    stdout,
                    stderr,
                    duration_ms,
                    utc_now(),
                    json.dumps(notes or []),
                    run_id,
                ),
            )

    def get(self, run_id: int) -> RunRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def list(self, limit: int = 200, offset: int = 0) -> list[RunRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"]) if row is not None else 0

    def clear(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM runs")


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=int(row["id"]),
        command=str(row["command"]),
        command_slug=str(row["command_slug"]),
        argv=list(json.loads(row["argv_json"])),
        argv_display=str(row["argv_display"]),
        params=dict(json.loads(row["params_json"])),
        status=str(row["status"]),
        exit_code=row["exit_code"],
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        stdout=str(row["stdout"]),
        stderr=str(row["stderr"]),
        notes=list(json.loads(row["notes_json"])),
    )
'''


def _rendering_source() -> str:
    return '''"""Render tool output as a table or preformatted text.

The renderer **never** emits raw tool output as HTML: everything is escaped.
A tool that prints `<script>` must not be able to inject script into the UI.
HTML escaping is done here, at the single point where output becomes markup.
"""

from __future__ import annotations

import csv
import io
import json
from html import escape
from typing import Any

#: Cap how many rows a table renders, so a huge output cannot hang the browser.
MAX_TABLE_ROWS = 500

#: Cap how many columns are shown.
MAX_TABLE_COLS = 20


def render(stdout: str, stderr: str, *, exit_code: int | None) -> str:
    """Produce the HTML fragment shown in the Result tab."""
    body = stdout.strip()
    if not body:
        if stderr.strip():
            return (
                '<p class="muted">The tool produced no stdout. '
                "See the stderr tab for its error output.</p>"
            )
        return '<p class="muted">The tool produced no output.</p>'

    parsed = _try_json(body)
    if parsed is not None:
        return _render_json(parsed)

    rows = _try_csv(body)
    if rows is not None:
        return _render_table(rows, caption="Detected CSV output")

    return _render_pre(body, caption="Plain text output")


def _try_json(text: str) -> Any | None:
    if not text.startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _try_csv(text: str) -> list[list[str]] | None:
    """Parse CSV only when the shape is convincing.

    Requires at least two lines, a comma or tab in the header, and every row
    having the header's column count. This avoids turning ordinary prose with a
    comma into a one-column table.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    sample = lines[0]
    if "," not in sample and "\\t" not in sample:
        return None
    delimiter = "," if sample.count(",") >= sample.count("\\t") else "\\t"
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error:
        return None
    if len(rows) < 2:
        return None
    width = len(rows[0])
    if width < 2:
        return None
    if not all(len(row) == width for row in rows[1:]):
        return None
    return rows


def _render_json(value: Any) -> str:
    # A list of objects is the common "report" shape: render it as a table.
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        columns: list[str] = []
        for item in value:
            for key in item:
                if key not in columns:
                    columns.append(key)
        rows = [[_cell(item.get(col)) for col in columns] for item in value]
        return _render_table([columns, *rows], caption=f"Detected JSON array ({len(value)} items)")

    # A single object with scalar values renders as a key/value table.
    if isinstance(value, dict) and value and all(not isinstance(v, (dict, list)) for v in value.values()):
        rows = [[key, _cell(val)] for key, val in value.items()]
        return _render_table(rows, caption="Detected JSON object", header=False)

    return _render_pre(json.dumps(value, indent=2, ensure_ascii=False), caption="Detected JSON")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _render_table(rows: list[list[str]], *, caption: str, header: bool = True) -> str:
    shown = rows[: MAX_TABLE_ROWS + 1] if header else rows[:MAX_TABLE_ROWS]
    truncated = len(rows) > len(shown)
    parts = [
        f'<p class="hint">{escape(caption)}</p>',
        '<div class="data-table-wrap"><table class="data-table">',
    ]
    if header and shown:
        head = shown[0][:MAX_TABLE_COLS]
        parts.append("<thead><tr>" + "".join(f"<th>{escape(str(c))}</th>" for c in head) + "</tr></thead>")
        body_rows = shown[1:]
    else:
        body_rows = shown
    parts.append("<tbody>")
    for row in body_rows:
        cells = "".join(f"<td>{escape(str(c))}</td>" for c in row[:MAX_TABLE_COLS])
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</tbody></table></div>")
    if truncated:
        parts.append(f'<p class="hint">Showing the first {len(shown)} rows.</p>')
    return "".join(parts)


def _render_pre(text: str, *, caption: str) -> str:
    return f'<p class="hint">{escape(caption)}</p><pre class="log">{escape(text)}</pre>'
'''


def _fastapi_app_source() -> str:
    return '''"""The FastAPI application: routes, forms, execution, history.

Design notes (see SPEC.md):

* Execution is **synchronous but off the event loop**: the route is a normal
  ``def``, so FastAPI runs it in a thread pool. The history row is written as
  ``running`` *before* the process starts, which is what makes the polling
  progress view and the Cancel button possible.
* ``shell=False`` everywhere, argv as a list, built by
  :func:`helpui_app.executor.build_argv`.
* Tool output is escaped by :mod:`helpui_app.rendering`; it is never treated
  as markup.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from helpui_app import rendering
from helpui_app.config import Config, load_config
from helpui_app.executor import ProcessController, build_argv, execute, quote_argv_entry
from helpui_app.history import History
from helpui_app.security import ValidationError, safe_join, sanitise_filename, tool_argv, validate_choice, validate_float, validate_int
from helpui_app.spec import load_spec


class RunRegistry:
    """Maps run id -> ProcessController so Cancel can target one process."""

    def __init__(self) -> None:
        self._controllers: dict[int, ProcessController] = {}
        self._lock = threading.Lock()

    def register(self, run_id: int) -> ProcessController:
        controller = ProcessController()
        with self._lock:
            self._controllers[run_id] = controller
        return controller

    def cancel(self, run_id: int) -> bool:
        """Request cancellation. The kill happens *outside* the lock.

        ``ProcessController.cancel()`` waits for the child to die (up to the
        escalate-to-kill grace period), so calling it under ``self._lock`` would
        stall every other request that touches the registry for that whole time.
        The lock is held only long enough to read the reference.
        """
        with self._lock:
            controller = self._controllers.get(run_id)
        return controller.cancel() if controller is not None else False

    def release(self, run_id: int) -> None:
        with self._lock:
            self._controllers.pop(run_id, None)


def create_app(config: Config | None = None) -> FastAPI:
    """Build the application. Accepts an override config for tests."""
    cfg = config or load_config()
    spec = load_spec(cfg.spec_path)
    history = History(cfg.db_path)
    registry = RunRegistry()

    app = FastAPI(title=f"HelpUI: {spec.tool_name}", docs_url=None, redoc_url=None)
    app.state.config = cfg
    app.state.spec = spec
    app.state.history = history
    app.state.registry = registry

    templates = Jinja2Templates(directory=str(cfg.templates_dir))
    app.mount("/static", StaticFiles(directory=str(cfg.static_dir)), name="static")

    # -- pages -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        commands = [_command_view(spec, c) for c in spec.commands]
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"spec": spec, "commands": commands},
        )

    @app.get("/command/{slug}", response_class=HTMLResponse)
    def form_page(request: Request, slug: str) -> HTMLResponse:
        command = _find_command(spec, slug)
        if command is None:
            raise HTTPException(status_code=404, detail=f"unknown command: {slug}")
        return templates.TemplateResponse(
            request=request,
            name="form.html",
            context={"spec": spec, "command": _command_view(spec, command)},
        )

    @app.get("/history", response_class=HTMLResponse)
    def history_page(request: Request) -> HTMLResponse:
        records = history.list(limit=cfg.history_limit)
        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "spec": spec,
                "runs": [r.as_row_view() for r in records],
                "total": history.count(),
            },
        )

    @app.get("/history/{run_id}", response_class=HTMLResponse)
    def history_detail(request: Request, run_id: int) -> HTMLResponse:
        record = history.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return templates.TemplateResponse(
            request=request,
            name="record.html",
            context={
                "spec": spec,
                "record": record,
                "rendered": rendering.render(record.stdout, record.stderr, exit_code=record.exit_code),
            },
        )

    @app.get("/history/{run_id}/download")
    def history_download(run_id: int) -> PlainTextResponse:
        record = history.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        body = _download_body(record)
        filename = f"run-{record.id}-{record.command_slug}.txt"
        return PlainTextResponse(
            body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- api ---------------------------------------------------------------

    @app.get("/api/spec")
    def api_spec() -> JSONResponse:
        return JSONResponse(spec.to_dict())

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: int) -> JSONResponse:
        record = history.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return JSONResponse(
            {
                "id": record.id,
                "command": record.command,
                "status": record.status,
                "exit_code": record.exit_code,
                "duration_ms": record.duration_ms,
                "stdout": record.stdout,
                "stderr": record.stderr,
                "argv": record.argv,
            }
        )

    # -- execution ---------------------------------------------------------

    @app.post("/run/{slug}", response_class=HTMLResponse)
    async def run_command(request: Request, slug: str) -> HTMLResponse:
        command = _find_command(spec, slug)
        if command is None:
            raise HTTPException(status_code=404, detail=f"unknown command: {slug}")

        form = await request.form()
        try:
            values, params, uploads = await _collect_values(cfg, command, form)
        except ValidationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="_error.html",
                context={"message": str(exc)},
                status_code=400,
            )

        tool = tool_argv(spec.tool_path)
        argv = build_argv(tool, command.argv_path(), values)

        # Create the history row *before* starting the process: the row id is
        # what the registry is keyed by, so Cancel can find the live process,
        # and a crash mid-run still leaves a visible `running` record.
        run_id = history.create(
            command=command.name or "(root)",
            command_slug=_slug(command.name),
            argv=argv,
            argv_display=" ".join(quote_argv_entry(a) for a in argv),
            params=params,
        )
        # `execute()` blocks until the process exits, times out or is cancelled.
        # This route is `async def` because parsing the form needs `await`, but a
        # blocking call inside an async route runs *on the event loop* and freezes
        # the whole server -- `/api/runs/*`, the history page and Cancel all stop
        # answering while a long tool runs. `run_in_threadpool` moves the wait to
        # a worker thread so the loop stays free.
        controller = registry.register(run_id)

        try:
            outcome = await run_in_threadpool(
                execute, cfg, tool, command.argv_path(), values, controller=controller
            )
        finally:
            registry.release(run_id)

        history.finish(
            run_id,
            status=outcome.status,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            duration_ms=outcome.duration_ms,
            notes=[*outcome.notes, *uploads],
        )

        record = history.get(run_id)
        assert record is not None
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "spec": spec,
                "record": record,
                "rendered": rendering.render(record.stdout, record.stderr, exit_code=record.exit_code),
            },
        )

    @app.post("/cancel/{slug}", response_class=HTMLResponse)
    def cancel_command(request: Request, slug: str) -> HTMLResponse:
        """Cancel the most recent running job for this command."""
        for record in history.list(limit=cfg.history_limit):
            if record.command_slug == _slug(slug) and record.status == "running":
                cancelled = registry.cancel(record.id)
                return templates.TemplateResponse(
                    request=request,
                    name="_error.html",
                    context={
                        "message": (
                            "Cancellation requested; the process was killed."
                            if cancelled
                            else "Cancellation requested, but the process had already finished."
                        )
                    },
                )
        return templates.TemplateResponse(
            request=request,
            name="_error.html",
            context={"message": "Nothing is currently running for this command."},
        )

    @app.get("/result/{run_id}", response_class=HTMLResponse)
    def result_fragment(request: Request, run_id: int) -> HTMLResponse:
        """Polled by HTMX while a run is in progress."""
        record = history.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "spec": spec,
                "record": record,
                "rendered": rendering.render(record.stdout, record.stderr, exit_code=record.exit_code),
            },
        )

    return app


# -- helpers ---------------------------------------------------------------


def _slug(name: str) -> str:
    if not name:
        return "_root"
    return name.strip().replace(" ", "_").lower()


def _command_view(spec: Any, command: Any) -> dict[str, Any]:
    """Template-friendly view of a CLICommand (adds slug/label/counts)."""
    return {
        "slug": _slug(command.name),
        "name": command.name,
        "label": command.name or "(root)",
        "help": command.help,
        "options": command.options,
        "positionals": command.positionals,
        "option_count": len(command.options) + len(command.positionals),
    }


def _find_command(spec: Any, slug: str):
    for command in spec.commands:
        if _slug(command.name) == slug or command.name == slug:
            return command
    return None


async def _collect_values(
    cfg: Config, command: Any, form: Any
) -> tuple[list[tuple[str | None, tuple[str, ...]]], dict[str, Any], list[str]]:
    """Validate submitted form data into argv-ready values.

    Returns ``(values, params_for_history, notes)``.

    Every value is validated here, *before* any process starts: ints and floats
    by conversion, choices against the parsed choice list, files by size and
    count, and filenames by basename sanitisation.
    """
    values: list[tuple[str | None, tuple[str, ...]]] = []
    params: dict[str, Any] = {}
    notes: list[str] = []
    upload_count = 0

    for option in command.options:
        if option.is_flag:
            if form.get(option.dest):
                values.append((option.primary_flag, ()))
                params[option.dest] = True
            continue

        if option.type == "file":
            uploads = [item for item in form.getlist(option.dest) if getattr(item, "filename", "")]
            if not uploads:
                if option.required:
                    raise ValidationError(f"{option.primary_flag} is required")
                continue
            for upload in uploads:
                upload_count += 1
                if upload_count > cfg.max_upload_files:
                    raise ValidationError(f"at most {cfg.max_upload_files} files may be uploaded")
                path = await _store_upload(cfg, upload, notes)
                values.append((option.primary_flag, (path,)))
                params[option.dest] = path
            continue

        raw_items = [v for v in form.getlist(option.dest) if isinstance(v, str) and v != ""]
        if not raw_items:
            if option.required:
                raise ValidationError(f"{option.primary_flag} is required")
            if option.default:
                raw_items = [option.default]
            else:
                continue

        if option.multiple and option.type != "choice":
            # The textarea form submits one blob; split on newlines.
            expanded: list[str] = []
            for item in raw_items:
                expanded.extend(part.strip() for part in item.splitlines() if part.strip())
            raw_items = expanded or raw_items

        converted: list[str] = []
        for raw in raw_items:
            if option.type == "int":
                converted.append(str(validate_int(raw, dest=option.dest)))
            elif option.type == "float":
                converted.append(str(validate_float(raw, dest=option.dest)))
            elif option.type == "choice":
                converted.append(validate_choice(raw, option.choices or [], dest=option.dest))
            else:
                converted.append(raw if not option.multiple else raw.strip())
        values.append((option.primary_flag, tuple(converted)))
        params[option.dest] = converted[0] if len(converted) == 1 else converted

    for positional in command.positionals:
        # A positional declared `type="file"` arrives as an UploadFile, exactly
        # like a `file` option: the form renders an `<input type="file">` whose
        # value is never a string, so filtering for `str` here would drop it and
        # report the field as missing even though the user filled it in. Both
        # branches therefore share `_store_upload`.
        if positional.type == "file":
            uploads = [item for item in form.getlist(positional.dest) if getattr(item, "filename", "")]
            if not uploads:
                if positional.required:
                    raise ValidationError(f"{positional.dest} is required")
                continue
            stored: list[str] = []
            for upload in uploads:
                upload_count += 1
                if upload_count > cfg.max_upload_files:
                    raise ValidationError(f"at most {cfg.max_upload_files} files may be uploaded")
                stored.append(await _store_upload(cfg, upload, notes))
            values.append((None, tuple(stored)))
            params[positional.dest] = stored[0] if len(stored) == 1 else stored
            continue

        raw_items = [v for v in form.getlist(positional.dest) if isinstance(v, str) and v != ""]
        if not raw_items:
            if positional.required:
                raise ValidationError(f"{positional.dest} is required")
            continue
        values.append((None, tuple(raw_items)))
        params[positional.dest] = raw_items[0] if len(raw_items) == 1 else raw_items

    return values, params, notes


async def _store_upload(cfg: Config, upload: UploadFile, notes: list[str]) -> str:
    """Persist one uploaded file and return the path to pass to the tool.

    Shared by the ``file`` *option* and ``file`` *positional* branches. Keeping
    one implementation is what stops the two paths from drifting: they had
    already diverged once, which silently dropped positional uploads.

    Size is checked before anything is written, and the filename is funnelled
    through ``safe_join``, so a traversal attempt cannot escape ``upload_dir``.
    """
    data = await upload.read()
    if len(data) > cfg.max_upload_bytes:
        raise ValidationError(
            f"{upload.filename} exceeds the {cfg.max_upload_bytes} byte upload limit"
        )
    target = safe_join(cfg.upload_dir, upload.filename or "upload")
    target.write_bytes(data)
    notes.append(f"saved upload {sanitise_filename(upload.filename or 'upload')} to {target}")
    return str(target)


def _download_body(record: Any) -> str:
    """A self-describing text file: the command, the outputs, the metadata."""
    lines = [
        f"# run {record.id}",
        f"# command: {record.command}",
        f"# argv: {record.argv_display}",
        f"# status: {record.status}",
        f"# exit_code: {record.exit_code}",
        f"# started_at: {record.started_at}",
        f"# duration_ms: {record.duration_ms}",
        "",
        "# --- params ---",
        json.dumps(record.params, indent=2, ensure_ascii=False),
        "",
        "# --- stdout ---",
        record.stdout,
        "",
        "# --- stderr ---",
        record.stderr,
        "",
    ]
    return "\\n".join(lines)
'''


def _spec_source() -> str:
    return '''"""Load and expose the scanned CLI specification.

The spec is generated code's *source of truth*: which commands exist, what
options each takes, and -- critically -- the single `tool_path` that is
allowed to be executed (the whitelist).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Option types this runtime knows how to render and validate.
OPTION_TYPES = frozenset(
    {"str", "int", "float", "bool", "choice", "file", "dir", "password"}
)


@dataclass
class Option:
    """One command-line option or positional argument."""

    name: str
    dest: str
    type: str = "str"
    required: bool = False
    default: str | None = None
    choices: list[str] | None = None
    help: str = ""
    is_flag: bool = False
    multiple: bool = False
    value_name: str = ""
    aliases: list[str] = field(default_factory=list)

    @property
    def primary_flag(self) -> str:
        """The spelling used when building argv; prefers the long form."""
        for alias in self.aliases:
            if alias.startswith("--"):
                return alias
        return self.name

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Option:
        option_type = str(data.get("type", "str"))
        return cls(
            name=str(data.get("name", "")),
            dest=str(data.get("dest", "")),
            type=option_type if option_type in OPTION_TYPES else "str",
            required=bool(data.get("required", False)),
            default=data.get("default"),
            choices=list(data["choices"]) if data.get("choices") else None,
            help=str(data.get("help", "")),
            is_flag=bool(data.get("is_flag", False)),
            multiple=bool(data.get("multiple", False)),
            value_name=str(data.get("value_name", "")),
            aliases=[str(a) for a in data.get("aliases", [])],
        )


@dataclass
class Command:
    """A subcommand (empty name for the root invocation)."""

    name: str
    help: str = ""
    options: list[Option] = field(default_factory=list)
    positionals: list[Option] = field(default_factory=list)
    subcommands: list[str] = field(default_factory=list)

    def argv_path(self) -> list[str]:
        """The subcommand tokens to place before the options.

        ``"convert"`` -> ``["convert"]``; ``"admin reset"`` -> ``["admin",
        "reset"]``; the root command -> ``[]``. Nested names are stored
        space-separated by HelpUI, so they split back cleanly here.
        """
        if not self.name:
            return []
        return self.name.split()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Command:
        return cls(
            name=str(data.get("name", "")),
            help=str(data.get("help", "")),
            options=[Option.from_dict(o) for o in data.get("options", [])],
            positionals=[Option.from_dict(p) for p in data.get("positionals", [])],
            subcommands=[str(s) for s in data.get("subcommands", [])],
        )


@dataclass
class Spec:
    """The whole scanned specification."""

    tool_name: str
    tool_path: str
    description: str = ""
    commands: list[Command] = field(default_factory=list)
    parser_kind: str = "unknown"

    def command(self, name: str) -> Command | None:
        for candidate in self.commands:
            if candidate.name == name:
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_path": self.tool_path,
            "description": self.description,
            "parser_kind": self.parser_kind,
            "commands": [
                {
                    "name": c.name,
                    "help": c.help,
                    "options": [o.__dict__ for o in c.options],
                    "positionals": [p.__dict__ for p in c.positionals],
                    "subcommands": list(c.subcommands),
                }
                for c in self.commands
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Spec:
        return cls(
            tool_name=str(data.get("tool_name", "")),
            tool_path=str(data.get("tool_path", "")),
            description=str(data.get("description", "")),
            commands=[Command.from_dict(c) for c in data.get("commands", [])],
            parser_kind=str(data.get("parser_kind", "unknown")),
        )


def load_spec(path: Path) -> Spec:
    """Read ``spec.json``.

    Raises ``FileNotFoundError`` with a helpful message if the project was
    copied without its spec, rather than failing later with a confusing error.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing. This file is written by `helpui generate`; "
            "regenerate the project or restore spec.json."
        )
    return Spec.from_dict(json.loads(path.read_text(encoding="utf-8")))
'''


def _server_source() -> str:
    return '''"""uvicorn launcher for the generated application."""

from __future__ import annotations

import webbrowser
from threading import Timer

import uvicorn

from helpui_app.app import create_app
from helpui_app.config import Config


def run(
    config: Config,
    *,
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
    open_browser: bool = False,
) -> None:
    """Start the web server (blocking)."""
    bind_host = host or config.host
    bind_port = port or config.port

    if open_browser and not reload:
        url = f"http://{bind_host if bind_host != '0.0.0.0' else '127.0.0.1'}:{bind_port}/"
        Timer(1.0, lambda: webbrowser.open(url)).start()

    if reload:
        # uvicorn's reloader needs an import string, not an app object.
        uvicorn.run(
            "helpui_app.app:create_app",
            factory=True,
            host=bind_host,
            port=bind_port,
            reload=True,
        )
        return

    uvicorn.run(create_app(config), host=bind_host, port=bind_port)
'''


def _project_readme(spec: CLISpec, *, default_port: int, default_host: str) -> str:
    command_lines = "\n".join(
        f"| `{c.name or '(root)'}` | {c.help or '—'} | {len(c.options)} |" for c in spec.commands
    )
    return f"""# WebUI for `{spec.tool_name}`

Generated by [HelpUI](https://github.com/) from this tool's `--help` output.

* **Tool path:** `{spec.tool_path}`
* **Detected framework:** `{spec.parser_kind}`
* **Description:** {spec.description or '—'}

## Run it

```console
python app.py                  # default: http://{default_host}:{default_port}
python app.py --port 9000      # different port
python app.py --host 0.0.0.0   # bind all interfaces
```

Dependencies: `fastapi`, `jinja2`, `uvicorn`, `python-multipart`. Install them
with:

```console
pip install fastapi jinja2 uvicorn python-multipart
```

## Commands exposed

| Command | Description | Options |
|---|---|---|
{command_lines}

## Configuration

Set environment variables instead of editing generated files:

| Variable | Default | Meaning |
|---|---|---|
| `HELPUI_HOST` | `{default_host}` | Bind address |
| `HELPUI_PORT` | `{default_port}` | Bind port |
| `HELPUI_TIMEOUT` | `{DEFAULT_TIMEOUT_SECONDS:g}` | Seconds before the tool is killed |
| `HELPUI_MAX_OUTPUT_BYTES` | `{DEFAULT_MAX_OUTPUT_BYTES}` | Captured output cap |
| `HELPUI_MAX_UPLOAD_BYTES` | `{DEFAULT_MAX_UPLOAD_BYTES}` | Per-file upload cap |
| `HELPUI_MAX_UPLOAD_FILES` | `{DEFAULT_MAX_UPLOAD_FILES}` | Uploads per run |
| `HELPUI_HISTORY_LIMIT` | `{DEFAULT_HISTORY_LIMIT}` | Rows shown on the history page |

## Layout

```
app.py              entry point (`python app.py`)
spec.json           the scanned CLI specification
helpui_app/         application package
  config.py         configuration and limits
  paths.py          project-relative paths
  security.py       whitelist, validation, resource limits
  executor.py       subprocess execution (shell=False, argv list)
  history.py        SQLite history
  rendering.py      JSON / CSV / text rendering (always escaped)
  app.py            FastAPI routes
  server.py         uvicorn launcher
  spec.py           spec.json loading
templates/          Jinja2 templates
static/             style.css, htmx.min.js (bundled; no CDN)
data/               history.db and uploads (created at runtime)
```

## Security posture

* Only the tool recorded in `spec.json` is ever executed — that is the
  whitelist.
* Arguments are passed to `subprocess` as a **list** with `shell=False`; no
  shell string is ever built.
* `int`/`float` values are validated by conversion, `choice` values against the
  parsed choice list.
* Uploaded filenames are reduced to a safe basename; `..` and path separators
  are stripped before any file is written.
* A run is killed after `HELPUI_TIMEOUT` seconds, and the Cancel button kills
  the running process directly.
* Tool output is HTML-escaped before rendering; it is never treated as markup.
* Memory limiting via `resource.setrlimit` applies on Linux/macOS only; on
  Windows it is skipped and the reason is recorded in the run's notes.

## Regenerating

Re-running `helpui generate` overwrites this directory (use `--force`). Any
edits to files here are lost.
"""

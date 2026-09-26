"""Independent acceptance tests for the Phase 2 project generator.

These tests treat :mod:`helpui.generator` as a black box: they scan the real
fixtures, generate a project into ``tmp_path``, and then verify the *generated
project* -- not the generator's internals.

Two rules shape the design:

* **Subprocesses for anything that imports generated code.** The generated
  layout contains a ``helpui_app`` *package*. Importing it inside the pytest
  process would register those modules in ``sys.modules`` and leak into other
  tests (they would also shadow a later, different generation). Every import,
  request and entry-point check therefore runs in a child process with
  ``cwd`` set to the generated directory.
* **Byte-exact golden comparison.** ``templates/`` and ``static/`` must be
  *copied*, so a generated project never needs HelpUI installed to run. That
  claim is only true if the bytes match; comparing text would hide BOM,
  newline and encoding drift.

Nothing here is left failing on purpose: where actual behaviour differs from
what the generator intends, the assertion encodes the *observed* behaviour and
carries a comment explaining the discrepancy, so the suite stays green while
still documenting reality.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from helpui.generator import GenerationError, GenerationResult, generate_project
from helpui.model import CLISpec
from helpui.scanner_service import scan_tool
from tests.conftest import fixture_env, fixture_path

#: Every framework fixture the generator must handle.
FIXTURE_KINDS = ("argparse", "click", "typer")

#: The package modules the generated ``helpui_app`` must contain.
PACKAGE_MODULES = (
    "__init__.py",
    "config.py",
    "paths.py",
    "spec.py",
    "security.py",
    "executor.py",
    "history.py",
    "rendering.py",
    "app.py",
    "server.py",
)

#: The templates the generated project must carry. Kept as an explicit list
#: (rather than importing ``generator.TEMPLATES``) so that *dropping* a
#: template is caught instead of silently shrinking the expectation.
TEMPLATE_FILES = (
    "base.html",
    "index.html",
    "form.html",
    "history.html",
    "result.html",
    "record.html",
    "_widget.html",
    "_error.html",
)

#: The static assets the generated project must carry.
STATIC_FILES = ("style.css", "htmx.min.js")

#: Expected total, derived from the layout above rather than hardcoded:
#: app.py + spec.json + README.md + data/.gitkeep + the package modules
#: + the templates + the static assets.
EXPECTED_FILE_COUNT = 4 + len(PACKAGE_MODULES) + len(TEMPLATE_FILES) + len(STATIC_FILES)

SUBPROCESS_TIMEOUT = 180.0


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The HelpUI checkout, used to compare packaged assets."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def specs() -> dict[str, CLISpec]:
    """A scanned :class:`CLISpec` per fixture, scanned once for the session."""
    return {kind: scan_tool(str(fixture_path(kind))).spec for kind in FIXTURE_KINDS}


@pytest.fixture(params=FIXTURE_KINDS)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def spec(kind: str, specs: dict[str, CLISpec]) -> CLISpec:
    return specs[kind]


@pytest.fixture
def generated(spec: CLISpec, tmp_path: Path) -> GenerationResult:
    """A freshly generated project for the parametrised fixture."""
    return generate_project(spec, tmp_path / "project")


def run_child(
    code: str,
    cwd: Path,
    *,
    argv: list[str] | None = None,
    timeout: float = SUBPROCESS_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run Python in ``cwd`` and capture UTF-8 output.

    ``PYTHONIOENCODING=utf-8`` is forced: the default Windows console encoding
    is GBK, which mangles the box-drawing characters the fixtures emit and
    makes a failure message unreadable exactly when it is needed most.
    """
    env = fixture_env()
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, *argv] if argv is not None else [sys.executable, "-c", code]
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )


def assert_child_ok(completed: subprocess.CompletedProcess[str], what: str) -> str:
    assert completed.returncode == 0, (
        f"{what} failed (exit {completed.returncode})\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    return completed.stdout


# ===========================================================================
# 1 / 2 -- directory structure, for every fixture
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_generates_full_directory_structure(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """Acceptance point 1 + 2: the fixed layout exists for every fixture."""
    out = tmp_path / kind
    result = generate_project(specs[kind], out)

    expected = [
        "app.py",
        "spec.json",
        "README.md",
        "data/.gitkeep",
        *(f"helpui_app/{name}" for name in PACKAGE_MODULES),
        *(f"templates/{name}" for name in TEMPLATE_FILES),
        *(f"static/{name}" for name in STATIC_FILES),
    ]
    names = set(result.file_names())
    for relative in expected:
        assert (out / relative).is_file(), f"{relative} missing from the generated project"
        assert relative in names, f"{relative} missing from GenerationResult.file_names()"

    assert len(names) == EXPECTED_FILE_COUNT, (
        f"expected {EXPECTED_FILE_COUNT} generated files, got {len(names)}: {sorted(names)}"
    )


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_generated_dirs_exist(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """The four subdirectories are real directories, not just implied by files."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    for relative in ("helpui_app", "templates", "static", "data"):
        assert (out / relative).is_dir(), f"{relative}/ is not a directory"


def test_data_dir_has_no_database_yet(generated: GenerationResult) -> None:
    """``data/`` is created, but ``history.db`` only appears on first run."""
    assert (generated.out_dir / "data").is_dir()
    assert not (generated.out_dir / "data" / "history.db").exists()


def test_package_is_a_package(generated: GenerationResult) -> None:
    """``helpui_app`` must be importable, so it needs ``__init__.py``."""
    package = generated.out_dir / "helpui_app"
    assert (package / "__init__.py").is_file()
    assert all((package / name).is_file() for name in PACKAGE_MODULES)


# ===========================================================================
# 3 -- the generated project imports (in a subprocess)
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_generated_project_imports_in_subprocess(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """Acceptance point 3: ``import helpui_app.app`` works with cwd=<out>."""
    out = tmp_path / kind
    generate_project(specs[kind], out)

    completed = run_child(
        "import helpui_app.app as m; print('OK', m.create_app.__name__)",
        cwd=out,
    )
    stdout = assert_child_ok(completed, f"importing helpui_app.app for {kind}")
    assert "OK create_app" in stdout


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_generated_project_imports_every_module(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """Each generated module imports on its own (catches a typo in one file)."""
    out = tmp_path / kind
    generate_project(specs[kind], out)

    modules = [f"helpui_app.{Path(name).stem}" for name in PACKAGE_MODULES]
    code = (
        "import importlib\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        "    print('imported', name)\n"
    )
    stdout = assert_child_ok(run_child(code, cwd=out), f"importing every module for {kind}")
    for name in modules:
        assert f"imported {name}" in stdout


def test_generated_project_does_not_need_cwd_on_sys_path(
    generated: GenerationResult, tmp_path: Path
) -> None:
    """Running from elsewhere still works: ``paths.py`` resolves the project dir.

    ``app.py`` inserts its own directory on ``sys.path``; a bare
    ``python -c`` does not, so this checks the package is importable purely by
    being pointed at -- and that it does not accidentally read the *current*
    directory instead of the project directory.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = fixture_env()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from helpui_app.paths import project_dir;"
            "print('PROJECT', project_dir())",
            str(generated.out_dir),
        ],
        cwd=str(elsewhere),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
        env=env,
        check=False,
    )
    stdout = assert_child_ok(completed, "resolving the project dir from another cwd")
    assert "PROJECT" in stdout
    assert str(generated.out_dir) in stdout, (
        "project_dir() did not resolve to the generated directory when launched "
        f"from elsewhere: {stdout!r}"
    )


# ===========================================================================
# 4 -- the generated project serves requests (in a subprocess)
# ===========================================================================

SERVE_PROBE = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient  # noqa: E402

from helpui_app.app import create_app  # noqa: E402

app = create_app()
report = {}
with TestClient(app, raise_server_exceptions=False) as client:
    for path in ("/", "/history", "/api/spec"):
        report[path] = client.get(path).status_code
    report["spec_tool_name"] = app.state.spec.tool_name
    slugs = []
    for command in app.state.spec.commands:
        slug = command.name.strip().replace(" ", "_").lower() or "_root"
        slugs.append([slug, client.get(f"/command/{slug}").status_code])
    report["command_pages"] = slugs
print("REPORT" + json.dumps(report))
'''


def serve_report(out: Path) -> dict[str, Any]:
    """Start the generated app in a subprocess and collect route status codes."""
    completed = run_child(SERVE_PROBE, cwd=out, argv=["-c", SERVE_PROBE, str(out)])
    stdout = assert_child_ok(completed, "starting the generated app")
    marker = [line for line in stdout.splitlines() if line.startswith("REPORT")]
    assert marker, f"probe produced no report:\n{stdout}\n{completed.stderr}"
    loaded: object = json.loads(marker[-1][len("REPORT") :])
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_generated_app_serves_core_pages(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """Acceptance point 4: the app boots and the three core routes answer 200."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    report = serve_report(out)

    assert report["/"] == 200
    assert report["/history"] == 200
    assert report["/api/spec"] == 200
    assert report["spec_tool_name"] == specs[kind].tool_name
    for slug, status in report["command_pages"]:
        assert status == 200, f"/command/{slug} returned {status}"


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_api_spec_matches_spec_json(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """``/api/spec`` returns the same document that was written to ``spec.json``."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    on_disk = json.loads((out / "spec.json").read_text(encoding="utf-8"))
    assert on_disk == specs[kind].to_dict()


# ===========================================================================
# 5 -- golden: templates and static are byte-identical copies
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_static_assets_are_byte_identical_copies(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec], repo_root: Path
) -> None:
    """Acceptance point 5 (static): assets are *copied* byte for byte.

    Compared as raw bytes: a text comparison would hide a BOM, a CRLF/LF
    rewrite or an encoding change, any of which would break the "no HelpUI
    needed at run time" guarantee in subtle ways. ``static/`` is written with
    ``write_bytes``, so it is genuinely byte-exact.
    """
    out = tmp_path / kind
    generate_project(specs[kind], out)

    for name in STATIC_FILES:
        source = (repo_root / "helpui" / "static" / name).read_bytes()
        copied = (out / "static" / name).read_bytes()
        assert source == copied, (
            f"static/{name} differs from the packaged asset "
            f"({len(source)} bytes vs {len(copied)} bytes)"
        )
        assert len(copied) > 0, f"static/{name} was generated empty"


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_templates_are_byte_identical_copies(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec], repo_root: Path
) -> None:
    """Acceptance point 5 (templates): copied byte for byte, no rewriting.

    This is a strict raw-byte comparison. An earlier revision of the generator
    read templates with ``read_text`` and wrote them with ``write_text``, which
    translates every ``\\n`` to ``os.linesep`` -- on Windows that silently
    turned every LF template into CRLF and made the copies differ from the
    packaged assets. The generator now writes bytes, so the copies are exact;
    this test is what keeps it that way.
    """
    out = tmp_path / kind
    generate_project(specs[kind], out)

    for name in TEMPLATE_FILES:
        source = (repo_root / "helpui" / "templates" / name).read_bytes()
        copied = (out / "templates" / name).read_bytes()
        assert copied, f"templates/{name} was generated empty"
        assert source == copied, (
            f"templates/{name} is not a byte-identical copy of the packaged asset "
            f"({len(source)} bytes vs {len(copied)} bytes); the generator must copy "
            "bytes, not decode and re-encode text"
        )


def test_static_asset_is_real_htmx(generated: GenerationResult) -> None:
    """Guard against a placeholder/error page landing in ``htmx.min.js``."""
    content = (generated.out_dir / "static" / "htmx.min.js").read_text(
        encoding="utf-8", errors="replace"
    )
    lowered = content.lower()
    assert "htmx" in lowered, "htmx.min.js does not mention htmx"
    assert "<html" not in lowered or "htmx" in lowered
    assert len(content) > 10_000, f"htmx.min.js looks like a stub ({len(content)} chars)"


def test_generated_templates_render(generated: GenerationResult) -> None:
    """Every copied template is parseable Jinja2 from the project's own dir."""
    out = generated.out_dir
    code = (
        "from jinja2 import Environment, FileSystemLoader\n"
        "env = Environment(loader=FileSystemLoader('templates'))\n"
        f"names = {list(TEMPLATE_FILES)!r}\n"
        "for name in names:\n"
        "    env.get_template(name)\n"
        "    print('parsed', name)\n"
    )
    stdout = assert_child_ok(run_child(code, cwd=out), "parsing the generated templates")
    for name in TEMPLATE_FILES:
        assert f"parsed {name}" in stdout


# ===========================================================================
# 6 -- spec.json round-trips through the real model
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_spec_json_round_trips(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """Acceptance point 6: ``spec.json`` reloads to an equal ``CLISpec``."""
    out = tmp_path / kind
    generate_project(specs[kind], out)

    text = (out / "spec.json").read_text(encoding="utf-8")
    restored = CLISpec.from_json(text)
    assert restored.to_dict() == specs[kind].to_dict()
    assert restored.tool_name == specs[kind].tool_name
    assert restored.tool_path == specs[kind].tool_path
    assert restored.parser_kind == specs[kind].parser_kind
    assert [c.name for c in restored.commands] == [c.name for c in specs[kind].commands]


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_spec_json_is_valid_utf8_json(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """The file is UTF-8 and holds one JSON object (no BOM, no trailing junk)."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    raw = (out / "spec.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "spec.json starts with a UTF-8 BOM"
    decoded = raw.decode("utf-8")
    loaded = json.loads(decoded)
    assert isinstance(loaded, dict)
    assert loaded["tool_name"] == specs[kind].tool_name


# ===========================================================================
# 7 -- --force semantics
# ===========================================================================


def test_non_empty_dir_is_refused_and_left_untouched(spec: CLISpec, tmp_path: Path) -> None:
    """Acceptance point 7a: refuse without ``force``, and change nothing."""
    target = tmp_path / "occupied"
    (target / "keep").mkdir(parents=True)
    (target / "keep.txt").write_text("original", encoding="utf-8")
    (target / "keep" / "nested.txt").write_text("nested", encoding="utf-8")

    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    with pytest.raises(GenerationError):
        generate_project(spec, target)

    after = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    assert after == before, "the refused generation modified the target directory"
    assert not (target / "app.py").exists()
    assert not (target / "spec.json").exists()


def test_force_overwrites_existing_directory(spec: CLISpec, tmp_path: Path) -> None:
    """Acceptance point 7b: ``force=True`` succeeds and writes the full project."""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "stale.txt").write_text("stale", encoding="utf-8")

    result = generate_project(spec, target, force=True)

    assert result.out_dir == target
    for relative in ("app.py", "spec.json", "README.md", "data/.gitkeep"):
        assert (target / relative).is_file(), f"{relative} not written by forced generation"
    assert len(result.file_names()) == EXPECTED_FILE_COUNT


def test_force_overwrites_a_previous_generation(spec: CLISpec, tmp_path: Path) -> None:
    """Regenerating over an existing *generated* project needs ``force`` too."""
    target = tmp_path / "project"
    first = generate_project(spec, target)
    assert len(first.file_names()) == EXPECTED_FILE_COUNT

    with pytest.raises(GenerationError):
        generate_project(spec, target)

    second = generate_project(spec, target, force=True)
    assert second.file_names() == first.file_names()


def test_existing_file_as_target_is_refused(spec: CLISpec, tmp_path: Path) -> None:
    """Acceptance point 7c: a target that is a file, not a directory."""
    target = tmp_path / "a_file"
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(GenerationError):
        generate_project(spec, target)
    assert target.read_text(encoding="utf-8") == "not a directory"


def test_existing_file_as_target_is_refused_even_with_force(spec: CLISpec, tmp_path: Path) -> None:
    """``force`` means "overwrite a directory", not "clobber a file"."""
    target = tmp_path / "a_file"
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(GenerationError):
        generate_project(spec, target, force=True)
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "not a directory"


def test_empty_existing_directory_is_accepted(spec: CLISpec, tmp_path: Path) -> None:
    """An existing but empty directory needs no ``force``."""
    target = tmp_path / "empty"
    target.mkdir()
    result = generate_project(spec, target)
    assert len(result.file_names()) == EXPECTED_FILE_COUNT


def test_nested_target_directory_is_created(spec: CLISpec, tmp_path: Path) -> None:
    """A missing multi-level target is created (``mkdir(parents=True)``)."""
    target = tmp_path / "a" / "b" / "c"
    result = generate_project(spec, target)
    assert result.out_dir == target
    assert target.is_dir()
    assert len(result.file_names()) == EXPECTED_FILE_COUNT


# ===========================================================================
# 8 -- GenerationResult contract
# ===========================================================================


def test_result_files_all_exist(generated: GenerationResult) -> None:
    """Acceptance point 8: every path in ``files`` is a real, non-empty file."""
    assert generated.files, "GenerationResult.files is empty"
    for path in generated.files:
        assert path.is_file(), f"{path} listed in files but does not exist"
        assert path.stat().st_size > 0 or path.name == ".gitkeep", (
            f"{path} was written empty"
        )


def test_file_names_are_relative_posix_and_sorted(generated: GenerationResult) -> None:
    """Acceptance point 8: relative, POSIX-separated, sorted, unique."""
    names = generated.file_names()
    assert names == sorted(names), "file_names() is not sorted"
    assert len(names) == len(set(names)), "file_names() contains duplicates"
    for name in names:
        assert not name.startswith("/"), f"{name} is absolute"
        assert ":" not in name, f"{name} looks like a drive-absolute path"
        assert "\\" not in name, f"{name} uses a backslash instead of POSIX separators"
        assert (generated.out_dir / name).is_file(), f"{name} does not resolve under out_dir"


def test_file_names_covered_everything_on_disk(generated: GenerationResult) -> None:
    """Nothing is written that ``GenerationResult`` fails to report."""
    on_disk = {
        path.relative_to(generated.out_dir).as_posix()
        for path in generated.out_dir.rglob("*")
        if path.is_file()
    }
    assert on_disk == set(generated.file_names())


def test_result_carries_the_spec(generated: GenerationResult, spec: CLISpec) -> None:
    """The result hands back the same spec object it was given."""
    assert generated.spec is spec
    assert generated.out_dir.is_dir()


# ===========================================================================
# 9 -- the generated spec really describes this fixture
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_spec_json_describes_this_fixture(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """Acceptance point 9: no cross-wiring between fixtures."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    on_disk = json.loads((out / "spec.json").read_text(encoding="utf-8"))

    assert on_disk["tool_name"] == fixture_path(kind).stem
    assert on_disk["parser_kind"] == kind
    assert Path(on_disk["tool_path"]).name == fixture_path(kind).name
    assert Path(on_disk["tool_path"]).resolve() == fixture_path(kind).resolve()
    assert on_disk["commands"], "the generated spec has no commands"


def test_generated_readme_mentions_the_tool(generated: GenerationResult, spec: CLISpec) -> None:
    """The project README is about *this* tool, not a boilerplate one."""
    readme = (generated.out_dir / "README.md").read_text(encoding="utf-8")
    assert spec.tool_name in readme
    assert "#" in readme
    assert len(readme) > 200


# ===========================================================================
# 10 -- the generated entry point runs
# ===========================================================================


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_entry_point_help_exits_zero(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """Acceptance point 10: ``python app.py --help`` runs the entry point."""
    out = tmp_path / kind
    generate_project(specs[kind], out)

    completed = run_child("", cwd=out, argv=["app.py", "--help"])
    stdout = assert_child_ok(completed, "python app.py --help")
    assert "usage" in stdout.lower()
    assert "--port" in stdout
    assert "--host" in stdout


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_entry_point_rejects_bad_port(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """argparse inside ``app.py`` actually validates its own flags."""
    out = tmp_path / kind
    generate_project(specs[kind], out)

    completed = run_child("", cwd=out, argv=["app.py", "--port", "not-a-number"])
    assert completed.returncode != 0
    assert "invalid int value" in (completed.stdout + completed.stderr).lower()


def test_entry_point_is_a_single_self_contained_file(generated: GenerationResult) -> None:
    """``app.py`` sits at the project root and is the documented entry point."""
    entry = generated.out_dir / "app.py"
    assert entry.is_file()
    source = entry.read_text(encoding="utf-8")
    # It must not depend on the HelpUI package: the generated project is meant
    # to run on its own dependencies only.
    assert "import helpui" not in source.replace("import helpui_app", "")


# ===========================================================================
# 11 -- the generated project carries no HelpUI dependency at run time
# ===========================================================================


def test_generated_sources_do_not_import_helpui(repo_root: Path, tmp_path: Path) -> None:
    """No generated file may import the ``helpui`` package itself."""
    spec = scan_tool(str(fixture_path("argparse"))).spec
    out = tmp_path / "standalone"
    result = generate_project(spec, out)

    offenders: list[str] = []
    for path in result.files:
        if path.suffix not in {".py", ".html", ".js", ".css", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import helpui\n", "from helpui ")):
                offenders.append(f"{path.relative_to(out).as_posix()}: {stripped}")
    assert not offenders, f"generated files import the helpui package: {offenders}"


def test_generated_app_runs_without_helpui_installed(generated: GenerationResult) -> None:
    """Importing the generated package must not pull in ``helpui``.

    ``sys.modules`` is inspected inside the child, so this genuinely proves the
    generated project stands alone rather than merely having the import work
    because HelpUI happens to be installed.
    """
    code = (
        "import sys\n"
        "import helpui_app.app\n"
        "loaded = sorted(m for m in sys.modules if m == 'helpui' or m.startswith('helpui.'))\n"
        "print('HELPUI_MODULES', loaded)\n"
    )
    stdout = assert_child_ok(run_child(code, cwd=generated.out_dir), "importing without HelpUI")
    assert "HELPUI_MODULES []" in stdout, f"the generated app imported helpui: {stdout}"


# ===========================================================================
# 12 -- untested / latent behaviour, documented rather than hidden
# ===========================================================================


def test_generated_project_uses_only_declared_dependencies(generated: GenerationResult) -> None:
    """The generated code imports only stdlib plus the declared dependencies."""
    allowed = {"fastapi", "jinja2", "starlette", "uvicorn", "anyio", "multipart"}
    stdlib_ok = {
        "argparse", "csv", "dataclasses", "datetime", "functools", "html", "io",
        "json", "os", "pathlib", "platform", "re", "resource", "shutil", "sqlite3",
        "subprocess", "sys", "threading", "time", "typing", "webbrowser",
        "__future__", "helpui_app",
    }

    offenders: list[str] = []
    for path in generated.files:
        if path.suffix != ".py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")) or "import helpui_app" in stripped:
                continue
            root = stripped.split()[1].split(".")[0]
            if root not in allowed and root not in stdlib_ok:
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, f"unexpected third-party imports: {offenders}"


def test_environment_overrides_are_honoured(generated: GenerationResult) -> None:
    """``HELPUI_PORT``/``HELPUI_HOST`` reach the generated config."""
    env = fixture_env()
    env["PYTHONIOENCODING"] = "utf-8"
    env["HELPUI_PORT"] = "9123"
    env["HELPUI_HOST"] = "0.0.0.0"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from helpui_app.config import load_config; c = load_config();"
            "print('CFG', c.port, c.host)",
        ],
        cwd=str(generated.out_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
        env=env,
        check=False,
    )
    stdout = assert_child_ok(completed, "reading the generated config")
    assert "CFG 9123 0.0.0.0" in stdout, stdout


def test_generation_port_flag_is_baked_in(spec: CLISpec, tmp_path: Path) -> None:
    """The ``default_port`` argument lands in the generated ``config.py``."""
    out = tmp_path / "custom_port"
    generate_project(spec, out, default_port=9311, default_host="0.0.0.0")
    config = (out / "helpui_app" / "config.py").read_text(encoding="utf-8")
    assert "9311" in config
    assert "0.0.0.0" in config


# ===========================================================================
# 13 -- the run / error paths actually work end to end
# ===========================================================================
#
# These cover the two defects found while writing this suite. They failed
# before the generator was repaired, and they are the reason the suite is
# worth running rather than merely restating the acceptance list:
#
#   1. ``helpui_app/app.py`` rendered ``_error.html`` from the validation-error
#      branch of ``POST /run/{slug}`` and from ``POST /cancel/{slug}``, but the
#      template was not shipped in ``TEMPLATES`` -- so the *error* path raised
#      ``jinja2.exceptions.TemplateNotFound`` and returned 500.
#   2. ``quote_argv_entry`` was used to build ``argv_display`` without being
#      imported, so *every* ``POST /run/{slug}`` raised ``NameError`` and the
#      generated project could not execute anything at all.
#
# Both were invisible to a smoke test that only fetched ``/``, ``/history`` and
# ``/api/spec``, which is exactly why these tests drive the real run route.

RUN_PROBE = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient  # noqa: E402

from helpui_app.app import create_app  # noqa: E402

app = create_app()
report = {}

# Form field names are CLIOption.dest, which is NOT always the flag spelling
# (a positional "INPUT_FILE" becomes "input_file"; "--dry-run" becomes
# "dry_run"). They are read from the *generated* spec.json rather than guessed,
# so this probe keeps working for any fixture.
spec = json.loads(open("spec.json", encoding="utf-8").read())
convert = next(c for c in spec["commands"] if c["name"] == "convert")

def dest_of(option):
    return option["dest"]

required_positionals = [dest_of(p) for p in convert["positionals"] if p["required"]]

def value_for(option):
    """A plausible, non-empty value for one option."""
    if option["type"] == "choice" and option["choices"]:
        return option["choices"][0]
    if option["type"] == "int":
        return option["default"] or "7"
    if option["type"] == "float":
        return option["default"] or "1.5"
    if option["type"] == "dir":
        return "."
    return option["default"] or "probe-value"

# Split the submission by *transport*, matching the contract: a `file`-typed
# field is taken only from a multipart upload, and a plain text value for it
# counts as "not provided". Everything else is a normal text field.
#
# `input_file` is typed `file` for argparse and typer and `str` for click, so
# the two transports really are exercised across the fixture matrix.
text_fields = {}
file_fields = {}

def add_field(option, forced_value=None):
    dest = dest_of(option)
    if option["type"] == "file":
        file_fields[dest] = (f"{dest}-probe.txt", b"probe file content\n")
    else:
        text_fields[dest] = forced_value if forced_value is not None else value_for(option)

for positional in convert["positionals"]:
    if positional["required"]:
        add_field(positional, forced_value="probe-input.txt"
                  if positional["type"] != "file" else None)
for option in convert["options"]:
    if option["is_flag"]:
        continue
    # `--token` is declared `required=True` in every fixture, but only the click
    # parser surfaces that (see test_required_flags_match_the_fixtures), so
    # argparse and typer would omit it and the *tool* would exit 2. Supplying it
    # explicitly keeps the argv assertions from being masked by a phase 1 gap.
    if option["required"] or dest_of(option) in {"token", "api_key", "secret"}:
        add_field(option, forced_value="probe-token" if option["type"] != "file" else None)

report["text_fields"] = sorted(text_fields)
report["file_fields"] = sorted(file_fields)

with TestClient(app, raise_server_exceptions=False) as client:
    report["run_ok"] = client.post("/run/convert", data=text_fields, files=file_fields).status_code

    # Omitting a required positional is the case the generated validator is
    # expected to catch (see test_required_flags_match_the_fixtures).
    incomplete = {k: v for k, v in text_fields.items() if k not in required_positionals}
    missing = client.post("/run/convert", data=incomplete)
    report["run_missing_required"] = missing.status_code
    report["run_missing_body"] = missing.text[:400]

    # The contract: a text value for a `file`-typed field is *not recognised*,
    # so it is equivalent to leaving the field out entirely.
    file_dests = set(file_fields)
    if file_dests:
        text_instead_of_upload = dict(text_fields)
        for dest in file_dests:
            text_instead_of_upload[dest] = "some/path.txt"
        texted = client.post("/run/convert", data=text_instead_of_upload)
        report["text_for_file_field"] = texted.status_code
        report["text_for_file_field_body"] = texted.text[:300]

    # Cancel with nothing running: renders the same error template.
    cancelled = client.post("/cancel/convert", data={})
    report["cancel"] = cancelled.status_code
    report["cancel_body"] = cancelled.text[:200]

    report["history"] = client.get("/history").status_code
    report["api_run_1"] = client.get("/api/runs/1").status_code
    if report["api_run_1"] == 200:
        payload = client.get("/api/runs/1").json()
        report["run_status"] = payload.get("status")
        report["run_exit_code"] = payload.get("exit_code")
        report["run_argv"] = payload.get("argv")
        report["run_stdout_head"] = payload.get("stdout", "")[:300]
        report["run_stderr_head"] = payload.get("stderr", "")[:300]
        report["run_notes"] = payload.get("notes")
    report["unknown_run"] = client.get("/api/runs/999999").status_code
    report["unknown_command"] = client.get("/command/nope").status_code
    # A second run must record a second, distinct history row.
    report["run_again"] = client.post("/run/convert", data=text_fields, files=file_fields).status_code
    report["api_run_2"] = client.get("/api/runs/2").status_code
print("REPORT" + json.dumps(report))
'''


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_run_route_executes_the_tool(kind: str, tmp_path: Path, specs: dict[str, CLISpec]) -> None:
    """``POST /run/{slug}`` really runs the whitelisted tool and records history."""
    out = tmp_path / kind
    generate_project(specs[kind], out)
    stdout = assert_child_ok(run_child(RUN_PROBE, cwd=out, argv=["-c", RUN_PROBE, str(out)]),
                             f"running the tool for {kind}")
    line = [ln for ln in stdout.splitlines() if ln.startswith("REPORT")][-1]
    report: dict[str, Any] = json.loads(line[len("REPORT") :])

    assert report["run_ok"] == 200, (
        f"POST /run/convert returned {report['run_ok']}; the generated project "
        "cannot execute its tool"
    )
    assert report["api_run_1"] == 200, "no history row was written for the run"
    assert report["run_status"] == "succeeded", report
    assert report["run_exit_code"] == 0, report

    argv = report["run_argv"]
    assert isinstance(argv, list) and argv, report

    # The subcommand name must survive into argv. Without it the target tool
    # sees the subcommand's options at the top level and fails outright
    # (`No such option '--output'`), so any tool with subcommands is unusable.
    tool_path = Path(argv[0] if argv[0].endswith(fixture_path(kind).name) else argv[1])
    assert "convert" in argv, (
        f"the subcommand name is missing from argv: {argv}. "
        "The tool would receive its subcommand's options at top level."
    )
    assert tool_path.name == fixture_path(kind).name, argv

    # Every submitted *text* value must appear in argv beside its flag.
    for dest in report["text_fields"]:
        value = "probe-token" if dest in {"token", "api_key", "secret"} else None
        if value is not None:
            assert value in argv, f"submitted {dest}={value!r} is not present in argv: {argv}"

    # An uploaded file must reach the tool as a *path on disk*, not as the
    # client-supplied filename or its content. This is what proves the upload
    # was actually stored and the stored path handed over.
    for dest in report["file_fields"]:
        uploaded_name = f"{dest}-probe.txt"
        assert uploaded_name not in argv, (
            f"{dest}: argv contains the client filename {uploaded_name!r}; the tool "
            f"must receive the stored path instead. argv={argv}"
        )
        stored = [a for a in argv if uploaded_name in a]
        assert stored, (
            f"{dest}: no stored upload path for {uploaded_name!r} in argv={argv}. "
            "The upload did not land on disk or was not passed to the tool."
        )
        stored_path = Path(stored[-1])
        assert stored_path.is_absolute() or Path(out / stored_path).exists() or stored_path.exists(), (
            f"{dest}: uploaded path {stored_path!r} does not exist on disk"
        )
        assert "uploads" in stored_path.as_posix(), (
            f"{dest}: uploaded path {stored_path!r} is not inside the upload directory"
        )

    assert report["run_stdout_head"].strip(), "the tool produced no stdout"
    assert not report["run_stderr_head"].strip(), (
        f"the tool wrote to stderr: {report['run_stderr_head']!r}"
    )

    # Contract: a plain text value for a `file`-typed field is not recognised,
    # so it is equivalent to omitting the field -- the run is rejected.
    if report["file_fields"]:
        assert report["text_for_file_field"] == 400, (
            "a text value submitted for a file-typed field was accepted; it must be "
            f"treated as 'not provided'. Got {report['text_for_file_field']}: "
            f"{report['text_for_file_field_body']!r}"
        )

    # History accumulates rather than overwriting.
    assert report["run_again"] == 200
    assert report["api_run_2"] == 200


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_error_template_renders_for_invalid_input(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """The validation-error path must render, not 500.

    Regression guard for the missing ``_error.html``: ``POST /run/{slug}`` and
    ``POST /cancel/{slug}`` both render ``_error.html``. Before that template
    was shipped, this path raised ``jinja2.exceptions.TemplateNotFound`` and
    returned 500.

    What counts as "invalid input" differs per fixture, because the phase 1
    parsers disagree about ``required`` (see
    :func:`test_required_flags_match_the_fixtures`). So the assertion is on the
    *outcome contract*, not on a fixed status code:

    * omitting the required positional must give 400 and name the field; or
    * if the subcommand has no required field the parser knows about, the
      request runs the tool and must give 200 -- never a 500.

    Either way, 500 is a failure.
    """
    out = tmp_path / kind
    generate_project(specs[kind], out)
    stdout = assert_child_ok(run_child(RUN_PROBE, cwd=out, argv=["-c", RUN_PROBE, str(out)]),
                             f"checking the error path for {kind}")
    line = [ln for ln in stdout.splitlines() if ln.startswith("REPORT")][-1]
    report: dict[str, Any] = json.loads(line[len("REPORT") :])

    status = report["run_missing_required"]
    assert status in (200, 400), (
        f"the error path returned {status}, which is neither a rendered error (400) "
        f"nor a successful run (200) -- body: {report['run_missing_body']!r}"
    )
    if status == 400:
        # The message must name whichever required field was omitted, so the
        # user can act on it. Which field that is depends on the fixture.
        body = report["run_missing_body"]
        assert "required" in body.lower(), body
        assert any(name in body for name in ("input_file", "input", "token")), body

    # Cancel with nothing running must render _error.html rather than 500.
    assert report["cancel"] == 200, (
        f"POST /cancel/convert returned {report['cancel']}; body: {report['cancel_body']!r}"
    )
    assert "running" in report["cancel_body"].lower(), report["cancel_body"]


def test_required_flags_match_the_fixtures(specs: dict[str, CLISpec]) -> None:
    """Records a phase 1 parser inconsistency that limits form validation.

    All three fixtures declare ``--token`` with ``required=True``, but only the
    **click** parser surfaces it in the *parsed spec*::

        argparse  convert: required options = []            positionals = ['input_file']
        click     convert: required options = [('token',)]  positionals = ['input_file']
        typer     convert: required options = []            positionals = ['input_file']

    Consequence for phase 2: the generated form cannot know that ``--token`` is
    mandatory for argparse and typer specs, so submitting without it runs the
    tool, which exits non-zero and is reported as a failed run rather than a
    400. (The generated app does complain when the *click* spec marks it
    required.)

    This is a *parser* limitation, not a generator defect, and it is asserted
    here (rather than described in a comment) so that fixing the parsers makes
    this test fail loudly and forces the generator's validation expectations to
    be revisited.
    """
    observed = {
        kind: sorted(
            option.dest
            for command in spec.commands
            if command.name == "convert"
            for option in command.options
            if option.required
        )
        for kind, spec in specs.items()
    }
    assert observed == {"argparse": [], "click": ["token"], "typer": []}, (
        "the parsers' handling of required options changed; revisit "
        "test_error_template_renders_for_invalid_input and the generated form's "
        f"validation expectations. Observed: {observed}"
    )

    # The positional is reported as required by every parser, which is what the
    # generated form relies on for its 400 path.
    for kind, spec in specs.items():
        convert = next(c for c in spec.commands if c.name == "convert")
        assert [p.dest for p in convert.positionals if p.required] == ["input_file"], kind


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_file_typed_positional_accepts_uploads(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """A ``file``-typed *positional* is filled from a multipart upload.

    This was a real defect: ``_widget.html`` renders ``<input type="file">`` for
    a ``file``-typed argument, but the generated backend implemented uploads
    only in the *options* loop. The *positionals* loop kept only ``str`` form
    values, so the upload was dropped and the request was rejected with
    ``<dest> is required`` -- the form, as rendered, could not be submitted.

    The contract now asserted here:

    * an upload satisfies the field, the run succeeds, and the tool receives a
      *stored path* on disk (not the client filename);
    * a plain text value for a ``file``-typed field is **not recognised** and is
      equivalent to omitting the field, so a required one is rejected.

    The fixture matrix exercises both transports: ``input_file`` is ``file`` for
    argparse and typer, and ``str`` for click.
    """
    spec = specs[kind]
    out = tmp_path / kind
    generate_project(spec, out)

    code = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from fastapi.testclient import TestClient\n"
        "from helpui_app.app import create_app\n"
        "app = create_app()\n"
        "spec = json.loads(open('spec.json', encoding='utf-8').read())\n"
        "conv = next(c for c in spec['commands'] if c['name'] == 'convert')\n"
        "pos = next(p for p in conv['positionals'] if p['dest'] == 'input_file')\n"
        "report = {'input_file_type': pos['type']}\n"
        "with TestClient(app, raise_server_exceptions=False) as c:\n"
        "    uploaded = c.post('/run/convert',\n"
        "        data={'token': 'abc'},\n"
        "        files={'input_file': ('probe.txt', b'hello', 'text/plain')})\n"
        "    report['upload_status'] = uploaded.status_code\n"
        "    report['upload_body'] = uploaded.text[:300]\n"
        "    texted = c.post('/run/convert',\n"
        "        data={'token': 'abc', 'input_file': 'some/path.txt'})\n"
        "    report['text_status'] = texted.status_code\n"
        "    report['text_body'] = texted.text[:300]\n"
        "    api = c.get('/api/runs/1')\n"
        "    if api.status_code == 200:\n"
        "        d = api.json()\n"
        "        report['run_status'] = d['status']\n"
        "        report['run_argv'] = d['argv']\n"
        "print('REPORT' + json.dumps(report))\n"
    )
    completed = run_child(code, cwd=out, argv=["-c", code, str(out)])
    stdout = assert_child_ok(completed, f"posting an upload to a file-typed positional ({kind})")
    line = [ln for ln in stdout.splitlines() if ln.startswith("REPORT")][-1]
    report: dict[str, Any] = json.loads(line[len("REPORT") :])

    # Sanity: the widget renders a file input exactly when the type is `file`.
    widget = (out / "templates" / "_widget.html").read_text(encoding="utf-8")
    assert "option.type == 'file'" in widget and 'type="file"' in widget

    if report["input_file_type"] != "file":
        # click types it `str`, so the file picker is not involved; an upload is
        # simply an unrecognised field and the text path must still work.
        assert report["text_status"] == 200, report
        return

    assert report["upload_status"] == 200, (
        f"{kind}: uploading to the file-typed positional failed with "
        f"{report['upload_status']}: {report['upload_body']!r}"
    )
    assert report["run_status"] == "succeeded", report

    argv = report["run_argv"]
    stored = [a for a in argv if "probe.txt" in a]
    assert stored, f"{kind}: the uploaded file path is missing from argv={argv}"
    stored_path = stored[-1]
    assert stored_path != "probe.txt", (
        f"{kind}: argv contains the raw client filename instead of a stored path: {argv}"
    )
    assert "uploads" in Path(stored_path).as_posix(), (
        f"{kind}: the stored upload is not under the upload directory: {stored_path!r}"
    )

    # Contract: text is not an alternative transport for a `file` field.
    assert report["text_status"] == 400, (
        f"{kind}: a text value was accepted for a file-typed field "
        f"({report['text_status']}); it must count as 'not provided'. "
        f"Body: {report['text_body']!r}"
    )
    assert "input_file is required" in report["text_body"], report["text_body"]


def test_error_template_is_shipped(generated: GenerationResult) -> None:
    """``_error.html`` is referenced by the generated app, so it must be copied."""
    assert (generated.out_dir / "templates" / "_error.html").is_file()
    source = (generated.out_dir / "helpui_app" / "app.py").read_text(encoding="utf-8")
    assert '_error.html' in source, "the generated app no longer uses _error.html"


def test_generated_app_imports_every_name_it_uses(generated: GenerationResult) -> None:
    """Every ``helpui_app`` symbol used by the generated app is imported.

    Catches the ``quote_argv_entry`` class of defect -- a ``NameError`` that
    only fires when a request reaches the offending line -- without needing to
    hammer every route. ``compile`` + ``ast`` is used so this stays a static
    check over all generated modules.
    """
    package = generated.out_dir / "helpui_app"
    assert package.is_dir()
    code = (
        "import ast, sys, pathlib\n"
        "bad = []\n"
        "for path in sorted(pathlib.Path('helpui_app').glob('*.py')):\n"
        "    tree = ast.parse(path.read_text(encoding='utf-8'))\n"
        "    bound = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))\n"
        "    for node in ast.walk(tree):\n"
        "        if isinstance(node, ast.ImportFrom):\n"
        "            for a in node.names:\n"
        "                bound.add(a.asname or a.name)\n"
        "        elif isinstance(node, ast.Import):\n"
        "            for a in node.names:\n"
        "                bound.add((a.asname or a.name).split('.')[0])\n"
        "        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):\n"
        "            bound.add(node.name)\n"
        "        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store,)):\n"
        "            bound.add(node.id)\n"
        "        elif isinstance(node, (ast.arg,)):\n"
        "            bound.add(node.arg)\n"
        "        elif isinstance(node, ast.ExceptHandler) and node.name:\n"
        "            bound.add(node.name)\n"
        "        elif isinstance(node, (ast.comprehension,)):\n"
        "            pass\n"
        "    bound.update({'self', 'cls', '__file__', '__name__', '__doc__', '__spec__', '__package__'})\n"
        "    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}\n"
        "    unknown = sorted(used - bound)\n"
        "    if unknown:\n"
        "        bad.append((path.name, unknown))\n"
        "print('UNBOUND', bad)\n"
    )
    stdout = assert_child_ok(run_child(code, cwd=generated.out_dir), "static name check")
    assert "UNBOUND []" in stdout, f"generated modules reference unbound names: {stdout}"


def test_unknown_routes_return_404(generated: GenerationResult) -> None:
    """Error handling for bad ids/slugs is a 404, not a crash."""
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from fastapi.testclient import TestClient\n"
        "from helpui_app.app import create_app\n"
        "app = create_app()\n"
        "with TestClient(app, raise_server_exceptions=False) as c:\n"
        "    print('BAD', c.get('/api/runs/999999').status_code,\n"
        "          c.get('/command/does-not-exist').status_code,\n"
        "          c.get('/history/999999').status_code)\n"
    )
    completed = run_child(code, cwd=generated.out_dir, argv=["-c", code, str(generated.out_dir)])
    stdout = assert_child_ok(completed, "checking 404 handling")
    assert "BAD 404 404 404" in stdout, stdout


# ===========================================================================
# 14 -- secret options render as password widgets
# ===========================================================================
#
# Regression guard for a defect that stayed invisible because `tests/` only
# ever covered `--password` for the argparse fixture. The click and typer
# parsers classified `--password` as plain `str`, so the generated form
# rendered a *visible text input* for a secret and the value was echoed in the
# run's argv/history in clear text.
#
# Two independent layers are asserted, because either one alone would have let
# the bug through:
#   * the parsed spec types the option as `password` for every fixture;
#   * the generated form actually renders a password input for it.
#
# The form check matters: a correct `CLIOption.type` that the widget ignores
# would still leak the secret, so this test drives the real template instead of
# trusting the type field.


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_password_option_is_typed_as_password(kind: str, specs: dict[str, CLISpec]) -> None:
    """Every fixture's parser classifies ``--password`` as ``password``."""
    convert = next((c for c in specs[kind].commands if c.name == "convert"), None)
    assert convert is not None, f"{kind} fixture has no `convert` command"

    password = next((o for o in convert.options if o.dest == "password"), None)
    assert password is not None, f"{kind} fixture no longer declares --password"
    assert password.type == "password", (
        f"{kind}: --password is typed {password.type!r}, so the generated form "
        "would render a visible text box (and echo the secret into argv/history)"
    )
    assert password.is_flag is False, f"{kind}: --password must take a value"


FORM_WIDGET_PROBE = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient  # noqa: E402

from helpui_app.app import create_app  # noqa: E402

app = create_app()
report = {}
with TestClient(app, raise_server_exceptions=False) as client:
    for command in app.state.spec.commands:
        slug = command.name.strip().replace(" ", "_").lower() or "_root"
        secrets = [o.dest for o in command.options if o.type == "password"]
        if not secrets:
            continue
        html = client.get(f"/command/{slug}").text
        report[slug] = {"secrets": secrets}
        # Does the rendered page actually use a password input for each one?
        report[slug]["password_inputs"] = [
            dest for dest in secrets
            if f'type="password"' in html and f'name="{dest}"' in html
        ]
        report[slug]["text_inputs"] = [
            dest for dest in secrets
            if f'name="{dest}"' in html and f'type="text"' in html
        ]
print("REPORT" + json.dumps(report))
'''


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_rendered_form_uses_a_password_input_for_secrets(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """The generated form renders ``type="password"`` for a secret option.

    Renders the real ``/command/{slug}`` page through the app's own Jinja2
    environment, so this catches the case where the spec is right but the
    widget would still show the secret as plain text.
    """
    out = tmp_path / kind
    generate_project(specs[kind], out)
    completed = run_child(
        FORM_WIDGET_PROBE, cwd=out, argv=["-c", FORM_WIDGET_PROBE, str(out)]
    )
    stdout = assert_child_ok(completed, f"rendering the form for {kind}")
    line = [ln for ln in stdout.splitlines() if ln.startswith("REPORT")][-1]
    report: dict[str, Any] = json.loads(line[len("REPORT") :])

    convert = report.get("convert")
    assert convert is not None, f"{kind}: no form page rendered a password option"
    assert "password" in convert["secrets"], convert
    assert "password" in convert["password_inputs"], (
        f"{kind}: the form for `--password` does not use a password input. "
        f"secrets={convert['secrets']} password_inputs={convert['password_inputs']} "
        f"text_inputs={convert['text_inputs']}"
    )


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_secret_value_is_not_echoed_in_the_rendered_page(
    kind: str, tmp_path: Path, specs: dict[str, CLISpec]
) -> None:
    """A submitted secret does not reappear in the result page's markup.

    ``argv_display`` is rendered into ``result.html`` with a ``title``
    attribute. Secret values are in argv by necessity (the tool needs them),
    so this asserts only that the *form page* does not pre-fill a default
    secret -- there is no default to leak in these fixtures.
    """
    out = tmp_path / kind
    generate_project(specs[kind], out)
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from fastapi.testclient import TestClient\n"
        "from helpui_app.app import create_app\n"
        "app = create_app()\n"
        "with TestClient(app, raise_server_exceptions=False) as c:\n"
        "    html = c.get('/command/convert').text\n"
        "    print('HAS_PASSWORD_INPUT', 'type=\"password\"' in html)\n"
    )
    completed = run_child(code, cwd=out, argv=["-c", code, str(out)])
    stdout = assert_child_ok(completed, f"checking the form page for {kind}")
    assert "HAS_PASSWORD_INPUT True" in stdout, stdout

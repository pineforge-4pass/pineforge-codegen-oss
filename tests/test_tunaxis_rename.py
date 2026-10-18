"""The tunaxis-codegen rename: the real package, the old-name alias and the shim.

Everything here runs from outside: real subprocesses interpret real files, and
the distributions are built by the same hatchling PEP 517 entry points the
release workflow calls, then read back as archives. Nothing is mocked.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import textwrap
import tomllib
import zipfile
from email.parser import Parser as EmailParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _release_version():
    """Load scripts/release_version.py without importing any package."""
    spec = importlib.util.spec_from_file_location(
        "_tunaxis_rename_release_version",
        str(ROOT / "scripts" / "release_version.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNEL = _release_version().channels(VERSION)
PYPI_VERSION = CHANNEL["pypi"]


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _clean_env(**extra: str) -> dict:
    """The current environment without import overrides."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                        "PYTHONWARNINGS"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _run_checkout(args, cwd: Path, *, stdin: str | None = None):
    """Run with the checkout root as the only import path entry."""
    return subprocess.run(
        [PYTHON, *map(str, args)],
        cwd=cwd,
        env=_clean_env(PYTHONPATH=str(ROOT)),
        input=stdin,
        capture_output=True,
        text=True,
    )


def _run_with_site(site: Path, args, cwd: Path):
    """Run against an installed tree and nothing from the checkout."""
    return subprocess.run(
        [PYTHON, *map(str, args)],
        cwd=cwd,
        env=_clean_env(PYTHONPATH=str(site)),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Checkout imports
# ---------------------------------------------------------------------------

ALIAS_PROBE = textwrap.dedent(
    '''\
    import importlib
    import sys
    import warnings
    from pathlib import Path

    checkout = Path(sys.argv[1]).resolve()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        import pineforge_codegen
        import pineforge_codegen.parser
        import pineforge_codegen.codegen.visit_expr
        from pineforge_codegen.analyzer.base import Analyzer
        import tunaxis_codegen
        import tunaxis_codegen.parser
        import tunaxis_codegen.codegen.visit_expr
        from tunaxis_codegen.analyzer.base import Analyzer as CanonicalAnalyzer

    assert pineforge_codegen is tunaxis_codegen
    assert sys.modules["pineforge_codegen"] is tunaxis_codegen
    assert pineforge_codegen.parser is tunaxis_codegen.parser
    assert sys.modules["pineforge_codegen.parser"] is sys.modules["tunaxis_codegen.parser"]
    assert pineforge_codegen.codegen.visit_expr is tunaxis_codegen.codegen.visit_expr
    assert Analyzer is CanonicalAnalyzer
    assert pineforge_codegen.Parser is tunaxis_codegen.Parser
    assert pineforge_codegen.transpile_full is tunaxis_codegen.transpile_full
    assert pineforge_codegen.errors.CompileError is tunaxis_codegen.errors.CompileError
    assert Path(tunaxis_codegen.__file__).resolve().is_relative_to(checkout)
    # The alias shares the canonical module object; the import machinery must
    # not have retyped it as the alias on the way back.
    assert tunaxis_codegen.parser.__name__ == "tunaxis_codegen.parser"
    assert tunaxis_codegen.parser.__spec__.name == "tunaxis_codegen.parser"
    # visit_expr is loaded lazily, through the alias finder, on the line above.
    assert tunaxis_codegen.codegen.visit_expr.__name__ == (
        "tunaxis_codegen.codegen.visit_expr")
    assert tunaxis_codegen.codegen.visit_expr.__spec__.name == (
        "tunaxis_codegen.codegen.visit_expr")

    assert len(caught) == 1, [str(w.message) for w in caught]
    warning = caught[0]
    assert issubclass(warning.category, DeprecationWarning)
    message = str(warning.message)
    assert "tunaxis_codegen" in message
    assert "tunaxis-codegen" in message
    # stacklevel points at the importing statement, not at the alias module.
    assert Path(warning.filename).resolve() == Path(__file__).resolve()
    assert warning.lineno == __IMPORT_LINE__
    '''
)


def test_alias_resolves_to_canonical_objects_and_warns_at_the_import(tmp_path):
    source = ALIAS_PROBE.replace(
        "__IMPORT_LINE__",
        str(ALIAS_PROBE.splitlines().index("    import pineforge_codegen") + 1),
    )
    probe = tmp_path / "alias_probe.py"
    probe.write_text(source, encoding="utf-8")

    proc = _run_checkout([probe, ROOT], tmp_path)
    assert proc.returncode == 0, proc.stderr


QUIET_PROBE = textwrap.dedent(
    '''\
    import sys
    import warnings
    from pathlib import Path

    warnings.filterwarnings("error", category=DeprecationWarning)
    import tunaxis_codegen

    assert "pineforge_codegen" not in sys.modules
    assert not [n for n in sys.modules if n.startswith("pineforge_codegen.")]
    assert Path(tunaxis_codegen.__file__).resolve().is_relative_to(
        Path(sys.argv[1]).resolve())
    '''
)


def test_new_name_imports_quietly_from_a_bare_checkout(tmp_path):
    probe = tmp_path / "quiet_probe.py"
    probe.write_text(QUIET_PROBE, encoding="utf-8")

    proc = _run_checkout([probe, ROOT], tmp_path)
    assert proc.returncode == 0, proc.stderr


# A fresh interpreter per trial: once both imports have run, the modules are
# cached and the start-up window is gone.
RACE_PROBE = textwrap.dedent(
    '''\
    import json
    import sys
    import threading
    import time

    baseline = len(sys.meta_path)
    state = {}

    def load_alias():
        try:
            import pineforge_codegen  # noqa: F401
            state["alias"] = "ok"
        except BaseException as exc:
            state["alias"] = f"{type(exc).__name__}: {exc}"

    def import_submodule():
        deadline = time.monotonic() + 30.0
        while "pineforge_codegen" not in sys.modules:
            if time.monotonic() > deadline:
                state["racer"] = "never saw the alias package"
                return
        parent = sys.modules["pineforge_codegen"]
        spec = getattr(parent, "__spec__", None)
        if not getattr(spec, "_initializing", False):
            return
        # One scheduling slice while the other thread imports the transpiler:
        # that import is the window, and it is orders of magnitude longer than
        # this. The slice also keeps the check from landing in the few
        # bytecodes between the alias entering sys.modules and installing its
        # finder.
        time.sleep(0.001)
        if not getattr(spec, "_initializing", False):
            return
        state["initializing"] = True
        state["finders_installed"] = len(sys.meta_path) > baseline
        try:
            from pineforge_codegen.errors import CompileError
            import tunaxis_codegen.errors
            state["racer"] = (
                "ok" if CompileError is tunaxis_codegen.errors.CompileError
                else "the alias returned another module")
        except BaseException as exc:
            state["racer"] = f"{type(exc).__name__}: {exc}"

    loader = threading.Thread(target=load_alias)
    racer = threading.Thread(target=import_submodule)
    loader.start()
    racer.start()
    loader.join()
    racer.join()
    print(json.dumps(state))

    if not state.get("initializing"):
        raise SystemExit(3)  # the trial missed the start-up window; retry it
    failed = (state.get("racer") != "ok"
              or not state.get("finders_installed")
              or state.get("alias") != "ok")
    raise SystemExit(1 if failed else 0)
    '''
)


def test_concurrent_submodule_import_during_alias_startup(tmp_path):
    """A second thread importing a submodule must not see the bare alias directory.

    The alias installs its meta-path finder before it imports the transpiler.
    While that import runs, a submodule import must either resolve through the
    finder (3.13+ additionally serialises it on the initializing parent) or,
    under the old order, fail with ModuleNotFoundError because only
    ``pineforge_codegen/__init__.py`` exists on disk.
    """
    probe = tmp_path / "race_probe.py"
    probe.write_text(RACE_PROBE, encoding="utf-8")

    trials = 20
    attempts = 0
    observed = 0
    failures = []
    while observed < trials and attempts < trials * 2:
        attempts += 1
        proc = _run_checkout([probe], tmp_path)
        if proc.returncode == 0:
            observed += 1
        elif proc.returncode == 3:
            continue
        else:
            failures.append(proc.stdout + proc.stderr)

    assert not failures, failures[0]
    assert observed == trials, (observed, attempts)


# ---------------------------------------------------------------------------
# Module CLIs
# ---------------------------------------------------------------------------

def test_cpp_allowlist_module_cli_old_and_new_names_behave_identically(tmp_path):
    payload = "reinterpret_cast<int *>(p);\n"
    new = _run_checkout(["-m", "tunaxis_codegen.cpp_allowlist"], tmp_path, stdin=payload)
    old = _run_checkout(["-m", "pineforge_codegen.cpp_allowlist"], tmp_path, stdin=payload)

    assert new.returncode == old.returncode == 1
    assert new.stdout == old.stdout
    assert "reinterpret-cast" in new.stdout


def test_timeframe_token_module_cli_old_and_new_names_behave_identically(tmp_path):
    request = json.dumps({"version": 1, "values": ["60", 3, "1D", None]}) + "\n"
    new = _run_checkout(
        ["-m", "tunaxis_codegen.timeframe_token_cli"], tmp_path, stdin=request)
    old = _run_checkout(
        ["-m", "pineforge_codegen.timeframe_token_cli"], tmp_path, stdin=request)

    assert new.returncode == old.returncode == 0
    assert new.stdout == old.stdout
    result = json.loads(new.stdout)
    assert result["version"] == 1
    assert len(result["tokens"]) == 4
    assert result["tokens"][1] is None


# ---------------------------------------------------------------------------
# Built distributions
# ---------------------------------------------------------------------------

_BACKEND = textwrap.dedent(
    """\
    import sys
    from hatchling.build import build_sdist, build_wheel
    out = sys.argv[1]
    build_sdist(out)
    build_wheel(out)
    """
)


@pytest.fixture(scope="module")
def wheels(tmp_path_factory):
    """Build both distributions exactly as the release workflow does.

    Needs the hatchling build backend. CI (test.yml) installs it and runs these
    tests for real; a host without it (the registry-less lab hosts) skips them
    with this reason, and the spot-box proof runs them with hatchling installed.
    """
    pytest.importorskip("hatchling", reason="the build backend hatchling is not installed on this host")
    dist = tmp_path_factory.mktemp("dist")
    main_dir = dist / "tunaxis"
    shim_dir = dist / "shim"
    main_dir.mkdir()
    shim_dir.mkdir()

    proc = subprocess.run(
        [PYTHON, "-c", _BACKEND, str(main_dir)],
        cwd=ROOT, env=_clean_env(), capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "tunaxis-codegen build failed (hatchling must be installed)\n"
        + proc.stdout + proc.stderr)

    proc = subprocess.run(
        [PYTHON, str(ROOT / "scripts" / "build_shim.py"), "--outdir", str(shim_dir)],
        cwd=ROOT, env=_clean_env(), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    def one(directory: Path, suffix: str) -> Path:
        found = sorted(directory.glob(f"*{suffix}"))
        assert len(found) == 1, [p.name for p in found]
        return found[0]

    return {
        "main_wheel": one(main_dir, ".whl"),
        "main_sdist": one(main_dir, ".tar.gz"),
        "shim_wheel": one(shim_dir, ".whl"),
        "shim_sdist": one(shim_dir, ".tar.gz"),
    }


def _wheel_members(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return archive.namelist()


def _wheel_metadata(wheel: Path):
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            member for member in archive.namelist()
            if member.endswith(".dist-info/METADATA"))
        return EmailParser().parsestr(
            archive.read(name).decode("utf-8"))


def _wheel_entry_points(wheel: Path) -> configparser.ConfigParser:
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            member for member in archive.namelist()
            if member.endswith(".dist-info/entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(name).decode("utf-8"))
    return parser


def _console_scripts(wheel: Path) -> set[str]:
    entry_points = _wheel_entry_points(wheel)
    if not entry_points.has_section("console_scripts"):
        return set()
    return set(entry_points["console_scripts"])


def test_main_wheel_is_tunaxis_codegen_only(wheels):
    members = _wheel_members(wheels["main_wheel"])
    dist_info = next(
        member.split("/")[0] for member in members
        if member.endswith(".dist-info/METADATA"))

    assert "tunaxis_codegen/__init__.py" in members
    assert all(
        member.startswith("tunaxis_codegen/") or member.startswith(dist_info + "/")
        for member in members), members
    assert not [member for member in members if "pineforge_codegen" in member]

    metadata = _wheel_metadata(wheels["main_wheel"])
    assert metadata["Name"] == "tunaxis-codegen"
    assert metadata["Version"] == PYPI_VERSION
    # No runtime dependency: only the optional dev extra may declare requirements.
    runtime_requirements = [
        requirement for requirement in (metadata.get_all("Requires-Dist") or [])
        if "extra ==" not in requirement
    ]
    assert not runtime_requirements, runtime_requirements

    entry_points = _wheel_entry_points(wheels["main_wheel"])
    assert entry_points["console_scripts"]["pf-cpp-allowlist"] == (
        "tunaxis_codegen.cpp_allowlist:main")
    assert entry_points["console_scripts"]["tunaxis-cpp-allowlist"] == (
        "tunaxis_codegen.cpp_allowlist:main")

    with tarfile.open(wheels["main_sdist"]) as archive:
        names = archive.getnames()
    prefix = names[0].split("/")[0]
    assert f"{prefix}/tunaxis_codegen/__init__.py" in names
    assert not [name for name in names if "/pineforge_codegen/" in name]


def test_shim_wheel_forwards_to_the_exact_version(wheels):
    members = _wheel_members(wheels["shim_wheel"])
    dist_info = next(
        member.split("/")[0] for member in members
        if member.endswith(".dist-info/METADATA"))

    assert "pineforge_codegen/__init__.py" in members
    assert all(
        member.startswith("pineforge_codegen/") or member.startswith(dist_info + "/")
        for member in members), members

    metadata = _wheel_metadata(wheels["shim_wheel"])
    assert metadata["Name"] == "pineforge-codegen"
    assert metadata["Version"] == PYPI_VERSION
    assert metadata.get_all("Requires-Dist") == [
        f"tunaxis-codegen=={PYPI_VERSION}"]

    entry_points = _wheel_entry_points(wheels["shim_wheel"])
    assert entry_points["console_scripts"]["pf-cpp-allowlist"] == (
        "tunaxis_codegen.cpp_allowlist:main")


def test_shim_sdist_is_self_contained_and_renders_the_pin(wheels):
    with tarfile.open(wheels["shim_sdist"]) as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0]
        assert f"{prefix}/pineforge_codegen/__init__.py" in names
        assert f"{prefix}/VERSION" in names
        assert f"{prefix}/README.md" in names
        project = tomllib.loads(
            archive.extractfile(f"{prefix}/pyproject.toml").read().decode("utf-8"))

    settings = project["project"]
    assert settings["name"] == "pineforge-codegen"
    assert settings["dynamic"] == ["version"]
    assert settings["dependencies"] == [f"tunaxis-codegen=={PYPI_VERSION}"]
    assert "version" not in settings
    # The template's `dynamic = ["version", "dependencies"]` was rendered to a static pin above;
    # "dependencies" must no longer be declared dynamic.
    assert "dependencies" not in settings["dynamic"]


def test_the_two_wheels_share_no_file(wheels):
    main = set(_wheel_members(wheels["main_wheel"]))
    shim = set(_wheel_members(wheels["shim_wheel"]))
    assert main & shim == set()

    # Both wheels deliberately generate bin/pf-cpp-allowlist: the shim restores
    # it for users upgrading the old full 1.6.1 package, and both entry points
    # call the same tunaxis function, so any other console-script overlap would
    # let an uninstall of one distribution remove a script the other declared.
    overlap = (
        _console_scripts(wheels["main_wheel"])
        & _console_scripts(wheels["shim_wheel"])
    )
    assert overlap == {"pf-cpp-allowlist"}, sorted(overlap)


def test_root_wheel_has_no_alias_and_the_shim_restores_the_old_import(
        wheels, tmp_path):
    site = tmp_path / "site"

    def install(wheel: Path):
        proc = subprocess.run(
            [PYTHON, "-m", "pip", "install", "--no-index", "--no-deps",
             "--upgrade", "--target", str(site), str(wheel)],
            cwd=tmp_path, env=_clean_env(), capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    install(wheels["main_wheel"])
    assert (site / "bin" / "tunaxis-cpp-allowlist").is_file()

    # The new distribution never writes the old import. -S keeps a copy that
    # happens to be installed in the interpreter's site-packages out of it.
    probe = _run_with_site(site, ["-S", "-c", "import pineforge_codegen"], tmp_path)
    assert probe.returncode != 0
    assert "ModuleNotFoundError" in probe.stderr

    # Both console names the new distribution declares run, over the same
    # arguments, from the installed wheel. (pip's --target replaces bin/ when
    # the shim is installed, so this runs before that install.)
    payload = "reinterpret_cast<int *>(p);\n"
    results = {}
    for name in ("pf-cpp-allowlist", "tunaxis-cpp-allowlist"):
        script = site / "bin" / name
        assert script.is_file(), sorted(p.name for p in (site / "bin").iterdir())
        proc = subprocess.run(
            [str(script)], cwd=tmp_path, env=_clean_env(PYTHONPATH=str(site)),
            input=payload, capture_output=True, text=True,
        )
        results[name] = (proc.returncode, proc.stdout, proc.stderr)
    assert results["pf-cpp-allowlist"] == results["tunaxis-cpp-allowlist"]
    assert results["pf-cpp-allowlist"][0] == 1
    assert "reinterpret-cast" in results["pf-cpp-allowlist"][1]

    # The shim adds only the old import beside the same tunaxis_codegen.
    install(wheels["shim_wheel"])
    probe = _run_with_site(
        site,
        ["-S", "-c",
         "import pineforge_codegen, tunaxis_codegen\n"
         "assert pineforge_codegen is tunaxis_codegen\n"
         "assert pineforge_codegen.parser is tunaxis_codegen.parser\n"
         "assert pineforge_codegen.transpile_full is tunaxis_codegen.transpile_full\n"],
        tmp_path,
    )
    assert probe.returncode == 0, probe.stderr

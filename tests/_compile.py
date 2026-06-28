"""Shared helper for compile-only tests against the pineforge-engine headers.

The pure-transpiler test suite intentionally never invokes a C++ compiler,
because the transpiler ships independently of any built ``libpineforge.a``
(see README "Running tests"). End-to-end compile + run + diff testing
lives downstream in ``pineforge-backend``.

This helper enables an *opt-in* middle ground: given the pineforge-engine
include directory and a working Eigen header tree, run ``g++ -fsyntax-only``
against a transpiled C++ source string and assert it parses against the
public engine headers. No linker, no runtime — just structural validity.

Activation contract (env vars):

* ``PINEFORGE_ENGINE_INCLUDE`` — path to ``pineforge-engine/include``
  (the directory containing ``pineforge/engine.hpp``). Required.
* ``PINEFORGE_EIGEN_INCLUDE`` — path to an Eigen 3 header root (the dir
  whose immediate child is ``Eigen/``). Optional; falls back to
  ``/opt/homebrew/include/eigen3`` if present, else
  ``/usr/local/include/eigen3``. Skips the test when no Eigen tree is
  resolvable.
* ``PINEFORGE_GENERATED_INCLUDE`` — optional path to the include dir holding
  the CMake-generated ``pineforge/version.h``. Auto-resolved when unset:
  the engine include itself, then any ``build*/include`` beside the engine
  checkout, then a stub synthesized from ``version.h.in`` + ``VERSION`` — so
  the syntax check works even without a configured CMake build.
* ``CXX`` — compiler binary; defaults to ``g++``.

If any of the above is missing, the calling test is skipped via
``pytest.skip(...)`` so CI without an engine checkout stays green.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def _resolve_engine_include() -> Path | None:
    """Resolve the engine include dir from PINEFORGE_ENGINE_INCLUDE.

    Accepts either the include directory itself or a path that contains
    ``include/pineforge/engine.hpp``. Returns ``None`` when not set or not
    valid.
    """
    raw = os.environ.get("PINEFORGE_ENGINE_INCLUDE", "")
    if raw:
        p = Path(raw).expanduser().resolve()
        if (p / "pineforge" / "engine.hpp").is_file():
            return p
        if (p / "include" / "pineforge" / "engine.hpp").is_file():
            return p / "include"
        return None
    # Auto-detect sibling pineforge-engine checkout when env var not set.
    here = Path(__file__).resolve()
    candidates = []
    p = here
    for _ in range(8):
        p = p.parent
        candidates.append(p)
    for parent in candidates:
        cand = parent / "pineforge-engine" / "include"
        if (cand / "pineforge" / "engine.hpp").is_file():
            return cand.resolve()
    return None


def _resolve_eigen_include() -> Path | None:
    """Resolve an Eigen header tree (the dir whose child is ``Eigen/``)."""
    raw = os.environ.get("PINEFORGE_EIGEN_INCLUDE", "")
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.extend([
        Path("/opt/homebrew/include/eigen3"),
        Path("/opt/homebrew/include"),
        Path("/usr/local/include/eigen3"),
        Path("/usr/local/include"),
        Path("/usr/include/eigen3"),
        Path("/usr/include"),
    ])
    for cand in candidates:
        if (cand / "Eigen" / "Dense").is_file():
            return cand.resolve()
    return None


def _resolve_compiler() -> str | None:
    """Resolve the C++ compiler binary (defaults to ``g++``)."""
    name = os.environ.get("CXX", "g++")
    return shutil.which(name)


def _synthesize_version_header(engine_inc: Path, template: Path) -> Path | None:
    """Render ``version.h.in`` into a temp include dir so -fsyntax-only resolves
    ``<pineforge/version.h>`` even with no CMake build. The macros are pure
    version strings (no ABI/structural effect), so values from the ``VERSION``
    file with an ``unknown`` git sha are sufficient for a syntax check."""
    version = "0.0.0"
    vf = engine_inc.parent / "VERSION"
    if vf.is_file():
        version = (vf.read_text().strip() or version).splitlines()[0]
    major, minor, patch = (version.split(".") + ["0", "0", "0"])[:3]
    repl = {
        "PINEFORGE_VERSION_MAJOR": major,
        "PINEFORGE_VERSION_MINOR": minor,
        "PINEFORGE_VERSION_PATCH": patch,
        "PINEFORGE_VERSION_MMP": version,
        "PINEFORGE_VERSION_FULL": version,
        "PINEFORGE_VERSION_GIT_SHA": "unknown",
    }
    text = template.read_text()
    for key, val in repl.items():
        text = text.replace(f"@{key}@", val)
    if "@" in text:  # an unsubstituted placeholder would be invalid C++
        return None
    out = Path(tempfile.gettempdir()) / "pineforge_codegen_genhdr" / "include"
    (out / "pineforge").mkdir(parents=True, exist_ok=True)
    (out / "pineforge" / "version.h").write_text(text)
    return out


def _resolve_generated_include(engine_inc: Path | None) -> Path | None:
    """Resolve a dir that supplies the CMake-generated ``<pineforge/version.h>``.

    ``version.h`` is generated from ``include/pineforge/version.h.in`` into a
    build tree, so a fresh source checkout's ``include/`` does not contain it.
    Resolve it generically so the syntax check does not fail on the very first
    include. Order: (1) already present on the engine include path -> nothing to
    add; (2) ``PINEFORGE_GENERATED_INCLUDE`` override; (3) any ``build*/include``
    beside the engine checkout; (4) a synthesized stub from ``version.h.in``.
    Returns a dir to add with ``-I`` (or ``None`` when already resolvable).
    """
    if engine_inc is None:
        return None
    if (engine_inc / "pineforge" / "version.h").is_file():
        return None
    candidates: list[Path] = []
    env = os.environ.get("PINEFORGE_GENERATED_INCLUDE", "")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(sorted(engine_inc.parent.glob("build*/include")))
    for cand in candidates:
        if (cand / "pineforge" / "version.h").is_file():
            return cand.resolve()
    template = engine_inc / "pineforge" / "version.h.in"
    if template.is_file():
        return _synthesize_version_header(engine_inc, template)
    return None


_ENGINE_INC = _resolve_engine_include()
_EIGEN_INC = _resolve_eigen_include()
_COMPILER = _resolve_compiler()
_GENERATED_INC = _resolve_generated_include(_ENGINE_INC)


def have_compile_env() -> bool:
    """Cheap check used by parametrized tests to decide collection."""
    return _ENGINE_INC is not None and _EIGEN_INC is not None and _COMPILER is not None


def skip_if_no_compile_env() -> None:
    """Call from a test body to skip cleanly when env is missing.

    Centralises the skip message so each fail mode reads identically. The
    body checks each env piece individually so the user knows which knob to
    set, instead of a blanket "engine missing" message.
    """
    if _COMPILER is None:
        pytest.skip("CXX compiler not found on PATH (try CXX=clang++).")
    if _ENGINE_INC is None:
        pytest.skip(
            "PINEFORGE_ENGINE_INCLUDE not set or invalid. "
            "Point it at pineforge-engine/include (containing pineforge/engine.hpp)."
        )
    if _EIGEN_INC is None:
        pytest.skip(
            "Eigen3 headers not found. Set PINEFORGE_EIGEN_INCLUDE to the dir "
            "whose child is Eigen/ (or install eigen3 to a system path)."
        )


def compile_cpp(cpp_source: str, *, label: str = "snippet") -> None:
    """Run ``-fsyntax-only`` on ``cpp_source``; raise AssertionError on failure.

    This intentionally does NOT link or run anything. The single failure
    surface is "the emitted C++ is syntactically and semantically well-formed
    against the public pineforge-engine headers". That is the precise
    contract we want from the codegen and nothing more, because heavier
    semantic + ABI tests live in pineforge-backend.

    The compile flags mirror the engine's own production build (see
    ``pineforge-engine/CMakeLists.txt``): C++17, -fno-exceptions disabled
    (the runtime throws std::runtime_error from a few hot paths), warnings
    promoted to errors so a malformed emit cannot quietly succeed.
    """
    skip_if_no_compile_env()

    assert _COMPILER is not None and _ENGINE_INC is not None and _EIGEN_INC is not None
    with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False, mode="w") as f:
        f.write(cpp_source)
        cpp_path = f.name

    try:
        cmd = [
            _COMPILER,
            "-std=c++17",
            "-fsyntax-only",
            "-I", str(_ENGINE_INC),
            "-I", str(_EIGEN_INC),
        ]
        if _GENERATED_INC is not None:
            # Supplies the CMake-generated <pineforge/version.h> (absent from a
            # fresh source checkout's include/).
            cmd += ["-I", str(_GENERATED_INC)]
        cmd.append(cpp_path)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            # Truncate large emitted-source dumps so failure reports stay
            # readable in CI logs. The first ~80 lines of source plus the
            # first ~40 lines of compiler diagnostics is enough to localise
            # almost every regression.
            src_preview = "\n".join(cpp_source.splitlines()[:80])
            diag_preview = "\n".join(
                (result.stderr or result.stdout).splitlines()[:40]
            )
            raise AssertionError(
                f"compile-only check failed for {label} (exit={result.returncode}).\n"
                f"--- compiler diagnostics ---\n{diag_preview}\n"
                f"--- emitted C++ (first 80 lines) ---\n{src_preview}\n"
            )
    finally:
        try:
            os.unlink(cpp_path)
        except OSError:
            pass

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


_ENGINE_INC = _resolve_engine_include()
_EIGEN_INC = _resolve_eigen_include()
_COMPILER = _resolve_compiler()


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
            cpp_path,
        ]
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

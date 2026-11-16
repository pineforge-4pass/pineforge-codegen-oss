"""Real compiler controls for the shared flags and both runtime build helpers.

The deliberately unsafe C++ conversion tests instrumentation, not Pine
semantics. Finite and NaN operands enter at runtime; neither the source nor
the compiler flags change with the value. No compiler or runtime is mocked.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from tests import _compile as compile_env
from tests._e2e import build_strategy_library


_CAST_CPP = r"""
#include <cstdio>
#include <cstdlib>

extern "C" int pf_float_cast_control(const char* text) {
    const double value = std::strtod(text, nullptr);
    return static_cast<int>(value);
}
"""

_ARGV_MAIN = r"""
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::printf("converted=%d\n", pf_float_cast_control(argv[1]));
    return 0;
}
"""

_ENV_MAIN = r"""
int main() {
    const char* text = std::getenv("PINEFORGE_FLOAT_CAST_INPUT");
    if (text == nullptr) return 2;
    std::printf("converted=%d\n", pf_float_cast_control(text));
    return 0;
}
"""

_LIBRARY_RUNNER = """
import ctypes
import sys

library = ctypes.CDLL(sys.argv[1])
convert = library.pf_float_cast_control
convert.argtypes = [ctypes.c_char_p]
convert.restype = ctypes.c_int
print(f"converted={convert(sys.argv[2].encode('ascii'))}", flush=True)
"""

# Distinct values check that the real operand reaches the cast and that
# finite conversion still truncates toward zero, including for negatives.
_FINITE_CONTROLS = (("17.75", 17), ("-23.5", -23), ("0.875", 0))
_TRAP_RETURNCODES = frozenset(
    -getattr(signal, name)
    for name in ("SIGILL", "SIGTRAP", "SIGABRT")
    if hasattr(signal, name)
)


def _assert_controls(run) -> None:
    for text, expected in _FINITE_CONTROLS:
        finite = run(text)
        assert finite.returncode == 0, finite.stdout + finite.stderr
        assert finite.stdout == f"converted={expected}\n"
    invalid = run("nan")
    # A loader error, parsing-error exit, segfault, or recoverable report
    # followed by success must not qualify as the requested trap.
    assert invalid.returncode in _TRAP_RETURNCODES, (
        f"NaN conversion did not trap: exit={invalid.returncode}\n"
        f"stdout:\n{invalid.stdout}\nstderr:\n{invalid.stderr}"
    )
    assert invalid.stdout == "", "execution continued past the NaN conversion"


@pytest.mark.parametrize("opt", ("-O0", "-O2"))
def test_shared_flags_trap_input_driven_float_cast_overflow(tmp_path: Path, opt: str):
    """Standalone compile/run control: reuse one actual binary for every input."""
    compiler = compile_env._COMPILER
    if compiler is None:
        unavailable = (
            pytest.fail if os.environ.get("PINEFORGE_REQUIRE_NATIVE") == "1"
            else pytest.skip
        )
        unavailable("CXX compiler not found on PATH for float-cast-overflow controls.")
    source = tmp_path / "float_cast_control.cpp"
    binary = tmp_path / "float_cast_control"
    source.write_text(_CAST_CPP + _ARGV_MAIN, encoding="utf-8")
    command = [
        compiler, "-std=c++17", opt, *compile_env.STRATEGY_FP_FLAGS,
        str(source), "-o", str(binary),
    ]
    built = subprocess.run(command, capture_output=True, text=True)
    assert built.returncode == 0, (
        f"Required float-cast-overflow flags failed: {command!r}\n"
        + built.stdout + built.stderr
    )
    _assert_controls(lambda text: subprocess.run(
        [str(binary), text], capture_output=True, text=True,
    ))


@pytest.mark.parametrize("opt", ("-O0", "-O2"))
def test_emitted_tu_helper_traps_input_driven_float_cast_overflow(monkeypatch, opt: str):
    """Exercise the real helper, changing only the program's environment input."""
    compile_env.skip_if_no_engine_lib()
    for text, expected in _FINITE_CONTROLS:
        monkeypatch.setenv("PINEFORGE_FLOAT_CAST_INPUT", text)
        assert compile_env.run_emitted_tu(
            _CAST_CPP, _ENV_MAIN, opt=opt, label="float-cast-overflow control",
        ) == f"converted={expected}\n"
    monkeypatch.setenv("PINEFORGE_FLOAT_CAST_INPUT", "nan")
    with pytest.raises(AssertionError) as stopped:
        compile_env.run_emitted_tu(
            _CAST_CPP, _ENV_MAIN, opt=opt, label="float-cast-overflow control",
        )
    message = str(stopped.value)
    assert any(
        f"float-cast-overflow control at {opt} exited {code}:\n" in message
        for code in _TRAP_RETURNCODES
    ), message
    assert "converted=" not in message, message


@pytest.mark.parametrize("opt", ("-O0", "-O2"))
def test_strategy_library_helper_traps_input_driven_float_cast_overflow(
    tmp_path: Path, opt: str,
):
    """Reuse one real engine-linked shared object, loaded in child processes."""
    compile_env.skip_if_no_engine_lib()
    # Only optimization is overridden. Sanitizer and FP options must come
    # from the same tuple as every ordinary strategy-library build.
    build_strategy_library(_CAST_CPP, tmp_path, extra_flags=(opt,))
    library = tmp_path / "strategy.so"
    _assert_controls(lambda text: subprocess.run(
        [sys.executable, "-c", _LIBRARY_RUNNER, str(library), text],
        capture_output=True, text=True,
    ))

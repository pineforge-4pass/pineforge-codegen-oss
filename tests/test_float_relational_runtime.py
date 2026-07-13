"""Executable coverage for Pine-compatible numeric comparisons.

The structural tests in :mod:`tests.test_na_relational_lowering` prove that
all comparison emission sites route through the shared lowering.  This module
adds the missing semantic layer: transpile a strategy, link the emitted C++
against a local ``libpineforge``, execute one bar per matrix row, and inspect
the generated strategy's public result members.

The test is deliberately opt-in, like the compile-only suite.  It runs when a
valid ``PINEFORGE_ENGINE_INCLUDE`` (or auto-detected engine checkout), Eigen,
compiler, and built engine library are available; otherwise it skips cleanly.
Set ``PINEFORGE_ENGINE_LIB`` to select a nonstandard engine library path.

No assertion depends on the comparator's C++ spelling.  The matrix pins the
TradingView oracle contract: exact equality or an inclusive, fixed absolute
band of ``1e-10`` defines equality. Ordered operators exclude that band, and the
band is magnitude-independent (not relative tolerance and not grid rounding).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


_PINE_MATRIX = """//@version=6
strategy("Float relational runtime matrix")

float lhs = close
float rhs = open
int eq_out = lhs == rhs ? 1 : 0
int ne_out = lhs != rhs ? 1 : 0
int lt_out = lhs < rhs ? 1 : 0
int gt_out = lhs > rhs ? 1 : 0
int le_out = lhs <= rhs ? 1 : 0
int ge_out = lhs >= rhs ? 1 : 0

int mix_eq_out = time == volume ? 1 : 0
int mix_ne_out = time != volume ? 1 : 0
int mix_lt_out = time < volume ? 1 : 0
int mix_gt_out = time > volume ? 1 : 0
int mix_le_out = time <= volume ? 1 : 0
int mix_ge_out = time >= volume ? 1 : 0

bump() =>
    var float calls = 0.0
    calls += 1.0
    calls

int once_out = bump() == 1.0 ? 1 : 0
"""


_CPP_DRIVER = r"""
#include <iostream>
#include <limits>

int main() {
    struct Row {
        const char* name;
        double lhs;
        double rhs;
        int64_t mixed_i;
        double mixed_f;
    };

    const Row rows[] = {
        {"exact_equal", 1.0, 1.0, 42, 42.0},
        {"band_equal_above", 1.00000000009, 1.0, 42, 42.0},
        {"band_equal_below", 0.99999999991, 1.0, 42, 42.0},
        {"exact_band_inclusive", 1.0e-10, 0.0, 42, 42.0},
        {"outside_band_above", 1.00000000011, 1.0, 43, 42.0},
        {"outside_band_below", 0.99999999989, 1.0, 41, 42.0},
        // This pair straddles a 10-decimal rounding boundary but remains
        // equal because its exact difference is only about 2e-12.
        {"grid_straddle_equal", 1.000000000051, 1.000000000049, 42, 42.0},
        {"finn_boundary_equal", 2.499999999999483, 2.5, 42, 42.0},
        {"magnitude_1000_equal", 1000.00000000009, 1000.0, 42, 42.0},
        {"magnitude_1000_ordered", 1000.0000002, 1000.0, 43, 42.0},
        {"tiny_equal_above", 0.00000000009, 0.0, 42, 42.0},
        {"tiny_equal_below", -0.00000000009, 0.0, 42, 42.0},
        {"tiny_ordered_above", 0.00000000011, 0.0, 43, 42.0},
        {"tiny_ordered_below", -0.00000000011, 0.0, 41, 42.0},
        {"nan", std::numeric_limits<double>::quiet_NaN(), 1.0, 42, 42.0},
        {"positive_infinity_equal", std::numeric_limits<double>::infinity(),
             std::numeric_limits<double>::infinity(), 42, 42.0},
        {"positive_infinity_above", std::numeric_limits<double>::infinity(),
             1.0, 43, 42.0},
        {"negative_infinity_below", -std::numeric_limits<double>::infinity(),
             1.0, 41, 42.0},
        {"huge_equal", 1.0e300, 1.0e300, 42, 42.0},
        {"huge_ordered", 1.0e300, 9.0e299, 43, 42.0},
        {"huge_adjacent_ordered",
             std::nextafter(1.0e300, std::numeric_limits<double>::infinity()),
             1.0e300, 43, 42.0},
        // A naive abs(lhs-rhs) overflows here; ordering must remain defined.
        {"huge_opposite_signs", 1.0e308, -1.0e308, 41, 42.0},
        // Pine promotes the int64 operand to double for a mixed comparison.
        {"mixed_int64_promotes", 1.0, 1.0, 9007199254740993LL,
             9007199254740992.0},
        {"mixed_int64_na", 1.0, 1.0,
             std::numeric_limits<int64_t>::min(), 0.0},
    };

    for (const Row& row : rows) {
        GeneratedStrategy strategy;
        // The strategy reads lhs/rhs from close/open and the mixed pair from
        // time/volume.  No orders are placed, so arbitrary finite high/low are
        // sufficient even for the non-finite operand rows.
        Bar bar{row.rhs, 1.0, 1.0, row.lhs, row.mixed_f, row.mixed_i};
        strategy.run(&bar, 1);
        std::cout
            << row.name
            << '\t' << strategy.eq_out
            << '\t' << strategy.ne_out
            << '\t' << strategy.lt_out
            << '\t' << strategy.gt_out
            << '\t' << strategy.le_out
            << '\t' << strategy.ge_out
            << '\t' << strategy.mix_eq_out
            << '\t' << strategy.mix_ne_out
            << '\t' << strategy.mix_lt_out
            << '\t' << strategy.mix_gt_out
            << '\t' << strategy.mix_le_out
            << '\t' << strategy.mix_ge_out
            << '\t' << strategy.once_out
            << '\n';
    }
    return 0;
}
"""


# Columns emitted by the generated executable for one comparator result.
_EQ = (1, 0, 0, 0, 1, 1)
_LT = (0, 1, 1, 0, 1, 0)
_GT = (0, 1, 0, 1, 0, 1)
_NA = (0, 0, 0, 0, 0, 0)


_EXPECTED: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    "exact_equal": (_EQ, _EQ),
    "band_equal_above": (_EQ, _EQ),
    "band_equal_below": (_EQ, _EQ),
    "exact_band_inclusive": (_EQ, _EQ),
    "outside_band_above": (_GT, _GT),
    "outside_band_below": (_LT, _LT),
    "grid_straddle_equal": (_EQ, _EQ),
    "finn_boundary_equal": (_EQ, _EQ),
    "magnitude_1000_equal": (_EQ, _EQ),
    "magnitude_1000_ordered": (_GT, _GT),
    "tiny_equal_above": (_EQ, _EQ),
    "tiny_equal_below": (_EQ, _EQ),
    "tiny_ordered_above": (_GT, _GT),
    "tiny_ordered_below": (_LT, _LT),
    "nan": (_NA, _EQ),
    "positive_infinity_equal": (_EQ, _EQ),
    "positive_infinity_above": (_GT, _GT),
    "negative_infinity_below": (_LT, _LT),
    "huge_equal": (_EQ, _EQ),
    "huge_ordered": (_GT, _GT),
    "huge_adjacent_ordered": (_GT, _GT),
    "huge_opposite_signs": (_GT, _LT),
    "mixed_int64_promotes": (_EQ, _EQ),
    "mixed_int64_na": (_EQ, _NA),
}


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None

    engine_inc = compile_env._ENGINE_INC
    if engine_inc is None:
        return None
    engine_root = engine_inc.parent
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(engine_root.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(cpp_source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip(
            "built libpineforge not found; set PINEFORGE_ENGINE_LIB or build "
            "the engine beside PINEFORGE_ENGINE_INCLUDE"
        )

    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None

    with tempfile.TemporaryDirectory(prefix="pineforge-float-rel-") as tmp:
        cpp_path = Path(tmp) / "matrix.cpp"
        exe_path = Path(tmp) / "matrix"
        cpp_path.write_text(cpp_source)

        cmd = [
            compiler,
            "-std=c++17",
            "-O0",
            "-I", str(engine_inc),
            "-I", str(eigen_inc),
        ]
        if compile_env._GENERATED_INC is not None:
            cmd += ["-I", str(compile_env._GENERATED_INC)]
        cmd += [str(cpp_path), str(engine_lib), "-pthread", "-o", str(exe_path)]

        built = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(
                "generated comparator runtime probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:80])
            )

        ran = subprocess.run(
            [str(exe_path)], capture_output=True, text=True, timeout=30
        )
        if ran.returncode != 0:
            raise AssertionError(
                f"generated comparator runtime probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def test_generated_float_relational_runtime_matrix():
    cpp = transpile(_PINE_MATRIX)
    stdout = _compile_and_run(cpp + _CPP_DRIVER)
    observed: dict[str, tuple[int, ...]] = {}
    for line in stdout.splitlines():
        name, *raw_values = line.split("\t")
        observed[name] = tuple(int(value) for value in raw_values)

    assert set(observed) == set(_EXPECTED)
    for name, (expected_float, expected_mixed) in _EXPECTED.items():
        values = observed[name]
        assert len(values) == 13, (name, values)
        assert values[:6] == expected_float, (name, values[:6], expected_float)
        assert values[6:12] == expected_mixed, (
            name,
            values[6:12],
            expected_mixed,
        )
        assert values[12] == 1, (name, "stateful operand evaluated more than once")

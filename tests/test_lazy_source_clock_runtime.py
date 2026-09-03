"""Executable synthetic-bars coverage for the lazy-edge TA clocks.

Links the generated strategy against a built ``libpineforge`` (set
``PINEFORGE_ENGINE_LIB``; otherwise the sibling engine's ``build*/lib`` is
used) and drives it with synthetic bars -- no feed, no probe. The expected
numbers are the TradingView models pinned 2026-09-03 with ``lab tv`` on
NYSE:F 1D (see tests/test_lazy_source_clock.py and
tests/test_lazy_edge_ta_every_bar.py): change/mom/roc read the call's own
held ``source[length]`` (na before the first execution); cum/barssince only
see the samples of bars where the call executes.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


# Cadence-4 lazy edges over closes 100, 102, ..., 122 (12 bars): the calls
# execute on bars 1, 5 and 9.
#   hold-last source (change/mom/roc, length 3):
#     bar 1: no execution at or before bar -2           -> na (naCount 1)
#     bar 5: held source as of bar 2 = close[1] = 102   -> change 8, roc 7.843...
#     bar 9: held source as of bar 6 = close[5] = 110   -> change 8, roc 7.2727...
#     (every-bar would read close[b-3]: change 6, roc 5.769...; a ring of
#      executions would still be na at bar 9)
#   per-execution (cum, barssince(close < 105)):
#     cum at bar 9 = 102 + 110 + 118 = 330             (every-bar: 1090)
#     barssince at bar 9 = 2 executions since bar 1    (every-bar: 7 bars)
_PINE = """//@version=6
strategy("lazy edge clocks")
var float lastRoc = na
var float lastChange = na
var int momHits = 0
var int naCount = 0
var float lastCum = na
var float lastBarsSince = na
gate = bar_index % 4 == 1
v = gate ? ta.roc(close, 3) : na
c = gate ? ta.change(close, 3) : na
m = gate and ta.mom(close, 3) > 0
cu = gate ? ta.cum(close) : na
bs = gate ? ta.barssince(close < 105) + 0.0 : na
if gate and na(v)
    naCount += 1
if not na(v)
    lastRoc := v
if not na(c)
    lastChange := c
if m
    momHits += 1
if not na(cu)
    lastCum := cu
if not na(bs)
    lastBarsSince := bs
"""

_DRIVER = r"""
#include <iomanip>
#include <iostream>

static Bar make_bar(double close, int64_t timestamp) {
    return Bar{close, close, close, close, 1.0, timestamp};
}

static void report(const GeneratedStrategy& s) {
    std::cout << std::setprecision(10) << s.lastRoc << ' ' << s.lastChange << ' '
              << s.momHits << ' ' << s.naCount << ' ' << s.lastCum << ' '
              << s.lastBarsSince;
}

int main() {
    Bar bars[12];
    for (int i = 0; i < 12; ++i) {
        bars[i] = make_bar(100.0 + 2.0 * i, 1000 + static_cast<int64_t>(i) * 60000);
    }
    GeneratedStrategy precalc;
    precalc.run(bars, 12);                // static mode (_use_precalc path)
    report(precalc);
    std::cout << " | ";
    GeneratedStrategy dynamic;
    dynamic.run(bars, 12, "1", "1");      // dynamic mode (inline path)
    report(dynamic);
    std::cout << '\n';
    return 0;
}
"""

_EXPECTED_ONE = "7.272727273 8 2 1 330 2"


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    engine_inc = compile_env._ENGINE_INC
    if engine_inc is None:
        return None
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(engine_inc.parent.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip("set PINEFORGE_ENGINE_LIB to a built PineForge engine library")
    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None
    with tempfile.TemporaryDirectory(prefix="pineforge-lazy-source-clock-") as tmp:
        cpp = Path(tmp) / "clock.cpp"
        exe = Path(tmp) / "clock"
        cpp.write_text(source)
        command = [compiler, "-std=c++17", "-O0", "-I", str(engine_inc), "-I", str(eigen_inc)]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(cpp), str(engine_lib), "-pthread", "-o", str(exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if built.returncode != 0:
            raise AssertionError(built.stderr or built.stdout)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        if ran.returncode != 0:
            raise AssertionError(ran.stderr or ran.stdout)
        return ran.stdout.strip()


def test_hold_last_and_per_execution_clocks_in_both_run_modes():
    assert _compile_and_run(transpile(_PINE) + _DRIVER) == " | ".join([_EXPECTED_ONE] * 2)


def test_first_execution_is_na_and_stream_lifecycle_resets_the_clock():
    """The first execution has no held history (TV call 1: no entry); a second
    stream lifecycle on the same handle starts from that same na state."""
    pine = """//@version=6
strategy("lazy source clock stream")
gate = bar_index == 2 or bar_index == 5
signal = gate and ta.roc(close, 3) > 0
"""
    driver = r"""
#include <iostream>

static Bar make_bar(double close, int64_t timestamp) {
    return Bar{close, close, close, close, 1.0, timestamp};
}

int main() {
    Bar bars[4];
    for (int i = 0; i < 4; ++i) {
        bars[i] = make_bar(100.0 + i * 20.0, 1000 + i * 60000);
    }
    GeneratedStrategy reused;
    // History bars 0..3: the first execution (bar 2) has no held source -> na.
    const bool began_first = reused.stream_begin(bars, 4, "1", "1");
    const int after_history = reused.signal ? 1 : 0;
    // Ticks open bars 4, 5, 6; each closes the previous bar. Bar 5 executes
    // with the held source as of bar 2 (close 140) against close 200.
    reused.stream_push_tick(TradeTick{241123, 1, 180.0, 1.0});
    reused.stream_push_tick(TradeTick{301123, 2, 200.0, 1.0});
    reused.stream_push_tick(TradeTick{361123, 3, 300.0, 1.0});
    const int after_bar5 = reused.signal ? 1 : 0;
    const bool ended_first = reused.stream_end();

    const bool began_second = reused.stream_begin(bars, 4, "1", "1");
    const int second_after_history = reused.signal ? 1 : 0;
    const bool ended_second = reused.stream_end();

    std::cout << began_first << after_history << after_bar5 << ended_first
              << began_second << second_after_history << ended_second << '\n';
    return 0;
}
"""
    assert _compile_and_run(transpile(pine) + driver) == "1011101"

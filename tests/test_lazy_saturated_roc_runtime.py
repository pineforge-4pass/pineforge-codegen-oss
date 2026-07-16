"""Executable lifecycle coverage for generated lazy saturated ROC clocks."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


_PINE = """//@version=6
strategy("lazy ROC reuse")
gate = bar_index == 5 or (bar_index == 0 and close > 90)
signal = gate and ta.roc(close, 3) > 0
"""


_DRIVER = r"""
#include <iostream>

static Bar make_bar(double close, int64_t timestamp) {
    return Bar{close, close, close, close, 1.0, timestamp};
}

int main() {
    Bar first[6];
    Bar second[6];
    for (int i = 0; i < 6; ++i) {
        first[i] = make_bar(100.0 + i * 20.0, 1000 + i * 60000);
        second[i] = make_bar(10.0 + i * 8.0, 2000 + i * 60000);
    }

    GeneratedStrategy reused;
    reused.run(first, 6);
    const int first_signal = reused.signal ? 1 : 0;
    reused.run(second, 6);
    const int reused_signal = reused.signal ? 1 : 0;

    GeneratedStrategy fresh;
    fresh.run(second, 6);
    const int fresh_signal = fresh.signal ? 1 : 0;

    std::cout << first_signal << ' ' << reused_signal << ' '
              << fresh_signal << ' '
              << reused._pf_lazy_saturated_roc3_clock_1.bar_base_bar << ' '
              << fresh._pf_lazy_saturated_roc3_clock_1.bar_base_bar << '\n';
    return 0;
}
"""


_STREAM_DRIVER = r"""
#include <iostream>

static Bar make_bar(double close, int64_t timestamp) {
    return Bar{close, close, close, close, 1.0, timestamp};
}

int main() {
    Bar first[4];
    Bar second[4];
    for (int i = 0; i < 4; ++i) {
        first[i] = make_bar(100.0 + i * 20.0, 1000 + i * 60000);
        second[i] = make_bar(10.0 + i * 8.0, 1000000 + i * 60000);
    }

    GeneratedStrategy reused;
    const bool began_first = reused.stream_begin(first, 4, "1", "1");
    const bool tick_first_4 = reused.stream_push_tick(
        TradeTick{241123, 1, 180.0, 1.0});
    const bool tick_first_5 = reused.stream_push_tick(
        TradeTick{301123, 2, 200.0, 1.0});
    const bool tick_first_6 = reused.stream_push_tick(
        TradeTick{361123, 3, 210.0, 1.0});
    const int first_signal = reused.signal ? 1 : 0;
    const int first_base = reused._pf_lazy_saturated_roc3_clock_1.bar_base_bar;
    const bool ended_first = reused.stream_end();

    const bool began_second = reused.stream_begin(second, 4, "1", "1");
    const bool tick_second_4 = reused.stream_push_tick(
        TradeTick{1240123, 1, 42.0, 1.0});
    const bool tick_second_5 = reused.stream_push_tick(
        TradeTick{1300123, 2, 50.0, 1.0});
    const bool tick_second_6 = reused.stream_push_tick(
        TradeTick{1360123, 3, 58.0, 1.0});
    const int reused_signal = reused.signal ? 1 : 0;
    const int reused_base = reused._pf_lazy_saturated_roc3_clock_1.bar_base_bar;
    const bool ended_second = reused.stream_end();

    GeneratedStrategy fresh;
    const bool began_fresh = fresh.stream_begin(second, 4, "1", "1");
    const bool tick_fresh_4 = fresh.stream_push_tick(
        TradeTick{1240123, 1, 42.0, 1.0});
    const bool tick_fresh_5 = fresh.stream_push_tick(
        TradeTick{1300123, 2, 50.0, 1.0});
    const bool tick_fresh_6 = fresh.stream_push_tick(
        TradeTick{1360123, 3, 58.0, 1.0});
    const int fresh_signal = fresh.signal ? 1 : 0;
    const int fresh_base = fresh._pf_lazy_saturated_roc3_clock_1.bar_base_bar;
    const bool ended_fresh = fresh.stream_end();

    std::cout << began_first << tick_first_4 << tick_first_5 << tick_first_6
              << ended_first << began_second << tick_second_4 << tick_second_5
              << tick_second_6 << ended_second << began_fresh << tick_fresh_4
              << tick_fresh_5 << tick_fresh_6 << ended_fresh << ' '
              << first_signal << ' ' << reused_signal << ' ' << fresh_signal
              << ' ' << first_base << ' ' << reused_base << ' ' << fresh_base
              << '\n';
    return 0;
}
"""


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

    with tempfile.TemporaryDirectory(prefix="pineforge-lazy-roc-reuse-") as tmp:
        cpp = Path(tmp) / "reuse.cpp"
        exe = Path(tmp) / "reuse"
        cpp.write_text(source)
        command = [
            compiler,
            "-std=c++17",
            "-O0",
            "-I",
            str(engine_inc),
            "-I",
            str(eigen_inc),
        ]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(cpp), str(engine_lib), "-pthread", "-o", str(exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(built.stderr or built.stdout)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        if ran.returncode != 0:
            raise AssertionError(ran.stderr or ran.stdout)
        return ran.stdout.strip()


def test_same_handle_second_batch_run_matches_fresh_clock_state():
    # Without the bar-zero lifecycle reset, the second run sees working_bar=5
    # from run one, reuses its bar-zero base, and produces `1 0 1 0 -1`.
    assert _compile_and_run(transpile(_PINE) + _DRIVER) == "1 1 1 -1 -1"


def test_same_handle_second_stream_warmup_matches_fresh_clock_state():
    # stream_begin enters BacktestEngine::run directly, so this specifically
    # proves the reset is in generated on_bar rather than only in a wrapper.
    assert _compile_and_run(transpile(_PINE) + _STREAM_DRIVER) == (
        "111111111111111 1 1 1 0 -1 -1"
    )


def test_clock_numeric_first_short_gaps_saturation_same_bar_and_na_zero():
    driver = r'''
#include <cmath>
#include <iomanip>
#include <iostream>

int main() {
    _PFLazySaturatedROC3Clock clock;
    const double first = clock.evaluate(100.0, 80.0, 0);
    const double gap1 = clock.evaluate(110.0, 90.0, 1);
    const double gap2 = clock.evaluate(120.0, 100.0, 3);
    const double gap3 = clock.evaluate(150.0, 140.0, 6);
    const double same = clock.evaluate(180.0, 170.0, 6);
    const double next = clock.evaluate(198.0, 190.0, 9);

    _PFLazySaturatedROC3Clock na_clock;
    const double na_source = na_clock.evaluate(na<double>(), 100.0, 0);
    _PFLazySaturatedROC3Clock zero_clock;
    (void)zero_clock.evaluate(0.0, 100.0, 0);
    const double zero_previous = zero_clock.evaluate(10.0, 9.0, 3);

    std::cout << std::setprecision(17)
              << first << ' ' << gap1 << ' ' << gap2 << ' '
              << gap3 << ' ' << same << ' ' << next << ' '
              << std::isnan(na_source) << ' '
              << std::isnan(zero_previous) << '\n';
    return 0;
}
'''
    raw = _compile_and_run(transpile(_PINE) + driver).split()
    assert len(raw) == 8
    observed = tuple(float(value) for value in raw[:6])
    assert observed == pytest.approx(
        (25.0, 100.0 * 20.0 / 90.0, 20.0, 25.0, 50.0, 10.0),
        rel=0.0,
        abs=1e-12,
    )
    assert raw[6:] == ["1", "1"]


def test_shadowed_close_preserves_existing_eager_full_bar_route():
    pine = '''//@version=6
strategy("shadowed close eager")
float close = open
gate = bar_index == 0 or bar_index == 5
signal = gate and ta.roc(close, 3) > 0
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[6] = {
        Bar{100, 100, 100, 100, 1, 1000},
        Bar{20, 20, 20, 20, 1, 2000},
        Bar{10, 10, 10, 10, 1, 3000},
        Bar{20, 20, 20, 20, 1, 4000},
        Bar{30, 30, 30, 30, 1, 5000},
        Bar{50, 50, 50, 50, 1, 6000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 6);
    std::cout << (strategy.signal ? 1 : 0) << '\n';
    return 0;
}
'''
    assert _compile_and_run(transpile(pine) + driver) == "1"


@pytest.mark.parametrize("cap", [1, 2, 3, 4])
def test_eager_fallback_has_four_slots_independent_of_max_bars_back(cap: int):
    pine = f'''//@version=6
strategy("lazy ROC cap", max_bars_back={cap})
signal = bar_index == 3 and ta.roc(close, 3) > 0
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[4] = {
        Bar{10, 10, 10, 10, 1, 1000},
        Bar{20, 20, 20, 20, 1, 2000},
        Bar{30, 30, 30, 30, 1, 3000},
        Bar{40, 40, 40, 40, 1, 4000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 4);
    std::cout << (strategy.signal ? 1 : 0) << '\n';
    return 0;
}
'''
    assert _compile_and_run(transpile(pine) + driver) == "1"

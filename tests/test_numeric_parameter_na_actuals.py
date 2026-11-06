"""Keep missing integer values missing across an untyped numeric call.

The test runs two choices where the integer input is either missing or finite.
It checks the emitted strategy's selected values and the following calculation.
A source check also keeps the conversion at the call site.
"""

from __future__ import annotations

import json
import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests._numeric_expected import integer_to_double


_NA_FIRST = """//@version=6
strategy("numeric selection")
c = close > open
int k = c ? 1 : na
choose_value(value, bool choose) => choose ? na : value
actual = int(volume > 0 ? volume : na)
selected = choose_value(actual, not c)
y = selected + 1
plot(selected)
plot(y)
if bar_index == 5
    strategy.entry("L", strategy.long)
"""

_NA_SECOND = """//@version=6
strategy("numeric selection")
c = close > open
int k = c ? 1 : na
choose_value(value, bool choose) => choose ? value : na
actual = int(volume > 0 ? volume : na)
selected = choose_value(actual, c)
y = selected + 1
plot(selected)
plot(y)
if bar_index == 5
    strategy.entry("L", strategy.long)
"""

_SOURCES = (
    pytest.param(_NA_FIRST, id="na-first"),
    pytest.param(_NA_SECOND, id="na-second"),
)

_SCHEDULES = {
    "missing_first": (
        (True, 0), (False, 0), (True, 5), (False, 7),
        (False, 0), (True, 13), (True, 0), (False, 19),
    ),
    "finite_first": (
        (False, 23), (False, 0), (True, 31), (True, 0),
        (True, 47), (False, 59), (False, 0), (True, 0),
    ),
}

_DRIVER = r"""
#include <array>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <type_traits>
#include <vector>

static std::vector<pineforge::Bar> bars_for(
    double base, long long origin, std::array<bool, 8> rising,
    std::array<int, 8> volumes
) {
    std::vector<pineforge::Bar> bars(8);
    for (unsigned index = 0; index < bars.size(); ++index) {
        auto& bar = bars[index];
        bar.open = base;
        bar.high = base + 5.0;
        bar.low = base - 5.0;
        bar.close = rising[index] ? base + 3.0 : base - 3.0;
        bar.volume = volumes[index];
        bar.timestamp = origin + index * 60000;
    }
    return bars;
}

static void number(double value) {
    if (std::isnan(value)) std::cout << "null";
    else std::cout << value;
}

int main() {
    std::cout << std::setprecision(17);
    std::vector<std::vector<pineforge::Bar>> schedules = {
        bars_for(
            100.0, 1704067200000,
            {true, false, true, false, false, true, true, false},
            {0, 0, 5, 7, 0, 13, 0, 19}
        ),
        bars_for(
            150.0, 1743465600000,
            {false, false, true, true, true, false, false, true},
            {23, 0, 31, 0, 47, 59, 0, 0}
        ),
    };
    for (auto& bars : schedules) {
        for (unsigned count = 1; count <= bars.size(); ++count) {
            GeneratedStrategy strategy;
            static_assert(std::is_same_v<decltype(strategy.c), bool>);
            static_assert(std::is_same_v<decltype(strategy.actual), int>);
            static_assert(std::is_same_v<decltype(strategy.selected), double>);
            static_assert(std::is_same_v<decltype(strategy.y), double>);
            strategy.run(bars.data(), count, "1", "1");
            if (!strategy.last_error().empty()) {
                std::cerr << strategy.last_error() << "\n";
                return 14;
            }
            std::cout << "{\"prefix\":" << count
                      << ",\"timestamp\":" << bars[count - 1].timestamp
                      << ",\"c\":" << (strategy.c ? "true" : "false")
                      << ",\"actual\":" << strategy.actual
                      << ",\"actual_na\":"
                      << (is_na(strategy.actual) ? "true" : "false")
                      << ",\"selected\":";
            number(strategy.selected);
            std::cout << ",\"y\":";
            number(strategy.y);
            std::cout << "}\n";
        }
    }
    return 0;
}
"""


def _inputs(schedule: str, opt: str):
    origin = 1704067200000 if schedule == "missing_first" else 1743465600000
    base = 100.0 if schedule == "missing_first" else 150.0
    bars = [
        {
            "open": base, "high": base + 5.0, "low": base - 5.0,
            "close": base + 3.0 if up else base - 3.0,
            "volume": float(volume), "timestamp": origin + index * 60000,
        }
        for index, (up, volume) in enumerate(_SCHEDULES[schedule])
    ]
    return {
        "schedule": schedule, "opt": opt, "chart_timeframe": "1",
        "bars": bars, "prefixes": list(range(1, len(bars) + 1)),
    }


def _reference(payload):
    """Pine arithmetic over input bars, independent of emitted C++."""
    expected = []
    for prefix, bar in enumerate(payload["bars"], 1):
        selected_parameter = bar["close"] > bar["open"]
        actual = int(bar["volume"]) if bar["volume"] > 0 else None
        selected = (
            float(actual)
            if selected_parameter and actual is not None
            else None
        )
        expected.append({
            "prefix": prefix,
            "timestamp": bar["timestamp"],
            "c": selected_parameter,
            "actual": -(2**31) if actual is None else actual,
            "actual_na": actual is None,
            "selected": selected,
            "y": None if selected is None else selected + 1,
        })
    return expected


def _rows_by_schedule(stdout: str):
    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    width = len(next(iter(_SCHEDULES.values())))
    assert len(rows) == width * len(_SCHEDULES)
    return {
        schedule: rows[index * width:(index + 1) * width]
        for index, schedule in enumerate(_SCHEDULES)
    }


@pytest.mark.parametrize("source", _SOURCES)
@pytest.mark.parametrize("opt", ("-O0", "-O2"), ids=("O0", "O2"))
def test_untyped_parameter_na_actuals_keep_adapter(source, opt):
    observed = _rows_by_schedule(run_emitted_tu(transpile(source), _DRIVER, opt=opt))
    patterns = {}
    for schedule in _SCHEDULES:
        expected = _reference(_inputs(schedule, opt))
        actual_rows = observed[schedule]
        assert len(actual_rows) == len(expected)
        for actual, wanted in zip(actual_rows, expected):
            for key in ("prefix", "timestamp", "c", "actual", "actual_na"):
                assert actual[key] == wanted[key]
            for key in ("selected", "y"):
                if wanted[key] is None:
                    assert actual[key] is None
                else:
                    assert math.isfinite(actual[key])
                    assert actual[key] == wanted[key]
        patterns[schedule] = [
            (row["selected"], row["y"]) for row in actual_rows
        ]

    assert any(
        first != second
        for first, second in zip(
            patterns["missing_first"], patterns["finite_first"],
        )
    )


@pytest.mark.parametrize("source", _SOURCES)
def test_untyped_parameter_na_actuals_keep_call_site_conversion(source):
    """Value agreement is established only by test 1."""
    cpp = transpile(source)
    adapter_call = f"choose_value({integer_to_double('actual')}, "
    bare_call = "choose_value(actual, "
    assert cpp.count(adapter_call) == 1
    assert cpp.count(bare_call) == 0

"""Pine ``ta.iii`` and ``ta.wvad`` are na on a bar whose high equals its low.

Both variables divide by the bar's range:

    ta.iii  = (2 * close - high - low) / (high - low) * volume
    ta.wvad = (close - open) / (high - low) * volume

On a bar with no range TradingView answers na for both. A bar with a range
whose numerator is zero (the close in the middle of the range, the close at
the open) is a genuine 0. The engine's ``ta::III`` and ``ta::WVAD`` answer 0
on a bar with no range, so the Pine variables are built from the engine's
Pine-layer objects ``PineIii`` and ``PineWvad``
(``codegen/ta.py::_TA_PINE_LAYER_CLASSES``), which have a header of their own.

Invented bars; bars 1 and 4 are flat, bar 2 has a range and both numerators
zero:

    bar        0     1     2     3     4
    open       10    11    12    9     7
    high       12    11    14    10    7
    low        8     11    10    8     7
    close      11    11    12    8     7
    volume     8     5     6     4     3
    ta.iii     4     na    0     -4    na
    ta.wvad    2     na    0     -2    na

Built from ``ta::III`` and ``ta::WVAD`` the same script answers 0 on bars 1
and 4.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import cpp_code_only
from tests._compile import compile_cpp, run_emitted_tu


_TYPES = {
    "iii": "pineforge::source::PineIii",
    "wvad": "pineforge::source::PineWvad",
}
_KERNEL = {"iii": "ta::III", "wvad": "ta::WVAD"}
# The objects' header, then the guard for the feature macro that header
# defines beside them.
_HEADER_AND_GUARD = (
    "#include <pineforge/source/pine_ta_flat_range.hpp>\n"
    "#ifndef PINEFORGE_HAS_PINE_FLAT_RANGE\n"
    '#error "ta.iii and ta.wvad require the paired pineforge-engine flat-range'
    ' adapters (PINEFORGE_HAS_PINE_FLAT_RANGE)"\n'
    "#endif\n"
)
_PRECALCULATE = "void precalculate(const Bar* bars, int n)"


def _members(cpp, name):
    """Every declared object of ``ta.<name>``: its C++ type and its name."""
    return re.findall(
        rf"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_{name}_\w+);$", cpp, re.M
    )


_FLAT_SCRIPT = (
    '//@version=6\nstrategy("intensity and accumulation on a bar without a range")\n'
    "iiiValue = ta.iii\n"
    "wvadValue = ta.wvad\n"
    "plot(iiiValue + wvadValue)\n"
)

# The host runs the script once per bar; each row is read after that bar and
# printed as "route bar iii wvad", na spelled out so that it is never a zero.
# The bare run overload offers the precalculated route, which replays both
# objects over the whole feed before the first bar, and the overload with a
# timeframe does not; one handle runs once.
_FLAT_DRIVER = r"""
#include <cstddef>
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
// A member the emitter keeps as a series is read at its current bar.
template <class T> static double flat_bar_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}
struct RecordingStrategy : GeneratedStrategy {
    std::vector<std::vector<double>> rows;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        rows.push_back({flat_bar_value(iiiValue), flat_bar_value(wvadValue)});
    }
};
int main() {
    // Finite, well-formed one-minute bars: open, high, low, close, volume.
    static const double fields[][5] = {
        {10, 12, 8, 11, 8},
        {11, 11, 11, 11, 5},
        {12, 14, 10, 12, 6},
        {9, 10, 8, 8, 4},
        {7, 7, 7, 7, 3},
    };
    constexpr int count = 5;
    Bar bars[count];
    for (int i = 0; i < count; ++i)
        bars[i] = {fields[i][0], fields[i][1], fields[i][2], fields[i][3], fields[i][4],
                   1743465600000LL + i * 60000LL};
    for (bool cached : {true, false}) {
        RecordingStrategy strategy;
        if (cached) strategy.run(bars, count);
        else strategy.run(bars, count, "1", "1");
        const auto failure = pineforge::run_failure_value_of(strategy);
        const auto state = strategy.native_state();
        if (failure.code != pineforge::RunFailureCode::none ||
            strategy.last_run_status() != 0 || !strategy.last_error().empty() ||
            state.kind != pineforge::NativeLifecycleKind::Completed)
            throw std::runtime_error(
                std::string("hosted run failed: ") +
                pineforge::run_failure_code_of(strategy) + ": " + strategy.last_error());
        if (strategy._use_precalc != cached)
            throw std::runtime_error("the run did not take the requested route");
        if (strategy.rows.size() != static_cast<std::size_t>(count))
            throw std::runtime_error("the script did not run once per bar");
        for (int i = 0; i < count; ++i) {
            std::printf("%s %d", cached ? "cached" : "live", i);
            for (double value : strategy.rows[i]) {
                if (is_na(value)) std::printf(" na");
                else std::printf(" %.17g", value);
            }
            std::printf("\n");
        }
    }
}
"""

# bar, ta.iii, ta.wvad. The engine classes answer "1 0 0" and "4 0 0" on the
# two bars without a range.
_FLAT_ROWS = [
    "0 4 2",
    "1 na na",
    "2 0 0",
    "3 -4 -2",
    "4 na na",
]


def test_iii_and_wvad_are_na_on_a_bar_without_a_range():
    cpp = transpile(_FLAT_SCRIPT)
    # Precondition of the cached rows, on any emitter: the replay that runs
    # before the first bar steps both objects.
    members = {
        name for family in _TYPES for _kind, name in _members(cpp, family)
    }
    replayed = set(re.findall(r"\b_precalc_(_ta_(?:iii|wvad)_\w+)\[i\] =", cpp))
    assert len(members) >= 2 and replayed == members, (members, replayed)
    out = run_emitted_tu(cpp, _FLAT_DRIVER, opt="-O0", label="iii_wvad_flat_bar")
    assert out.splitlines() == [
        f"{route} {row}" for route in ("cached", "live") for row in _FLAT_ROWS
    ]


# One read of each kind the object is built for: a top-level read (replayed
# before the first bar by a run that may precalculate), a read in a block that
# only some bars reach, and a request.security copy.
_CONSUMERS = (
    '//@version=6\nstrategy("flat-range consumers")\n'
    "chartValue = ta.NAME\n"
    "float blockValue = na\n"
    "if bar_index % 2 == 0\n"
    "    blockValue := ta.NAME\n"
    'requestedValue = request.security(syminfo.tickerid, "60", ta.NAME)\n'
    "plot(chartValue + blockValue + requestedValue)\n"
)


def _consumers(name):
    return transpile(_CONSUMERS.replace("NAME", name))


@pytest.mark.parametrize("name", sorted(_TYPES))
def test_every_object_of_the_variable_is_the_pine_layer_type(name):
    cpp = _consumers(name)
    assert _HEADER_AND_GUARD in cpp
    members = _members(cpp, name)
    chart = [member for _kind, member in members if not member.startswith("_sec")]
    requested = [member for _kind, member in members if member.startswith("_sec")]
    assert len(chart) >= 2, members
    assert requested, members
    assert {kind for kind, _member in members} == {_TYPES[name]}, members
    # No declaration or reset names the engine class any more.
    assert _KERNEL[name] not in cpp_code_only(cpp)


@pytest.mark.parametrize("name", sorted(_TYPES))
def test_consumers_compile_against_the_paired_engine(name):
    # The engine must ship the objects' header and define them in it: an
    # engine without them fails here, at the include or at its guard.
    compile_cpp(_consumers(name), label=f"flat_range_{name}")


@pytest.mark.parametrize("name", sorted(_TYPES))
def test_precalculation_builds_the_pine_layer_type(name):
    cpp = transpile(
        '//@version=6\nstrategy("precalculated flat-range read")\n'
        f"value = ta.{name}\nplot(value)\n"
    )
    precalc = cpp.split(_PRECALCULATE, 1)[1].split("\n    }", 1)[0]
    # Built for the replay and rebuilt for the run.
    assert precalc.count(f"= {_TYPES[name]}(") == 2
    # The objects never stop a run, so the replay keeps no handler for a
    # stop: that belongs to the checked adapters.
    assert "try {" not in precalc
    assert "_pf_precalc_error" not in precalc

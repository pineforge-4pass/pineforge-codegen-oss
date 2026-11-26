"""Pine ``ta.vwma`` averages ``source * volume`` and ``volume`` separately.

TradingView answers ``ta.vwma(x, n)`` with ``ta.sma(x * volume, n) /
ta.sma(volume, n)``, and each of the two averages skips an na input on its
own. A bar whose source is na therefore still enters the volume average while
the product average holds its last mean, and the two windows cover different
bars until ``n`` further bars with a source have arrived. The engine's
``ta::VWMA`` keeps complete (source, volume) pairs instead: it answers na on
such a bar and weighs the following bars by the pairs' volumes. The Pine call
is built from the engine's Pine-layer object ``PineVwma``
(``codegen/ta.py::_TA_PINE_LAYER_CLASSES``).

Invented bars, length 2, the source hidden on bar 2:

    bar                0     1      2      3      4     5
    close              10    20     30     40     50    60
    volume             1     3      5      3      2     2
    source             10    20     na     40     50    60
    source * volume    10    60     na     120    100   120
    sma(product, 2)    na    35     35     90     110   110
    sma(volume, 2)     na    2      4      4      2.5   2
    ta.vwma            na    17.5   8.75   22.5   44    55

Bar 2 divides the held product mean 35 by the new volume mean (3 + 5) / 2.
Bar 3 divides (60 + 120) / 2 by (5 + 3) / 2: the volume window holds bar 2 and
the product window does not. From bar 4 on both windows cover the same two
bars again. Built from ``ta::VWMA`` the same script answers na on bar 2 and
30, that is (60 + 120) / (3 + 3), on bar 3.
"""

import re

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import cpp_code_only
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = "#include <pineforge/source/pine_ta_length.hpp>"
_TYPE = "pineforge::source::PineVwma"
_KERNEL = "ta::VWMA"
_PRECALCULATE = "void precalculate(const Bar* bars, int n)"


def _members(cpp):
    """Every declared ``ta.vwma`` object: its C++ type and its name."""
    return re.findall(r"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_vwma_\w+);$", cpp, re.M)


# ``vwmaValue`` reads a script variable, so the call is computed on the bar in
# every run. ``inlineValue`` spells the same source in the call: a run that
# may precalculate fills that call for the whole feed before the first bar.
# ``definitionValue`` is the Pine definition written out with ``ta.sma``.
_GAP_SCRIPT = (
    '//@version=6\nstrategy("volume-weighted average around a missing source bar")\n'
    "gappedSource = bar_index == 2 ? float(na) : close\n"
    "vwmaValue = ta.vwma(gappedSource, 2)\n"
    "inlineValue = ta.vwma(close == 30 ? na : close, 2)\n"
    "definitionValue = ta.sma(gappedSource * volume, 2) / ta.sma(volume, 2)\n"
    "plot(vwmaValue + inlineValue + definitionValue)\n"
)

# The host runs the script once per bar; each row is read after that bar and
# printed as "route bar source vwma inline definition", na spelled out so that
# it is never a zero. The bare run overload offers the precalculated route and
# the overload with a timeframe does not; one handle runs once.
_GAP_DRIVER = r"""
#include <cstddef>
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
// A member the emitter keeps as a series is read at its current bar.
template <class T> static double vwma_gap_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}
struct RecordingStrategy : GeneratedStrategy {
    std::vector<std::vector<double>> rows;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        rows.push_back({vwma_gap_value(gappedSource), vwma_gap_value(vwmaValue),
                        vwma_gap_value(inlineValue), vwma_gap_value(definitionValue)});
    }
};
int main() {
    const double closes[] = {10, 20, 30, 40, 50, 60};
    const double volumes[] = {1, 3, 5, 3, 2, 2};
    constexpr int count = 6;
    Bar bars[count];
    for (int i = 0; i < count; ++i) {
        const double price = closes[i];
        // Finite, well-formed one-minute bars: open, high, low, close, volume, time.
        bars[i] = {price, price + 1, price - 1, price, volumes[i],
                   1743465600000LL + i * 60000LL};
    }
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

# bar, source, ta.vwma of the variable, ta.vwma of the inline source, the
# definition. The engine class answers "2 na na na 8.75" and "3 40 30 30 22.5"
# on the bar without a source and on the one after it.
_GAP_ROWS = [
    "0 10 na na na",
    "1 20 17.5 17.5 17.5",
    "2 na 8.75 8.75 8.75",
    "3 40 22.5 22.5 22.5",
    "4 50 44 44 44",
    "5 60 55 55 55",
]


def test_vwma_across_a_missing_source_bar_follows_the_pine_definition():
    cpp = transpile(_GAP_SCRIPT)
    # Precondition of the cached rows, on any emitter: the inline call, and
    # only that one of the two, is filled before the first bar.
    members = {name for _kind, name in _members(cpp)}
    cached = set(re.findall(r"\b_precalc_(_ta_vwma_\w+)\[i\] =", cpp))
    assert len(cached) == 1 and cached < members, (members, cached)
    out = run_emitted_tu(cpp, _GAP_DRIVER, opt="-O0", label="vwma_missing_source")
    assert out.splitlines() == [
        f"{route} {row}" for route in ("cached", "live") for row in _GAP_ROWS
    ]


# One call of each kind the object is built for: a literal length, keyword
# arguments, an input length (rebuilt per run, precalculated), a simple
# length (built on the first call) and a request.security copy.
_CONSUMERS = (
    '//@version=6\nstrategy("volume-weighted average consumers")\n'
    'lengthInput = input.int(5, "Length")\n'
    'simple int simpleLength = syminfo.type == "crypto" ? 3 : 4\n'
    "gappedSource = bar_index % 11 == 4 ? float(na) : close\n"
    "fixedValue = ta.vwma(gappedSource, 2)\n"
    "namedValue = ta.vwma(source = gappedSource, length = 3)\n"
    "inputValue = ta.vwma(close, lengthInput)\n"
    "simpleValue = ta.vwma(close, simpleLength)\n"
    'requestedValue = request.security(syminfo.tickerid, "60", ta.vwma(close, 2))\n'
    "plot(fixedValue + namedValue + inputValue + simpleValue + requestedValue)\n"
)


def test_every_object_of_the_call_is_the_pine_layer_type():
    cpp = transpile(_CONSUMERS)
    assert _HEADER in cpp
    members = _members(cpp)
    chart = [name for _kind, name in members if not name.startswith("_sec")]
    requested = [name for _kind, name in members if name.startswith("_sec")]
    assert len(chart) >= 4, members
    assert requested, members
    assert {kind for kind, _name in members} == {
        _TYPE, f"pineforge::source::FirstCallBound<{_TYPE}>",
    }, members
    # The input length's per-run reset and the simple length's first-call
    # factory build the declared type.
    assert re.search(rf"= {re.escape(_TYPE)}\(.*get_input_int", cpp)
    assert f"return {_TYPE}(" in cpp
    # No declaration, reset or factory names the engine class any more.
    assert _KERNEL not in cpp_code_only(cpp)


def test_consumers_compile_against_the_paired_engine():
    # The engine header must define the object: an engine without it fails
    # here, at the member declarations.
    compile_cpp(transpile(_CONSUMERS), label="volume_weighted_consumers")


def test_precalculation_builds_the_pine_layer_type_without_a_length_handler():
    cpp = transpile(
        '//@version=6\nstrategy("precalculated volume-weighted average")\n'
        "value = ta.vwma(close, 3)\nplot(value)\n"
    )
    precalc = cpp.split(_PRECALCULATE, 1)[1].split("\n    }", 1)[0]
    # Built for the replay and rebuilt for the run.
    assert precalc.count(f"= {_TYPE}(") == 2
    # The object accepts every length, so the replay keeps no length-stop
    # handler: that belongs to the checked adapters.
    assert "try {" not in precalc
    assert "_pf_precalc_error" not in precalc

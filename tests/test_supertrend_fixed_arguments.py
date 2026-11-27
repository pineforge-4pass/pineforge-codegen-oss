"""A ``ta.supertrend`` whose factor and ATR period are fixed for the run stops
on its first execution when one of them is not positive.

The factor and the period of the calls below are literals or inputs, so the
generated object keeps its constructor, its first-use reset and the
precalculated route. TradingView stops such a call with "Invalid value of the
'<argument>' argument (<value>) in the 'supertrend' function. It must be > 0.",
the error a factor that is not a constant already raises here (that lowering
is pinned in test_codegen_ta_dynamic_length.py and
test_issue_426_supertrend_period.py).

These are routing and execution-contract rows. Every price is invented, and no
row is a value recorded on TradingView: a valid pair is compared with the
engine's own ``ta::Supertrend``, built in the driver from the same two values.
"""

import json
import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_ADAPTER = "pineforge::source::PineFixedSupertrend"
# The type of a call whose factor is not a constant (another lowering).
_NOT_CONSTANT = "pineforge::source::PineSupertrend"
# The adapter's header, then the guard for the feature macro that header
# defines beside the adapter.
_HEADER_AND_GUARD = (
    "#include <pineforge/source/pine_ta_length.hpp>\n"
    "#ifndef PINEFORGE_HAS_PINE_FIXED_SUPERTREND\n"
    '#error "ta.supertrend requires the paired pineforge-engine fixed-argument'
    ' adapter (PINEFORGE_HAS_PINE_FIXED_SUPERTREND)"\n'
    "#endif\n"
)
_PRECALCULATE = "void precalculate(const Bar* bars, int n)"
BARS = 10

_HEAD = '//@version=6\nstrategy("fixed supertrend arguments")\n'
_INPUTS = (
    'factorInput = input.float(1.5, "Factor")\n'
    'periodInput = input.int(2, "Period")\n'
)
_COUNTERS = "var int visited = 0\nvar int completed = 0\n"

# The call runs at the top level, on every bar, with both arguments inputs.
EVERY_BAR = (
    _HEAD + _INPUTS + _COUNTERS
    + "visited += 1\n"
    + "[stLine, stSide] = ta.supertrend(factorInput, periodInput)\n"
    + "completed += 1\n"
    + "plot(stLine + stSide + visited + completed)\n"
)

# The same call inside a function that the script first reaches on its third
# bar (bar_index 2).
FIRST_REACHED_LATER = (
    _HEAD + _INPUTS + _COUNTERS
    + "lateBand() =>\n"
    + "    [band, side] = ta.supertrend(factorInput, periodInput)\n"
    + "    band\n"
    + "visited += 1\n"
    + "float stLine = na\n"
    + "if bar_index >= 2\n"
    + "    stLine := lateBand()\n"
    + "completed += 1\n"
    + "plot(stLine + visited + completed)\n"
)


def _literal(arguments):
    """The top-level call with both arguments written in the script."""
    return (
        _HEAD + _COUNTERS
        + "visited += 1\n"
        + f"[stLine, stSide] = ta.supertrend({arguments})\n"
        + "completed += 1\n"
        + "plot(stLine + stSide + visited + completed)\n"
    )


def _stop_text(bar, argument, shown):
    return (
        f"Error on bar {bar}: Invalid value of the '{argument}' argument "
        f"({shown}) in the 'supertrend' function. It must be > 0."
    )


def _members(cpp):
    """Every declared ``ta.supertrend`` object: its C++ type and its name."""
    return re.findall(
        r"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_supertrend_\w+);$", cpp, re.M
    )


@pytest.mark.parametrize(
    "source,top_level,from_inputs",
    (
        pytest.param(EVERY_BAR, True, True, id="inputs"),
        pytest.param(FIRST_REACHED_LATER, False, True, id="inputs-in-function"),
        pytest.param(_literal("0.0, 2"), True, False, id="literals"),
        pytest.param(
            _literal("atrPeriod=0, factor=1.5"), True, False, id="named-literals"
        ),
    ),
)
def test_fixed_arguments_declare_the_checked_adapter(source, top_level, from_inputs):
    # Emitter shape only. What the generated code does is the hosted tests'.
    cpp = transpile(source)
    assert _HEADER_AND_GUARD in cpp
    members = _members(cpp)
    assert members, "the script declares no ta.supertrend object"
    assert {kind for kind, _name in members} == {_ADAPTER}, members
    # No declaration, reset or factory names the unchecked class.
    assert not re.search(r"\bta::Supertrend\s+_?(?:sec\d+_)?_ta_", cpp)
    assert "= ta::Supertrend(" not in cpp
    assert "return ta::Supertrend(" not in cpp
    if from_inputs:
        # The first-use reset builds the same type from the live inputs.
        assert re.search(rf"= {re.escape(_ADAPTER)}\(.*get_input_", cpp)
    if top_level:
        # The replay that fills the call's cache leaves an invalid-argument
        # stop to the bar on which the call executes.
        assert _PRECALCULATE in cpp, "the top-level call has no precalculated route"
        precalc = cpp.split(_PRECALCULATE, 1)[1].split("\n    }", 1)[0]
        assert f"_precalc_{members[0][1]}[i] =" in precalc
        assert "catch (const std::exception& _pf_precalc_error)" in precalc
        assert precalc.index(f"= {_ADAPTER}(") < precalc.index("try {")
        assert precalc.rindex(f"= {_ADAPTER}(") > precalc.index("catch (")


# The host owns the lifecycle: run() admits the bars, prepares the script
# once and dispatches every bar to on_source_bar. The bare overload offers the
# precalculated route and the overload with a timeframe does not. The host
# records a stop the script raises instead of rethrowing it, so the driver
# prints the failure record of every run; one handle runs once.
_DRIVER = r"""
#include <cstddef>
#include <iostream>
#include <pineforge/run_failure.hpp>
#include <type_traits>
#include <vector>

// Ten invented one-minute bars: open, high, low, close.
static const double fixed_st_prices[][4] = {
    {9, 12, 8, 10}, {10, 13, 9, 12}, {12, 15, 11, 14}, {14, 14, 10, 11},
    {11, 12, 7, 8}, {8, 9, 5, 6}, {6, 11, 6, 10}, {10, 16, 9, 15},
    {15, 18, 13, 17}, {17, 17, 12, 13},
};
constexpr int fixed_st_count = 10;

// A member the emitter keeps as a series is read at its current bar.
template <class T> static double fixed_st_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}

static bool fixed_st_same(double actual, double expected) {
    return is_na(actual) == is_na(expected) && (is_na(expected) || actual == expected);
}

// Observes the host's own per-bar call. A bar the script stops on records
// nothing: the stop leaves the generated callback first.
struct RecordingStrategy : GeneratedStrategy {
    std::vector<double> lines;
    std::vector<double> sides;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        RECORD_BAR
    }
};

struct FixedStCase {
    const char* name;
    const char* factor;  // the "Factor" input; null keeps the script's own
    const char* period;  // the "Period" input; null keeps the script's own
    bool valid;          // the pair is factor 1.5, period 2
};

int main() {
    static const FixedStCase cases[] = {
CASE_ROWS
    };
    Bar bars[fixed_st_count];
    for (int i = 0; i < fixed_st_count; ++i)
        bars[i] = {fixed_st_prices[i][0], fixed_st_prices[i][1], fixed_st_prices[i][2],
                   fixed_st_prices[i][3], 100.0, 1743465600000LL + i * 60000LL};
    for (const FixedStCase& item : cases) {
        for (bool cached : {true, false}) {
            RecordingStrategy strategy;
            if (item.factor) strategy.set_input("Factor", item.factor);
            if (item.period) strategy.set_input("Period", item.period);
            if (cached) strategy.run(bars, fixed_st_count);
            else strategy.run(bars, fixed_st_count, "1", "1");
            // For the valid pair: the bars whose recorded line and direction
            // are ta::Supertrend's own, and the direction changes among them.
            int equal = 0;
            int turns = 0;
            if (item.valid) {
                ta::Supertrend kernel(1.5, 2);
                for (std::size_t i = 0; i < strategy.lines.size(); ++i) {
                    const ta::SupertrendResult expected =
                        kernel.compute(bars[i].high, bars[i].low, bars[i].close);
                    if (fixed_st_same(strategy.lines[i], expected.value) &&
                        fixed_st_same(strategy.sides[i], expected.direction)) ++equal;
                    if (i > 0 && strategy.sides[i] != strategy.sides[i - 1]) ++turns;
                }
            }
            const auto state = strategy.native_state();
            std::cout << "CASE " << item.name << (cached ? " cached" : " live") << "\n"
                      << "STATUS " << strategy.last_run_status() << "\n"
                      << "NAME " << pineforge::run_failure_code_of(strategy) << "\n"
                      << "ARGS " << pineforge::run_failure_args_of(strategy) << "\n"
                      << "CALLBACK "
                      << (state.kind == pineforge::NativeLifecycleKind::Failed &&
                          state.failure.code == pineforge::NativeFailureCode::CallbackException)
                      << "\n"
                      << "VISITED " << strategy.visited << "\n"
                      << "COMPLETED " << strategy.completed << "\n"
                      << "RECORDED " << strategy.lines.size() << "\n"
                      << "KERNEL " << equal << "\n"
                      << "TURNS " << turns << "\n"
                      << "PUBLISHED " << strategy._use_precalc << "\n"
                      << "TEXT " << strategy.last_error() << "\n";
        }
    }
    return 0;
}
"""
_RECORD_LINE_AND_SIDE = (
    "lines.push_back(fixed_st_value(stLine)); sides.push_back(fixed_st_value(stSide));"
)
_KEYS = (
    "CASE", "STATUS", "NAME", "ARGS", "CALLBACK", "VISITED", "COMPLETED",
    "RECORDED", "KERNEL", "TURNS", "PUBLISHED", "TEXT",
)


def _cpp_text(value):
    return "nullptr" if value is None else f'"{value}"'


def _hosted_rows(source, cases, *, record, label):
    """Run every case on both routes; one row per run, keyed by
    ``"<case> cached"`` / ``"<case> live"``. A case is
    ``(name, Factor input, Period input, is the valid pair)``."""
    rows_cpp = "".join(
        f'        {{"{name}", {_cpp_text(factor)}, {_cpp_text(period)},'
        f' {"true" if valid else "false"}}},\n'
        for name, factor, period, valid in cases
    )
    driver = _DRIVER.replace("RECORD_BAR", record).replace("CASE_ROWS\n", rows_cpp)
    cpp = transpile(source)
    # Precondition, on any emitter: the call keeps its constructor and reset.
    # The lowering of a factor that is not a constant checks its arguments by
    # itself and is not what these rows are about.
    members = _members(cpp)
    assert members and all(kind != _NOT_CONSTANT for kind, _name in members), members
    output = run_emitted_tu(cpp, driver, opt="-O0", label=label)
    print(output, end="")
    lines = output.splitlines()
    assert len(lines) == len(_KEYS) * 2 * len(cases), output
    rows = {}
    for start in range(0, len(lines), len(_KEYS)):
        row = {}
        for expected, line in zip(_KEYS, lines[start:start + len(_KEYS)]):
            key, _separator, value = line.partition(" ")
            assert key == expected, output
            row[key] = value
        assert row["CASE"] not in rows, output
        rows[row["CASE"]] = row
    assert set(rows) == {
        f"{name} {route}" for name, _f, _p, _v in cases for route in ("cached", "live")
    }, output
    return rows


def _assert_stopped(row, *, bar, argument, shown, recorded=None):
    """The run stopped inside the script callback of ``bar``, at the call:
    the statements before the call ran on that bar, the ones after did not."""
    expected = {
        "STATUS": "1",
        "NAME": "pine_invalid_argument",
        "CALLBACK": "1",
        "VISITED": str(bar + 1),
        "COMPLETED": str(bar),
        "PUBLISHED": "0",
        "TEXT": _stop_text(bar, argument, shown),
    }
    if recorded is not None:
        expected["RECORDED"] = str(recorded)
    assert {key: row[key] for key in expected} == expected, row
    assert json.loads(row["ARGS"]) == {
        "argument": argument, "function": "supertrend", "rule": "must_be_positive",
    }, row


def _assert_completed(row):
    expected = {
        "STATUS": "0", "NAME": "", "CALLBACK": "0",
        "VISITED": str(BARS), "COMPLETED": str(BARS), "TEXT": "",
    }
    assert {key: row[key] for key in expected} == expected, row


# name, "Factor", "Period", the argument the stop names, the value it shows.
_INPUT_STOPS = (
    ("factor-zero", "0.0", "2", "factor", "0"),
    ("factor-negative", "-1.5", "2", "factor", "-1.5"),
    ("period-zero", "1.5", "0", "atrPeriod", "0"),
    ("period-negative", "1.5", "-1", "atrPeriod", "-1"),
    # Both invalid: the period is checked first.
    ("both-zero", "0.0", "0", "atrPeriod", "0"),
)
_INPUT_CASES = tuple(
    (name, factor, period, False) for name, factor, period, _a, _s in _INPUT_STOPS
) + (("valid", "1.5", "2", True),)


def test_invalid_input_arguments_stop_on_the_first_bar():
    cpp = transpile(EVERY_BAR)
    # Precondition of the cached rows: the call has a precalculated route.
    assert f"_precalc_{_members(cpp)[0][1]}[i] =" in cpp
    rows = _hosted_rows(
        EVERY_BAR, _INPUT_CASES, record=_RECORD_LINE_AND_SIDE,
        label="fixed_supertrend_inputs",
    )
    for name, _factor, _period, argument, shown in _INPUT_STOPS:
        for route in ("cached", "live"):
            _assert_stopped(
                rows[f"{name} {route}"], bar=0, argument=argument, shown=shown,
                recorded=0,
            )
    # A valid pair computes what ta::Supertrend computes, on both routes, and
    # the comparison is not of one constant: the direction turns in these bars.
    for route in ("cached", "live"):
        row = rows[f"valid {route}"]
        _assert_completed(row)
        assert row["RECORDED"] == str(BARS), row
        assert row["KERNEL"] == str(BARS), row
        assert int(row["TURNS"]) >= 1, row
        assert row["PUBLISHED"] == ("1" if route == "cached" else "0"), row


def test_invalid_input_arguments_stop_where_the_call_is_first_reached():
    # The object is built before the first bar and reset on it; the check is
    # made by the call, so the first two bars complete.
    rows = _hosted_rows(
        FIRST_REACHED_LATER, _INPUT_CASES, record="",
        label="fixed_supertrend_reached_later",
    )
    for name, _factor, _period, argument, shown in _INPUT_STOPS:
        for route in ("cached", "live"):
            _assert_stopped(
                rows[f"{name} {route}"], bar=2, argument=argument, shown=shown,
            )
    for route in ("cached", "live"):
        _assert_completed(rows[f"valid {route}"])


@pytest.mark.parametrize(
    "arguments,argument",
    (
        pytest.param("0.0, 2", "factor", id="factor-zero"),
        pytest.param("atrPeriod=0, factor=1.5", "atrPeriod", id="named-period-zero"),
    ),
)
def test_invalid_literal_arguments_stop_on_the_first_bar(arguments, argument):
    source = _literal(arguments)
    cpp = transpile(source)
    assert f"_precalc_{_members(cpp)[0][1]}[i] =" in cpp
    rows = _hosted_rows(
        source, (("literal", None, None, False),), record=_RECORD_LINE_AND_SIDE,
        label=f"fixed_supertrend_literal_{argument}",
    )
    for route in ("cached", "live"):
        _assert_stopped(
            rows[f"literal {route}"], bar=0, argument=argument, shown="0",
            recorded=0,
        )

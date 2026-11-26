"""A ``ta.*`` call whose length is a constant or an input stops on its first
execution when that length is zero.

TradingView compiles ``ta.sma(close, 0)``, and the same call with an input set
to 0, and stops the run on the bar where the call first executes: "Invalid
value of the 'length' argument (0) in the 'sma' function. It must be > 0.". It
does so for ta.rma, ta.sma, ta.rsi, ta.atr, ta.hma, ta.vwma, ta.mfi,
ta.highest, ta.lowest, ta.highestbars, ta.lowestbars and both lengths of
ta.dmi. Its text names the argument of ta.rsi ``y``, the function of ta.atr
``'atr'->'rma'`` and of ta.vwma ``'vwma'->'sma'``, and the two lengths of
ta.dmi ``DI Length`` and ``ADX Smoothing``. The classes these calls were built
from hold no such rule: with a length of zero most answer na on every bar,
ta.mfi answers 100, and ta.sma and ta.vwma read a window they do not have.

The generator declares each of these calls with the engine's checked
fixed-length type (``codegen/ta.py::_TA_PINE_LAYER_CLASSES``), which makes the
check on the call's first execution and then is the type it wraps: the kernel
class, or the Pine-layer type of ta.vwma, ta.mfi and ta.dmi.

The routing and table tests are the emitter's shape. The hosted tests run the
emitted translation unit on ten invented bars, on the precalculated route and
on the live one. No row is a value recorded on TradingView: a stop is compared
with the engine's text for the call, and the values of a valid length with the
wrapped type, built in the driver from the same length. What ta.vwma, ta.mfi
and ta.dmi answer where their Pine-layer type differs from the kernel class is
worked by hand in tests/test_ta_vwma_missing_source.py and
tests/test_pine_source_ta_types.py.
"""

import json
import re
from typing import NamedTuple

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import TA_CLASS_MAP
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.helpers import CPP_STANDARD_MACROS, cpp_code_only
from tests._compile import compile_cpp, run_emitted_tu
from tests._legacy_cpp import _EMITTER_SOURCE_SPELLINGS


_MACRO = "PINEFORGE_HAS_PINE_FIXED_LENGTH"
# The header of the checked types, then the guard for the feature macro that
# header defines beside them.
_HEADER_AND_GUARD = (
    "#include <pineforge/source/pine_ta_length.hpp>\n"
    f"#ifndef {_MACRO}\n"
    '#error "generated code requires the paired pineforge-engine fixed-length'
    f' adapters ({_MACRO})"\n'
    "#endif\n"
)
_PRECALCULATE = "void precalculate(const Bar* bars, int n)"
BARS = 10


class Family(NamedTuple):
    call: str      # the Pine call: {length} is its length, {second} ta.dmi's second
    checked: str   # the type every site with a fixed length is declared with
    wrapped: str   # the type the checked one builds after its check
    step: str      # the wrapped type's values of one bar, in the hosted driver
    argument: str  # the argument TradingView's text names for the length
    function: str  # the function part of TradingView's text, with its quotes


_FAMILIES = {
    "rma": Family(
        "ta.rma(close, {length})", "pineforge::source::PineFixedRma", "ta::RMA",
        "return {wrapped.compute(bar.close)};", "length", "'rma'"),
    "sma": Family(
        "ta.sma(close, {length})", "pineforge::source::PineFixedSma", "ta::SMA",
        "return {wrapped.compute(bar.close)};", "length", "'sma'"),
    "rsi": Family(
        "ta.rsi(close, {length})", "pineforge::source::PineFixedRsi", "ta::RSI",
        "return {wrapped.compute(bar.close)};", "y", "'rsi'"),
    "atr": Family(
        "ta.atr({length})", "pineforge::source::PineFixedAtr", "ta::ATR",
        "return {wrapped.compute(bar.high, bar.low, bar.close)};",
        "length", "'atr'->'rma'"),
    "hma": Family(
        "ta.hma(close, {length})", "pineforge::source::PineFixedHma", "ta::HMA",
        "return {wrapped.compute(bar.close)};", "length", "'hma'"),
    "vwma": Family(
        "ta.vwma(close, {length})", "pineforge::source::PineFixedVwma",
        "pineforge::source::PineVwma",
        "return {wrapped.compute(bar.close, bar.volume)};",
        "length", "'vwma'->'sma'"),
    "mfi": Family(
        "ta.mfi(close, {length})", "pineforge::source::PineFixedMfi",
        "pineforge::source::PineMfi",
        "return {wrapped.compute(bar.close, bar.volume)};", "length", "'mfi'"),
    "highest": Family(
        "ta.highest(high, {length})", "pineforge::source::PineFixedHighest",
        "ta::Highest", "return {wrapped.compute(bar.high)};", "length", "'highest'"),
    "lowest": Family(
        "ta.lowest(low, {length})", "pineforge::source::PineFixedLowest",
        "ta::Lowest", "return {wrapped.compute(bar.low)};", "length", "'lowest'"),
    "highestbars": Family(
        "ta.highestbars(high, {length})", "pineforge::source::PineFixedHighestBars",
        "ta::HighestBars", "return {wrapped.compute(bar.high)};",
        "length", "'highestbars'"),
    "lowestbars": Family(
        "ta.lowestbars(low, {length})", "pineforge::source::PineFixedLowestBars",
        "ta::LowestBars", "return {wrapped.compute(bar.low)};",
        "length", "'lowestbars'"),
    # Two lengths. The argument column is not used: each length has a name of
    # its own in ``_STOPS``.
    "dmi": Family(
        "ta.dmi({length}, {second})", "pineforge::source::PineFixedDmi",
        "pineforge::source::PineDmiReference",
        "const ta::DMIResult step = wrapped.compute(bar.high, bar.low, bar.close);"
        " return {step.diplus, step.diminus, step.adx};",
        "DI Length", "'dmi'"),
}
# The script variables the hosted driver reads after each bar.
_OBSERVED = {"dmi": ("fxPlus", "fxMinus", "fxValue")}
# The valid lengths: the "Length" input's default and, for ta.dmi, the
# "Second length" input's. The driver builds the wrapped type from them.
_VALID = {"dmi": ("3", "2")}


class Stop(NamedTuple):
    case: str            # the row's name
    length: str          # the length, as a literal or as the "Length" input
    second: str | None   # ta.dmi's second length, likewise
    argument: str        # the argument TradingView's text names
    relation: str        # how the text words the bound
    coded: str           # the argument of the coded failure: the Pine name


def _stops(family):
    """The zero-length rows of one call."""
    if family != "dmi":
        return (Stop("zero", "0", None, _FAMILIES[family].argument, ">", "length"),)
    return (
        Stop("di-zero", "0", "2", "DI Length", ">", "diLength"),
        Stop("adx-zero", "3", "0", "ADX Smoothing", "greater than", "adxSmoothing"),
        # Both zero: diLength is checked first.
        Stop("both-zero", "0", "0", "DI Length", ">", "diLength"),
    )


_HEAD = '//@version=6\nstrategy("fixed length checks")\n'
_COUNTERS = "var int visited = 0\nvar int completed = 0\n"


def _inputs(family):
    lines = 'lengthInput = input.int(3, "Length")\n'
    if family == "dmi":
        lines += 'secondInput = input.int(2, "Second length")\n'
    return lines


def _assignment(family, name, length, second):
    call = _FAMILIES[family].call.format(length=length, second=second)
    if family == "dmi":
        return f"[{name}Plus, {name}Minus, {name}Value] = {call}\n"
    return f"{name}Value = {call}\n"


def _hosted_script(family, length, second, *, inputs=""):
    """The call at the top level, on every bar, between two counters."""
    observed = _OBSERVED.get(family, ("fxValue",))
    return (
        _HEAD + inputs + _COUNTERS
        + "visited += 1\n"
        + _assignment(family, "fx", length, second)
        + "completed += 1\n"
        + f"plot({' + '.join(observed)} + visited + completed)\n"
    )


def _routing_source(family):
    """One site of each kind the type is spelled for: a constant length, an
    input length (rebuilt per run), a simple length (built on the first call)
    and a request.security copy."""
    requested = _FAMILIES[family].call.format(length="5", second="5")
    if family == "dmi":
        # The tuple form; the requested copy is read through a helper.
        request = (
            f'higher() => request.security(syminfo.tickerid, "60", {requested})\n'
            "[requestedPlus, requestedMinus, requestedValue] = higher()\n"
        )
    else:
        request = (
            f'requestedValue = request.security(syminfo.tickerid, "60", {requested})\n'
        )
    return (
        '//@version=6\nstrategy("fixed length routing")\n'
        'lengthInput = input.int(5, "Length")\n'
        'simple int simpleLength = syminfo.type == "crypto" ? 3 : 4\n'
        + _assignment(family, "fixed", "5", "5")
        + _assignment(family, "input", "lengthInput", "5")
        + _assignment(family, "simple", "simpleLength", "5")
        + request
        + "plot(fixedValue + inputValue + simpleValue + requestedValue)\n"
    )


def _members(cpp, family):
    """Every declared object of ``ta.<family>``: its C++ type and its name."""
    return re.findall(
        rf"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_{family}_\w+);$", cpp, re.M
    )


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_every_fixed_site_is_declared_with_the_checked_type(family):
    # Emitter shape only. What the generated code does is the hosted tests'.
    spec = _FAMILIES[family]
    cpp = transpile(_routing_source(family))
    members = _members(cpp, family)
    chart = [name for _kind, name in members if not name.startswith("_sec")]
    requested = [name for _kind, name in members if name.startswith("_sec")]
    assert len(chart) >= 3, members
    assert requested, members
    assert {kind for kind, _name in members} == {
        spec.checked, f"pineforge::source::FirstCallBound<{spec.checked}>",
    }, members
    # The input length's per-run reset and the simple length's first-call
    # factory build the declared type.
    assert re.search(rf"= {re.escape(spec.checked)}\(.*get_input_int", cpp)
    assert f"return {spec.checked}(" in cpp
    # No declaration, reset or factory is left on a type that does not check:
    # the kernel class of the call, or the Pine-layer type the checked one
    # wraps. A tuple result type (ta::DMIResult) is not a site's type.
    code = cpp_code_only(cpp)
    for unchecked in {TA_CLASS_MAP[family], spec.wrapped}:
        assert not re.search(rf"(?<![\w:]){re.escape(unchecked)}\b", code), unchecked
    # The types' header is included once, before the math header, with the
    # guard of their feature macro right behind it.
    assert cpp.count(_HEADER_AND_GUARD) == 1
    assert cpp.index(_HEADER_AND_GUARD) < cpp.index("#include <pineforge/math.hpp>\n")
    # The replay that fills a call's cache leaves an invalid length to the
    # bar on which the call executes.
    assert _PRECALCULATE in cpp, "the top-level calls have no precalculated route"
    precalc = cpp.split(_PRECALCULATE, 1)[1].split("\n    }", 1)[0]
    assert any(f"_precalc_{name}[i] =" in precalc for name in chart), chart
    assert "catch (const std::exception& _pf_precalc_error)" in precalc
    assert precalc.index(f"= {spec.checked}(") < precalc.index("try {")
    assert precalc.rindex(f"= {spec.checked}(") > precalc.index("catch (")


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_every_fixed_site_compiles_against_the_paired_engine(family):
    # The engine header must declare the type with the constructor and the
    # members generated code uses, as a member, in a first-call factory and
    # in a request.security copy: an engine without it fails here, at the
    # guard.
    compile_cpp(transpile(_routing_source(family)), label=f"fixed_length_{family}")


def test_the_twelve_calls_select_their_checked_types_in_the_class_table():
    table = CodeGen._TA_PINE_LAYER_CLASSES
    assert len(_FAMILIES) == 12
    for family, spec in _FAMILIES.items():
        # One row per call: the checked type, the length header, the one
        # feature macro, and the mark that keeps the replay's handler.
        assert table.get(TA_CLASS_MAP[family]) == (
            spec.checked, CodeGen._TA_LENGTH_HEADER, _MACRO, True,
        ), family
        # A historical comparison must know the spelling as the emitter's.
        assert spec.checked in _EMITTER_SOURCE_SPELLINGS, family
    # The macro is required by name after the include, and a script name with
    # its spelling is escaped.
    assert CodeGen._TA_PINE_LAYER_GUARDS.get(_MACRO) == tuple(
        _HEADER_AND_GUARD.splitlines()[1:]
    )
    assert _MACRO in CPP_STANDARD_MACROS


# The host owns the lifecycle: run() admits the bars, prepares the script
# once and dispatches every bar to on_source_bar. The bare overload offers the
# precalculated route and the overload with a timeframe does not. The host
# records a stop the script raises instead of rethrowing it, so the driver
# prints the failure record of every run; one handle runs once.
_DRIVER = r"""
#include <cstddef>
#include <iostream>
#include <pineforge/run_failure.hpp>
#include <pineforge/source/pine_ta_length.hpp>
#include <type_traits>
#include <vector>

// Ten invented one-minute bars: open, high, low, close, volume.
static const double fixed_len_bars[][5] = {
    {9, 12, 8, 10, 100}, {10, 13, 9, 12, 140}, {12, 15, 11, 14, 90},
    {14, 14, 10, 11, 210}, {11, 12, 7, 8, 160}, {8, 9, 5, 6, 120},
    {6, 11, 6, 10, 180}, {10, 16, 9, 15, 75}, {15, 18, 13, 17, 130},
    {17, 17, 12, 13, 95},
};
constexpr int fixed_len_count = 10;

// A script value as a double: an integer na (ta.highestbars and
// ta.lowestbars answer an int) reads as na, and a member the emitter keeps
// as a series is read at its current bar.
template <class T> static double fixed_len_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) {
        return is_na(value) ? na<double>() : static_cast<double>(value);
    } else {
        const auto current = value[0];
        return fixed_len_value(current);
    }
}

static bool fixed_len_same(double actual, double expected) {
    return is_na(actual) == is_na(expected) && (is_na(expected) || actual == expected);
}

// The values of one bar from the type the checked one wraps.
[[maybe_unused]] static std::vector<double> fixed_len_expected(WRAPPED_TYPE& wrapped,
                                                               const Bar& bar) {
    WRAPPED_STEP
}

// Observes the host's own per-bar call. A bar the script stops on records
// nothing: the stop leaves the generated callback first.
struct RecordingStrategy : GeneratedStrategy {
    std::vector<std::vector<double>> rows;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        rows.push_back({OBSERVED_VALUES});
    }
};

struct FixedLenCase {
    const char* name;
    const char* length;  // the "Length" input; null keeps the script's own
    const char* second;  // the "Second length" input; null keeps the script's own
    bool valid;          // the wrapped type is built from WRAPPED_ARGS and compared
};

int main() {
    static const FixedLenCase cases[] = {
CASE_ROWS
    };
    Bar bars[fixed_len_count];
    for (int i = 0; i < fixed_len_count; ++i)
        bars[i] = {fixed_len_bars[i][0], fixed_len_bars[i][1], fixed_len_bars[i][2],
                   fixed_len_bars[i][3], fixed_len_bars[i][4],
                   1743465600000LL + i * 60000LL};
    for (const FixedLenCase& item : cases) {
        for (bool cached : {true, false}) {
            RecordingStrategy strategy;
            if (item.length) strategy.set_input("Length", item.length);
            if (item.second) strategy.set_input("Second length", item.second);
            if (cached) strategy.run(bars, fixed_len_count);
            else strategy.run(bars, fixed_len_count, "1", "1");
            // For valid lengths: the bars whose recorded values are the
            // wrapped type's own, and the bars on which it has a value.
            int equal = 0;
            int present = 0;
            if (item.valid) {
                WRAPPED_TYPE wrapped(WRAPPED_ARGS);
                for (std::size_t i = 0; i < strategy.rows.size(); ++i) {
                    const std::vector<double> expected =
                        fixed_len_expected(wrapped, bars[i]);
                    bool same = strategy.rows[i].size() == expected.size();
                    for (std::size_t k = 0; same && k < expected.size(); ++k)
                        same = fixed_len_same(strategy.rows[i][k], expected[k]);
                    if (same) ++equal;
                    if (!is_na(expected.back())) ++present;
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
                      << "RECORDED " << strategy.rows.size() << "\n"
                      << "KERNEL " << equal << "\n"
                      << "PRESENT " << present << "\n"
                      << "PUBLISHED " << strategy._use_precalc << "\n"
                      << "TEXT " << strategy.last_error() << "\n";
        }
    }
    return 0;
}
"""
_KEYS = (
    "CASE", "STATUS", "NAME", "ARGS", "CALLBACK", "VISITED", "COMPLETED",
    "RECORDED", "KERNEL", "PRESENT", "PUBLISHED", "TEXT",
)


def _cpp_text(value):
    return "nullptr" if value is None else f'"{value}"'


def _hosted_rows(family, source, cases, *, label):
    """Run every case on both routes; one row per run, keyed by
    ``"<case> cached"`` / ``"<case> live"``. A case is
    ``(name, "Length" input, "Second length" input, are the lengths valid)``."""
    spec = _FAMILIES[family]
    rows_cpp = "".join(
        f'        {{"{name}", {_cpp_text(length)}, {_cpp_text(second)},'
        f' {"true" if valid else "false"}}},\n'
        for name, length, second, valid in cases
    )
    observed = _OBSERVED.get(family, ("fxValue",))
    driver = (
        _DRIVER
        .replace("OBSERVED_VALUES",
                 ", ".join(f"fixed_len_value({name})" for name in observed))
        .replace("CASE_ROWS\n", rows_cpp)
        .replace("WRAPPED_TYPE", spec.wrapped)
        .replace("WRAPPED_ARGS", ", ".join(_VALID.get(family, ("3",))))
        .replace("WRAPPED_STEP", spec.step)
    )
    cpp = transpile(source)
    # Precondition of the cached rows, on any emitter: the script has one
    # call of the function, and the replay that runs before the first bar
    # steps it.
    members = _members(cpp, family)
    assert len(members) == 1, members
    assert f"_precalc_{members[0][1]}[i] =" in cpp
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
        f"{name} {route}" for name, _l, _s, _v in cases for route in ("cached", "live")
    }, output
    return rows


def _assert_stopped(row, family, stop):
    """The run stopped inside the script callback of bar 0, at the call: the
    statement before the call ran, the one after it did not, and no bar was
    recorded. The text is the engine's for the call; the coded failure names
    the function and the argument as the script's call spells them."""
    expected = {
        "STATUS": "1",
        "NAME": "pine_invalid_argument",
        "CALLBACK": "1",
        "VISITED": "1",
        "COMPLETED": "0",
        "RECORDED": "0",
        "PUBLISHED": "0",
        "TEXT": (
            f"Error on bar 0: Invalid value of the '{stop.argument}' argument (0)"
            f" in the {_FAMILIES[family].function} function."
            f" It must be {stop.relation} 0."
        ),
    }
    assert {key: row[key] for key in expected} == expected, row
    assert json.loads(row["ARGS"]) == {
        "argument": stop.coded, "function": family, "rule": "must_be_positive",
    }, row


def _assert_completed(row):
    expected = {
        "STATUS": "0", "NAME": "", "CALLBACK": "0",
        "VISITED": str(BARS), "COMPLETED": str(BARS), "TEXT": "",
    }
    assert {key: row[key] for key in expected} == expected, row


# A constant length of zero, one row per length of each call.
_CONSTANT_STOPS = tuple(
    pytest.param(family, stop, id=f"{family}-{stop.case}")
    for family in sorted(_FAMILIES)
    for stop in _stops(family)
    if stop.case != "both-zero"
)


@pytest.mark.parametrize("family,stop", _CONSTANT_STOPS)
def test_a_constant_length_of_zero_stops_on_the_first_bar(family, stop):
    source = _hosted_script(family, stop.length, stop.second)
    rows = _hosted_rows(
        family, source, (("literal", None, None, False),),
        label=f"fixed_length_constant_{family}_{stop.case.replace('-', '_')}",
    )
    for route in ("cached", "live"):
        _assert_stopped(rows[f"literal {route}"], family, stop)


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_an_input_length_of_zero_stops_on_the_first_bar(family):
    source = _hosted_script(
        family, "lengthInput", "secondInput", inputs=_inputs(family)
    )
    stops = _stops(family)
    valid_length, *valid_second = _VALID.get(family, ("3",))
    cases = tuple(
        (stop.case, stop.length, stop.second, False) for stop in stops
    ) + (("valid", valid_length, valid_second[0] if valid_second else None, True),)
    rows = _hosted_rows(family, source, cases, label=f"fixed_length_input_{family}")
    for stop in stops:
        for route in ("cached", "live"):
            _assert_stopped(rows[f"{stop.case} {route}"], family, stop)
    # The control: with valid lengths the same script computes on every bar
    # what the wrapped type computes, on both routes, and the comparison is
    # not of na alone.
    for route in ("cached", "live"):
        row = rows[f"valid {route}"]
        _assert_completed(row)
        assert row["RECORDED"] == str(BARS), row
        assert row["KERNEL"] == str(BARS), row
        assert int(row["PRESENT"]) >= 3, row
        assert row["PUBLISHED"] == ("1" if route == "cached" else "0"), row

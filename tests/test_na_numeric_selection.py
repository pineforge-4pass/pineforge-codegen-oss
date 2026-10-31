"""Nullable numeric selections preserve NaN through their consumers."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


def _source(body: str) -> str:
    return (
        '//@version=6\nstrategy("numeric selection")\n'
        "c = close > open\n"
        "int k = c ? 1 : na\n"
        + body
        + '\nif bar_index == 5\n    strategy.entry("L", strategy.long)\n'
    )


_ROWS = [
    pytest.param(
        "y = math.max(c ? 1 : na, 0)\n",
        ("? (1) : (na<double>())",),
        "1 0 0 1 1 1\n",  # max(na, 0) is na; max(1, 0) is 1.
        id="math-source",
    ),
    pytest.param(
        "y = ta.sma(c ? 1 : na, 2)\n",
        ("? (1) : (na<double>())", ".compute("),
        "1 0 0 1 1 1\n",  # Two missing samples give na; mean(1, 1) is 1.
        id="ta-source",
    ),
    pytest.param(
        "t = c ? time : na\n"
        "y = t + 60000 < time ? 1.0 : 0.0\n",
        ("double t = 0.0;", "? (current_bar_.timestamp) : (na<double>())"),
        "0 0 0 0 1 1\n",  # na compares false; time + 60000 is later than time.
        id="untyped-time-future",
    ),
    pytest.param(
        "t = c ? time : na\n"
        "y = t - 60000 < time ? 1.0 : 0.0\n",
        ("double t = 0.0;", "? (current_bar_.timestamp) : (na<double>())"),
        "0 0 0 1 1 1\n",  # na compares false; time - 60000 is earlier than time.
        id="untyped-time-past",
    ),
    pytest.param(
        "x = c ? bar_index : na\n"
        "y = x + 1\n",
        ("double x = 0.0;", "? (pine_bar_index()) : (na<double>())"),
        "1 0 0 2 1 1\n",  # na + 1 is na; the second bull bar has index 1, so 1 + 1 is 2.
        id="untyped-bar-index",
    ),
    pytest.param(
        "add_half(bool choose) =>\n"
        "    x = choose ? 1 : na\n"
        "    x + 0.5\n"
        "y = add_half(c)\n",
        ("double x = ", "? (1) : (na<double>())"),
        "1 0 0 1.5 1 1\n",  # The local is na or 1; adding 0.5 gives na or 1.5.
        id="local-addition",
    ),
    pytest.param(
        "half_distance(bool choose) =>\n"
        "    x = choose ? 1 : na\n"
        "    (bar_index - x) / 2\n"
        "y = half_distance(c)\n",
        ("double x = ", "? (1) : (na<double>())"),
        "1 0 0 0 1 1\n",  # (index - na) / 2 is na; on bull bar 1, (1 - 1) / 2 is 0.
        id="local-division",
    ),
    pytest.param(
        "a = high > open\n"
        "b = c\n"
        "float z = a ? (b ? 1 : na) : 2.5\n"
        "y = z\n",
        ("? (1) : (na<double>())",),
        "1 0 0 1 1 1\n",  # Both bars select the inner arm: missing on bear, 1 on bull.
        id="nested-float",
    ),
    pytest.param(
        "values = array.from(1)\n"
        "y = c ? values.shift() : na\n"
        "remaining = values.size()\n"
        "if remaining != (c ? 0 : 1)\n"
        '    runtime.error("selection must evaluate only its chosen arm")\n',
        ("double y = 0.0;", ": (na<double>())"),
        "1 0 0 1 1 1\n",  # Bear leaves the array alone; bull shifts its only element once.
        id="selected-effect",
    ),
]


def _assert_nullable_target(cpp: str) -> None:
    # A nullable integer selection needs double storage so arithmetic preserves na.
    assert "double k = 0.0;" in cpp
    assignment = next(
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("k = ")
    )
    assert "? (1) : (na<double>())" in assignment
    assert "na<int>()" not in assignment


@pytest.mark.parametrize("body,fragments,expected", _ROWS)
def test_numeric_selection_emits_nan_with_a_declared_integer(
    body: str, fragments: tuple[str, ...], expected: str,
) -> None:
    cpp = transpile(_source(body))
    for fragment in fragments:
        assert fragment in cpp
    _assert_nullable_target(cpp)
    # The missing numeric arm is already NaN; no integer-to-float repair is needed.
    assert "auto _pf_w = (" not in cpp
    compile_cpp(cpp, label="numeric-selection")


_DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <limits>
#include <type_traits>

int main() {
    GeneratedStrategy bear_strategy;
    GeneratedStrategy bull_strategy;
    static_assert(std::is_same_v<decltype(bear_strategy.y), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.k), double>);
    pineforge::Bar bears[] = {
        {100.0, 101.0, 98.0, 99.0, 10.0, 1700000000000LL},
        {100.0, 101.0, 98.0, 99.0, 10.0, 1700000060000LL},
    };
    pineforge::Bar bulls[] = {
        {100.0, 102.0, 99.0, 101.0, 10.0, 1700000000000LL},
        {100.0, 102.0, 99.0, 101.0, 10.0, 1700000060000LL},
    };
    bear_strategy.run(bears, 2);
    bull_strategy.run(bulls, 2);
    if (!bear_strategy.last_error().empty() || !bull_strategy.last_error().empty())
        return 1;
    const bool bear_na = std::isnan(bear_strategy.y);
    const bool bull_na = std::isnan(bull_strategy.y);
    std::printf("%d %.17g %d %.17g %d %d\n",
                bear_na, bear_na ? 0.0 : bear_strategy.y,
                bull_na, bull_na ? 0.0 : bull_strategy.y,
                std::isnan(bear_strategy.k),
                bull_strategy.k == 1.0);
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body,fragments,expected", _ROWS)
def test_numeric_selection_values_on_bear_and_bull_bars(
    body: str, fragments: tuple[str, ...], expected: str, opt: str,
) -> None:
    assert run_emitted_tu(
        transpile(_source(body)), _DRIVER, opt=opt, label="numeric-selection",
    ) == expected


_BINARY_ROWS = [
    pytest.param(
        "float y = array.size(array.new_float(3)) + na\n",
        "+ na<double>()",
        "1 0 1 0 1 1\n",  # The size is 3 on both feeds; 3 + NaN is NaN.
        id="array-size-addition",
    ),
    pytest.param(
        "float y = array.size(array.new_float(3)) / na\n",
        "/ (double)(na<double>())",
        "1 0 1 0 1 1\n",  # 3 / NaN is NaN on either feed.
        id="array-size-division",
    ),
    pytest.param(
        "float y = array.size(array.new_float(3)) % na\n",
        ", (double)(na<double>())",
        "1 0 1 0 1 1\n",  # fmod(3, NaN) is NaN on either feed.
        id="array-size-remainder",
    ),
]


@pytest.mark.parametrize("body,fragment,expected", _BINARY_ROWS)
def test_numeric_binary_na_and_string_concatenation_emit_the_numeric_nan(
    body: str, fragment: str, expected: str,
) -> None:
    cpp = transpile(_source(body + 's = "a" + na\n'))
    # Numeric addition, division and remainder keep NaN rather than INT_MIN.
    assert fragment in cpp
    # The numeric NaN is formatted once as "nan", so the string is "anan".
    assert 's = (std::string("a") + std::to_string(na<double>()));' in cpp
    _assert_nullable_target(cpp)
    compile_cpp(cpp, label="numeric-and-string-na")


_BINARY_DRIVER = _DRIVER.replace(
    "    const bool bear_na",
    '    if (bear_strategy.s != "anan" || bull_strategy.s != "anan") return 2;\n'
    "    const bool bear_na",
)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body,fragment,expected", _BINARY_ROWS)
def test_numeric_binary_na_and_string_concatenation_values(
    body: str, fragment: str, expected: str, opt: str,
) -> None:
    # "a" followed by the numeric na text "nan" is the literal string "anan".
    assert run_emitted_tu(
        transpile(_source(body + 's = "a" + na\n')),
        _BINARY_DRIVER, opt=opt, label="numeric-and-string-na",
    ) == expected


_INTEGER_TARGET_BODY = """accept_int(int value) => value
local_int(bool choose) =>
    int local = choose ? 1 : na
    local
returned_int(bool choose) =>
    values = array.from(1)
    choose ? values.shift() : na
argument_value = accept_int(c ? 1 : na)
local_value = local_int(c)
return_value = returned_int(c)
int wide = c ? time : na
y = math.max(c ? 1 : na, 0)
"""


def test_nullable_integer_targets_keep_double_storage() -> None:
    cpp = transpile(_source(_INTEGER_TARGET_BODY))
    # Each nullable numeric boundary holds NaN, including a declared int parameter.
    assert "double accept_int(double " in cpp
    assert "double local = " in cpp
    assert "double returned_int(" in cpp
    # The timestamp is exactly representable as a double beside a NaN arm.
    assert "double wide = " in cpp
    assert "? (current_bar_.timestamp) : (na<double>())" in cpp
    # The independent math argument still takes the default NaN representation.
    assert "? (1) : (na<double>())" in cpp
    _assert_nullable_target(cpp)
    compile_cpp(cpp, label="integer-selection-targets")


_INTEGER_TARGET_DRIVER = _DRIVER.replace(
    "    const bool bear_na",
    r"""    static_assert(std::is_same_v<decltype(bear_strategy.argument_value), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.local_value), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.return_value), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.wide), double>);
    if (!std::isnan(bear_strategy.argument_value)
            || !std::isnan(bear_strategy.local_value)
            || !std::isnan(bear_strategy.return_value)
            || !std::isnan(bear_strategy.wide))
        return 2;
    if (bull_strategy.argument_value != 1.0 || bull_strategy.local_value != 1.0
            || bull_strategy.return_value != 1.0 || bull_strategy.wide != 1700000060000.0)
        return 3;
    const bool bear_na""",
)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_integer_targets_preserve_na_beside_float_consumers(opt: str) -> None:
    # Bear holds NaN at every boundary; bull holds 1 or the exact final timestamp.
    assert run_emitted_tu(
        transpile(_source(_INTEGER_TARGET_BODY)), _INTEGER_TARGET_DRIVER,
        opt=opt, label="integer-selection-targets",
    ) == "1 0 0 1 1 1\n"


_CONSUMER_ROWS = [
    pytest.param(
        "selected = k\n",
        ("double selected = 0.0;",),
        "1 0 0 2 1 1\n",  # The global is NaN or 1, so adding 1 yields NaN or 2.
        id="global",
    ),
    pytest.param(
        "choose_local(bool choose) =>\n"
        "    int local = choose ? 1 : na\n"
        "    local\n"
        "selected = choose_local(c)\n",
        ("double local = ", "double choose_local("),
        "1 0 0 2 1 1\n",  # The local and its return retain NaN or 1 before addition.
        id="function-local",
    ),
    pytest.param(
        "float selected = 0.0\n"
        "if high > open\n"
        "    int local = c ? 1 : na\n"
        "    selected := local\n",
        ("double local = ",),
        "1 0 0 2 1 1\n",  # Both feeds enter the block; its local selects NaN or 1.
        id="block-local",
    ),
    pytest.param(
        "accept_value(int value) => value\n"
        "selected = accept_value(c ? 1 : na)\n",
        ("double accept_value(double value)",),
        "1 0 0 2 1 1\n",  # The integer parameter receives NaN or 1 without narrowing.
        id="parameter",
    ),
    pytest.param(
        "default_value(int value = true ? na : 1) => value\n"
        "selected = c ? default_value(1) : default_value()\n",
        ("double default_value(double value)",),
        "1 0 0 2 1 1\n",  # Bear uses the NaN default; bull explicitly passes 1.
        id="parameter-default",
    ),
    pytest.param(
        "choose_return(bool choose) => choose ? 1 : na\n"
        "selected = choose_return(c)\n",
        ("double choose_return(",),
        "1 0 0 2 1 1\n",  # The return target retains the selected NaN or 1.
        id="return",
    ),
    pytest.param(
        "choose_nested(bool choose) =>\n"
        "    int local = high > open ? (choose ? 1 : na) : 2\n"
        "    local\n"
        "selected = choose_nested(c)\n",
        ("double local = ", "double choose_nested("),
        "1 0 0 2 1 1\n",  # high > open on both feeds, so the nested result is NaN or 1.
        id="nested-local",
    ),
    pytest.param(
        "var int held = c ? 1 : na\n"
        "selected = held\n",
        ("double held = na<double>();",),
        "1 0 0 2 1 1\n",  # The first bar stores NaN or 1, retained on the second bar.
        id="persistent-global",
    ),
    pytest.param(
        "varip int held = c ? 1 : na\n"
        "selected = held\n",
        ("double held = na<double>();",),
        "1 0 0 2 1 1\n",  # Each historical feed initializes once to NaN or 1.
        id="persistent-intrabar-global",
    ),
    pytest.param(
        "int current = c ? 1 : na\n"
        "previous = current[1]\n"
        "selected = current\n"
        "if (bar_index == 0 or not c) and not na(previous)\n"
        '    runtime.error("missing history must remain na")\n'
        "if bar_index == 1 and c and previous != 1\n"
        '    runtime.error("previous bull value must be one")\n',
        ("Series<double> current",),
        "1 0 0 2 1 1\n",  # History starts missing; the second slot reads NaN or 1.
        id="global-history",
    ),
    pytest.param(
        "history_value(int value) =>\n"
        "    previous = value[1]\n"
        "    na(previous) ? value : previous\n"
        "selected = history_value(c ? 1 : na)\n",
        ("double history_value_cs0(const Series<double>& value)",),
        "1 0 0 2 1 1\n",  # The first call uses current NaN/1; the next uses prior NaN/1.
        id="parameter-history",
    ),
    pytest.param(
        "choose_loop(bool choose) =>\n"
        "    for iteration = 0 to 0\n"
        "        int local = choose ? 1 : na\n"
        "        local\n"
        "selected = choose_loop(c)\n",
        ("double local = ", "double choose_loop("),
        "1 0 0 2 1 1\n",  # One loop iteration returns its nullable local as NaN or 1.
        id="loop-return",
    ),
    pytest.param(
        "choose_assignment(bool choose) =>\n"
        "    int local = choose ? 1 : na\n"
        "    local := choose ? 1 : na\n"
        "selected = choose_assignment(c)\n",
        ("double local = ", "double choose_assignment("),
        "1 0 0 2 1 1\n",  # The returned assignment keeps the local's NaN or 1 storage.
        id="assignment-return",
    ),
    pytest.param(
        "type Holder\n"
        "    int seed\n"
        "method take(Holder self, int value) => value\n"
        "holder = Holder.new(0)\n"
        "selected = holder.take(c ? 1 : na)\n",
        ("double _udt_Holder_take(Holder self, double value)",),
        "1 0 0 2 1 1\n",  # The bound method parameter and its return retain NaN or 1.
        id="method-parameter",
    ),
    pytest.param(
        "type Holder\n"
        "    int seed\n"
        "method take(Holder self, int value = true ? na : 1) => value\n"
        "holder = Holder.new(0)\n"
        "selected = c ? holder.take(1) : holder.take()\n",
        ("double _udt_Holder_take(Holder self, double value)",),
        "1 0 0 2 1 1\n",  # Bear uses the method's NaN default; bull passes 1.
        id="method-parameter-default",
    ),
    pytest.param(
        "choose_shadow(bool choose) =>\n"
        "    int captured = k\n"
        "    int k = choose ? 1 : na\n"
        "    captured\n"
        "selected = choose_shadow(c)\n",
        ("double captured = ", "double choose_shadow("),
        "1 0 0 2 1 1\n",  # The earlier read captures global NaN/1 before the local k exists.
        id="global-before-local-shadow",
    ),
]


_CONSUMERS = """y = selected + 1
bool positive = selected > 0
string text = str.tostring(selected)
maximum = math.max(selected, 0)
average = ta.sma(selected, 2)
plot_value = selected
bool missing_value = na(selected)
plot(plot_value)
direct_y = k + 1
bool direct_positive = k > 0
string direct_text = str.tostring(k)
direct_maximum = math.max(k, 0)
direct_average = ta.sma(k, 2)
direct_plot_value = k
plot(direct_plot_value)
"""


@pytest.mark.parametrize("body,fragments,expected", _CONSUMER_ROWS)
def test_declared_nullable_integer_consumers_keep_double_storage(
    body: str, fragments: tuple[str, ...], expected: str,
) -> None:
    cpp = transpile(_source(body + _CONSUMERS))
    _assert_nullable_target(cpp)
    for fragment in fragments:
        assert fragment in cpp
    # The value supplied to plot stays the same double read as every numeric consumer.
    assert "plot_value = selected;" in cpp
    assert "direct_plot_value = k;" in cpp
    # Default formatting maps a numeric NaN to the literal text "NaN".
    assert "pine_str_tostring_tv(selected, std::string(), syminfo_mintick_)" in cpp
    compile_cpp(cpp, label="nullable-integer-consumers")


# Missing inputs give NaN arithmetic/max/SMA/plot, false comparisons and text "NaN".
# Finite 1 gives y=2, true comparisons, text "1", max=1, SMA(1,1)=1 and plot=1.
_CONSUMER_DRIVER = _DRIVER.replace(
    "    const bool bear_na",
    r"""    static_assert(std::is_same_v<decltype(bear_strategy.selected), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.positive), bool>);
    static_assert(std::is_same_v<decltype(bear_strategy.maximum), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.average), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.plot_value), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_y), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_positive), bool>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_plot_value), double>);
    if (!std::isnan(bear_strategy.selected) || !bear_strategy.missing_value
            || bear_strategy.positive || bear_strategy.text != "NaN"
            || !std::isnan(bear_strategy.maximum)
            || !std::isnan(bear_strategy.average)
            || !std::isnan(bear_strategy.plot_value)
            || !std::isnan(bear_strategy.direct_y) || bear_strategy.direct_positive
            || bear_strategy.direct_text != "NaN"
            || !std::isnan(bear_strategy.direct_maximum)
            || !std::isnan(bear_strategy.direct_average)
            || !std::isnan(bear_strategy.direct_plot_value))
        return 2;
    if (bull_strategy.selected != 1.0 || bull_strategy.missing_value
            || !bull_strategy.positive || bull_strategy.text != "1"
            || bull_strategy.maximum != 1.0 || bull_strategy.average != 1.0
            || bull_strategy.plot_value != 1.0 || bull_strategy.direct_y != 2.0
            || !bull_strategy.direct_positive || bull_strategy.direct_text != "1"
            || bull_strategy.direct_maximum != 1.0 || bull_strategy.direct_average != 1.0
            || bull_strategy.direct_plot_value != 1.0)
        return 3;
    const bool bear_na""",
)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body,fragments,expected", _CONSUMER_ROWS)
def test_declared_nullable_integer_consumers_on_bear_and_bull_bars(
    body: str, fragments: tuple[str, ...], expected: str, opt: str,
) -> None:
    # Two bear samples are NaN; two bull samples are 1, so SMA(2) is NaN or 1.
    assert run_emitted_tu(
        transpile(_source(body + _CONSUMERS)), _CONSUMER_DRIVER,
        opt=opt, label="nullable-integer-consumers",
    ) == expected

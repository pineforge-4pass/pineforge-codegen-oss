"""Nullable numeric selections retain their storage at each scope boundary."""

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
    pytest.param(
        "choose_value(bool choose) =>\n"
        "    values = array.from(7)\n"
        "    choose ? na : values.shift()\n"
        "y = choose_value(not c) + 1\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # Bear gives NaN + 1 = NaN; bull gives 7 + 1 = 8.
        id="array-element-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) => choose ? na : 1\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : (1)"),
        "1 0 0 2 1 1\n",  # Bear returns NaN; bull returns 1, then 1 + 1 = 2.
        id="literal-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) => choose ? na : time\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : (current_bar_.timestamp)"),
        "1 0 0 1700000060001 1 1\n",  # Bull bar 1 has time 1700000060000; adding 1 is exact.
        id="timestamp-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) => choose ? na : bar_index\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : (pine_bar_index())"),
        "1 0 0 2 1 1\n",  # Bear returns NaN; bull bar 1 returns index 1, then 1 + 1 = 2.
        id="bar-index-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) =>\n"
        "    n = 5\n"
        "    choose ? na : n\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 6 1 1\n",  # Bear returns NaN; bull returns the local 5, then 5 + 1 = 6.
        id="local-integer-return-na-first",
    ),
    pytest.param(
        "type Holder\n"
        "    array<int> items\n"
        "choose_value(bool choose) =>\n"
        "    holder = Holder.new(array.from(7))\n"
        "    choose ? na : holder.items.shift()\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # Bear returns NaN; bull shifts 7 from its fresh field array, then adds 1.
        id="field-array-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) => choose ? na : array.from(7).shift()\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # The missing branch is NaN; the temporary's sole element is 7.
        id="temporary-array-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) =>\n"
        "    values = array.from(7)\n"
        "    selected_value = choose ? na : values.shift()\n"
        "    selected_value\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "double selected_value = ", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # The alias preserves NaN on bear and 7 on bull before addition.
        id="aliased-array-return-na-first",
    ),
    pytest.param(
        "type Holder\n"
        "    array<int> items\n"
        "method take(Holder this, bool choose) =>\n"
        "    choose ? na : this.items.shift()\n"
        "holder = Holder.new(array.from(7))\n"
        "selected = holder.take(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double _udt_Holder_take(", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # Each bar creates one field array; the method selects NaN or 7.
        id="method-array-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) =>\n"
        "    values = map.new<string, int>()\n"
        '    values.put("k", 7)\n'
        '    choose ? na : values.get("k")\n'
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # Bear returns NaN; the populated map returns 7 on bull, then adds 1.
        id="map-value-return-na-first",
    ),
    pytest.param(
        "choose_value(bool choose) =>\n"
        "    values = matrix.new<int>(1, 1, 7)\n"
        "    choose ? na : values.get(0, 0)\n"
        "selected = choose_value(not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(bool choose)", "? (na<double>()) : ("),
        "1 0 0 8 1 1\n",  # Bear returns NaN; the single matrix element is 7 on bull, then adds 1.
        id="matrix-value-return-na-first",
    ),
    pytest.param(
        "choose_value(value, bool choose) => choose ? na : value\n"
        "selected = choose_value(5, not c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(", "? (na<double>()) : (value)"),
        "1 0 0 6 1 1\n",  # The integer argument is 5; the missing function result is NaN.
        id="untyped-parameter-na-first",
    ),
    pytest.param(
        "choose_value(value, bool choose) => choose ? value : na\n"
        "selected = choose_value(5, c)\n"
        "y = selected + 1\n"
        "plot(selected)\nplot(y)\n",
        ("double choose_value(", "? (value) : (na<double>())"),
        "1 0 0 6 1 1\n",  # Bear selects NaN; bull returns the integer argument 5, then adds 1.
        id="untyped-parameter-na-second",
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
    choose ? 1 : na
argument_value = accept_int(c ? 1 : na)
local_value = local_int(c)
return_value = returned_int(c)
int wide = c ? time : na
y = math.max(c ? 1 : na, 0)
"""


def test_nullable_integer_targets_keep_their_scope_storage() -> None:
    cpp = transpile(_source(_INTEGER_TARGET_BODY))
    # A declared parameter, local and integer return keep guarded integer storage.
    assert "int accept_int(int value)" in cpp
    assert "int local = [&](){ auto _pf_v = (" in cpp
    assert "int local_int(bool choose)" in cpp
    assert "int returned_int(" in cpp
    assert "int argument_value = 0;" in cpp
    assert "int local_value = 0;" in cpp
    assert "int return_value = 0;" in cpp
    # The timestamp is exactly representable as a double beside a NaN arm.
    assert "double wide = " in cpp
    assert "? (current_bar_.timestamp) : (na<double>())" in cpp
    # The independent math argument still takes the default NaN representation.
    assert "? (1) : (na<double>())" in cpp
    _assert_nullable_target(cpp)
    compile_cpp(cpp, label="integer-selection-targets")


_INTEGER_TARGET_DRIVER = _DRIVER.replace(
    "    const bool bear_na",
    r"""    static_assert(std::is_same_v<decltype(bear_strategy.argument_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.local_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.return_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.wide), double>);
    if (bear_strategy.argument_value != -2147483648LL
            || bear_strategy.local_value != -2147483648LL
            || bear_strategy.return_value != -2147483648LL
            || !std::isnan(bear_strategy.wide))
        return 2;
    if (bull_strategy.argument_value != 1.0 || bull_strategy.local_value != 1.0
            || bull_strategy.return_value != 1.0 || bull_strategy.wide != 1700000060000.0)
        return 3;
    const bool bear_na""",
)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_integer_targets_preserve_na_beside_float_consumers(opt: str) -> None:
    # Bear has three INT_MIN values and a NaN timestamp; bull has 1 and the timestamp.
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
        "float selected = 0.0\n"
        "if high > open\n"
        "    int local = c ? 1 : na\n"
        "    selected := local\n",
        ("double local = ",),
        "1 0 0 2 1 1\n",  # Both feeds enter the block; its local selects NaN or 1.
        id="block-local",
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
]


_FUNCTION_CONSUMER_ROWS = [
    pytest.param(
        "choose_local(bool choose) =>\n"
        "    int local = choose ? 1 : na\n"
        "    local\n"
        "selected = choose_local(c)\n",
        ("int local = [&](){ auto _pf_v = (", "int choose_local("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # INT_MIN + 1 is -2147483647; finite 1 + 1 is 2.
        id="function-local",
    ),
    pytest.param(
        "choose_leaf(bool choose) =>\n"
        "    int leaf = choose ? 1 : na\n"
        "    leaf\n"
        "choose_caller(bool choose) =>\n"
        "    int local = choose_leaf(choose)\n"
        "    local\n"
        "selected = choose_caller(c)\n",
        ("int leaf = [&](){ auto _pf_v = (", "int choose_leaf(",
         "int local = choose_leaf(choose);", "int choose_caller("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # Both integer calls keep INT_MIN or 1; adding 1 gives the shown sum.
        id="local-through-call",
    ),
    pytest.param(
        "accept_value(int value) => value\n"
        "selected = accept_value(c ? 1 : na)\n",
        ("int accept_value(int value)", "accept_value([&](){ auto _pf_v = ("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # The guarded argument is INT_MIN or 1 before integer addition.
        id="parameter",
    ),
    pytest.param(
        "default_value(int value = true ? na : 1) => value\n"
        "selected = c ? default_value(1) : default_value()\n",
        ("int default_value(int value)", "default_value([&](){ auto _pf_v = ("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # Bear narrows the missing default to INT_MIN; bull passes 1.
        id="parameter-default",
    ),
    pytest.param(
        "choose_return(bool choose) => choose ? 1 : na\n"
        "selected = choose_return(c)\n",
        ("int choose_return(", "return [&](){ auto _pf_v = ("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # The guarded return is INT_MIN or 1; adding 1 remains integer.
        id="return",
    ),
    pytest.param(
        "choose_nested(bool choose) =>\n"
        "    int local = high > open ? (choose ? 1 : na) : 2\n"
        "    local\n"
        "selected = choose_nested(c)\n",
        ("int local = [&](){ auto _pf_v = (", "int choose_nested("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # Both feeds take the inner selection, then narrow to INT_MIN or 1.
        id="nested-local",
    ),
    pytest.param(
        "history_value(int value) =>\n"
        "    previous = value[1]\n"
        "    na(previous) ? value : previous\n"
        "selected = history_value(c ? 1 : na)\n",
        ("int64_t history_value_cs0(const Series<int64_t>& value)",
         "double previous = value[1];"),
        "int64_t",
        "-9223372036854775808 -9.2233720368547758e+18 0 NaN 0 "
        "-9.2233720368547758e+18 -9223372036854775808 1\n"
        "1 2 1 1 1 -4.6116860184273879e+18 1 0\n",
        # The double history alias gives an initial INT64_MIN result; bull SMA averages it with 1.
        id="parameter-history",
    ),
    pytest.param(
        "choose_loop(bool choose) =>\n"
        "    for iteration = 0 to 0\n"
        "        int local = choose ? 1 : na\n"
        "        local\n"
        "selected = choose_loop(c)\n",
        ("int local = [&](){ auto _pf_v = (", "double choose_loop("),
        "double",
        # The loop's int sentinel survives as a finite double, recording current behavior.
        # selected: bear promotes INT_MIN to -2147483648.0; bull promotes 1 to 1.0.
        # y: -2147483648.0 + 1 = -2147483647.0; 1.0 + 1 = 2.0.
        # positive: -2147483648.0 > 0 is false (0); 1.0 > 0 is true (1).
        # text: finite -2147483648.0 formats as "-2147483648"; 1.0 formats as "1".
        # maximum: max(-2147483648.0, 0) = 0.0; max(1.0, 0) = 1.0.
        # average: mean(-2147483648.0, -2147483648.0) = -2147483648.0; mean(1.0, 1.0) = 1.0.
        # plot_value: selected copies -2147483648.0 or 1.0, printed as integers.
        # missing_value: neither finite double is NaN, so both are false (0).
        "-2147483648 -2147483647 0 -2147483648 0 -2147483648 -2147483648 0\n"
        "1 2 1 1 1 1 1 0\n",
        id="loop-return",
    ),
    pytest.param(
        "choose_assignment(bool choose) =>\n"
        "    int local = choose ? 1 : na\n"
        "    local := choose ? 1 : na\n"
        "selected = choose_assignment(c)\n",
        ("int local = [&](){ auto _pf_v = (", "int choose_assignment(",
         "local = [&](){ auto _pf_v = ("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # The assignment narrows to INT_MIN or 1 and returns that integer.
        id="assignment-return",
    ),
    pytest.param(
        "choose_shadow(bool choose) =>\n"
        "    int captured = k\n"
        "    int k = choose ? 1 : na\n"
        "    captured\n"
        "selected = choose_shadow(c)\n",
        ("int captured = [&](){ auto _pf_v = (", "int choose_shadow(",
         "int k = [&](){ auto _pf_v = ("),
        "int",
        "-2147483648 -2147483647 0 NaN 0 -2147483648 -2147483648 1\n"
        "1 2 1 1 1 1 1 0\n",  # The earlier global read narrows to INT_MIN or 1 before shadowing.
        id="global-before-local-shadow",
    ),
]


_METHOD_CONSUMER_ROWS = [
    pytest.param(
        "type Holder\n"
        "    int seed\n"
        "method take(Holder self, int value) => self, value\n"
        "holder = Holder.new(0)\n"
        "selected = holder.take(c ? 1 : na)\n",
        "selected = _udt_Holder_take(holder, ((c) ? (1) : (na<double>())));",
        # The method takes an int; its numeric ternary argument retains the implicit conversion.
        id="method-parameter",
    ),
    pytest.param(
        "type Holder\n"
        "    int seed\n"
        "method take(Holder self, int value = true ? na : 1) => self, value\n"
        "holder = Holder.new(0)\n"
        "selected = c ? holder.take(1) : holder.take()\n",
        "selected = ((c) ? (_udt_Holder_take(holder, 1)) : "
        "(_udt_Holder_take(holder, ((true) ? (na<double>()) : (1)))));",
        # The omitted int parameter receives its authored numeric selection at the call.
        id="method-parameter-default",
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


@pytest.mark.parametrize("body,fragments,storage,expected", _FUNCTION_CONSUMER_ROWS)
def test_function_integer_consumers_keep_guarded_storage(
    body: str, fragments: tuple[str, ...], storage: str, expected: str,
) -> None:
    cpp = transpile(_source(body + _CONSUMERS))
    _assert_nullable_target(cpp)
    for fragment in fragments:
        assert fragment in cpp
    # Calls keep their result type; arithmetic is assigned to a double.
    initializer = "0.0" if storage == "double" else "0"
    assert f"{storage} selected = {initializer};" in cpp
    assert "double y = 0.0;" in cpp
    assert "y = (selected + 1);" in cpp
    assert "plot_value = selected;" in cpp
    assert "direct_plot_value = k;" in cpp
    assert "pine_str_tostring_tv(selected, std::string(), syminfo_mintick_)" in cpp
    compile_cpp(cpp, label="function-integer-consumers")


# Integer consumers expose the sentinel to arithmetic, min/max and TA inputs.
_FUNCTION_CONSUMER_DRIVER = _DRIVER.split("    const bool bear_na", 1)[0] + r"""
    static_assert(std::is_same_v<decltype(bear_strategy.selected), _EXPECTED_INT_TYPE>);
    static_assert(std::is_same_v<decltype(bear_strategy.positive), bool>);
    static_assert(std::is_same_v<decltype(bear_strategy.maximum), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.average), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.plot_value), _EXPECTED_INT_TYPE>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_y), double>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_positive), bool>);
    static_assert(std::is_same_v<decltype(bear_strategy.direct_plot_value), double>);
    if (!std::isnan(bear_strategy.k) || bull_strategy.k != 1.0
            || !std::isnan(bear_strategy.direct_y) || bear_strategy.direct_positive
            || bear_strategy.direct_text != "NaN"
            || !std::isnan(bear_strategy.direct_maximum)
            || !std::isnan(bear_strategy.direct_average)
            || !std::isnan(bear_strategy.direct_plot_value))
        return 2;
    if (bull_strategy.direct_y != 2.0 || !bull_strategy.direct_positive
            || bull_strategy.direct_text != "1"
            || bull_strategy.direct_maximum != 1.0 || bull_strategy.direct_average != 1.0
            || bull_strategy.direct_plot_value != 1.0)
        return 3;
    std::printf("%lld %.17g %d %s %.17g %.17g %lld %d\n",
                static_cast<long long>(bear_strategy.selected), bear_strategy.y,
                bear_strategy.positive, bear_strategy.text.c_str(),
                bear_strategy.maximum, bear_strategy.average,
                static_cast<long long>(bear_strategy.plot_value), bear_strategy.missing_value);
    std::printf("%lld %.17g %d %s %.17g %.17g %lld %d\n",
                static_cast<long long>(bull_strategy.selected), bull_strategy.y,
                bull_strategy.positive, bull_strategy.text.c_str(),
                bull_strategy.maximum, bull_strategy.average,
                static_cast<long long>(bull_strategy.plot_value), bull_strategy.missing_value);
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body,fragments,storage,expected", _FUNCTION_CONSUMER_ROWS)
def test_function_integer_consumers_on_bear_and_bull_bars(
    body: str, fragments: tuple[str, ...], storage: str, expected: str, opt: str,
) -> None:
    # Each row spells the values of both feeds, including their two-sample averages.
    assert run_emitted_tu(
        transpile(_source(body + _CONSUMERS)),
        _FUNCTION_CONSUMER_DRIVER.replace("_EXPECTED_INT_TYPE", storage),
        opt=opt, label="function-integer-consumers",
    ) == expected


@pytest.mark.parametrize("body,expected_assignment", _METHOD_CONSUMER_ROWS)
def test_method_integer_consumers_keep_their_signature_and_argument_expression(
    body: str, expected_assignment: str,
) -> None:
    cpp = transpile(_source(body + _CONSUMERS))
    _assert_nullable_target(cpp)
    # An int identity method keeps its parameter and return, including a defaulted call.
    assert (
        "    int _udt_Holder_take(Holder self, int value) {\n"
        "        return value;\n"
        "    }"
    ) in cpp
    assignment = next(
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("selected = ")
    )
    assert assignment == expected_assignment
    assert "int selected = 0;" in cpp
    assert "int plot_value = 0;" in cpp
    assert "double y = 0.0;" in cpp
    assert "y = (selected + 1);" in cpp
    assert "plot_value = selected;" in cpp
    assert "direct_plot_value = k;" in cpp
    assert "pine_str_tostring_tv(selected, std::string(), syminfo_mintick_)" in cpp
    compile_cpp(cpp, label="method-integer-consumers")

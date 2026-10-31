"""Numeric selections keep NaN until an integer target is known."""

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


def _assert_integer_target(cpp: str) -> None:
    # A declared int holds INT_MIN on the missing arm, independently of float consumers.
    assert "int k = 0;" in cpp
    assignment = next(
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("k = ")
    )
    assert "? (1) : (na<int>())" in assignment
    assert "na<double>()" not in assignment


@pytest.mark.parametrize("body,fragments,expected", _ROWS)
def test_numeric_selection_emits_nan_without_an_integer_target(
    body: str, fragments: tuple[str, ...], expected: str,
) -> None:
    cpp = transpile(_source(body))
    for fragment in fragments:
        assert fragment in cpp
    _assert_integer_target(cpp)
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
    static_assert(std::is_same_v<decltype(bear_strategy.k), int>);
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
                bear_strategy.k == std::numeric_limits<int>::min(),
                bull_strategy.k == 1);
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
    _assert_integer_target(cpp)
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


def test_integer_targets_keep_their_declared_width_and_sentinel() -> None:
    cpp = transpile(_source(_INTEGER_TARGET_BODY))
    # An int parameter, int local and int return all keep na<int>().
    assert "int accept_int(int " in cpp
    assert "int local = " in cpp
    assert "int returned_int(" in cpp
    # A timestamp requires the 64-bit integer representation even in an na selection.
    assert "int64_t wide = " in cpp
    assert "? (current_bar_.timestamp) : (na<int64_t>())" in cpp
    # The independent math argument still takes the default NaN representation.
    assert "? (1) : (na<double>())" in cpp
    _assert_integer_target(cpp)
    compile_cpp(cpp, label="integer-selection-targets")


_INTEGER_TARGET_DRIVER = _DRIVER.replace(
    "    const bool bear_na",
    r"""    static_assert(std::is_same_v<decltype(bear_strategy.argument_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.local_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.return_value), int>);
    static_assert(std::is_same_v<decltype(bear_strategy.wide), int64_t>);
    if (bear_strategy.argument_value != std::numeric_limits<int>::min()
            || bear_strategy.local_value != std::numeric_limits<int>::min()
            || bear_strategy.return_value != std::numeric_limits<int>::min()
            || bear_strategy.wide != std::numeric_limits<int64_t>::min())
        return 2;
    if (bull_strategy.argument_value != 1 || bull_strategy.local_value != 1
            || bull_strategy.return_value != 1 || bull_strategy.wide != 1700000060000LL)
        return 3;
    const bool bear_na""",
)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_integer_targets_preserve_na_beside_float_consumers(opt: str) -> None:
    # Bear uses each width's minimum; bull uses 1 or the final timestamp, with math na/1.
    assert run_emitted_tu(
        transpile(_source(_INTEGER_TARGET_BODY)), _INTEGER_TARGET_DRIVER,
        opt=opt, label="integer-selection-targets",
    ) == "1 0 0 1 1 1\n"

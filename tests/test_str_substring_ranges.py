"""Native coverage for the TradingView range rules of ``str.substring``."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("substring ranges")\n'

DRIVER = r'''
#include <iostream>
#include <string>
#include <vector>

static void print_hex(const std::string& value) {
    std::cout << "HEX:";
    for (unsigned char byte : value) {
        static constexpr char digits[] = "0123456789abcdef";
        std::cout << digits[byte >> 4] << digits[byte & 15];
    }
    std::cout << "\n";
}

int main() {
    std::vector<pineforge::Bar> bars(1);
    bars[0] = Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size());
    if (!strategy.last_error().empty()) {
        const std::string code = pineforge::run_failure_code_of(strategy);
        std::cout << "STOP:" << (code.empty() ? strategy.last_error() : code) << "\n";
    } else {
        print_hex(strategy.result);
    }
}
'''


def _run(call: str, *, opt: str) -> str:
    cpp = transpile(HEADER + f"result = {call}\n")
    return run_emitted_tu(cpp, DRIVER, opt=opt, label="substring range")


def _hex(text: str) -> str:
    return "HEX:" + text.encode("utf-8", "surrogatepass").hex()


RANGE_ROWS = [
    pytest.param(
        'str.substring("abc", 2, 1)',
        'str.substring("abc", bar_index >= 0 ? 2 : 0, '
        'bar_index >= 0 ? 1 : 0)',
        "STOP:pine_string_error",
        id="u-substr-rev",
    ),
    pytest.param(
        'str.substring("abc", 0, -1)',
        'str.substring("abc", bar_index >= 0 ? 0 : 1, '
        'bar_index >= 0 ? -1 : 0)',
        "STOP:pine_string_error",
        id="u-substr-neg-end",
    ),
    pytest.param(
        'str.substring("abc", 1, na)',
        'str.substring("abc", bar_index >= 0 ? 1 : 0, '
        'bar_index > 1000000 ? 1 : na)',
        "STOP:pine_string_error",
        id="u-substr-end-na",
    ),
    pytest.param(
        'str.substring("abc", 0, na)',
        'str.substring("abc", bar_index >= 0 ? 0 : 1, '
        'bar_index > 1000000 ? 1 : na)',
        _hex(""),
        id="u-substr-end-na-zero",
    ),
    pytest.param(
        'str.substring("abc", na, 2)',
        'str.substring("abc", bar_index > 1000000 ? 1 : na, '
        'bar_index >= 0 ? 2 : 0)',
        _hex("ab"),
        id="u-substr-begin-na",
    ),
    pytest.param(
        'str.substring("abc", na)',
        'str.substring("abc", bar_index > 1000000 ? 1 : na)',
        _hex("abc"),
        id="u-substr-two-na",
    ),
    # The two rows with a `na` SOURCE have no constant form (None): a bare `na`
    # literal passed as the string argument is typed as a number by the
    # generator and does not build, which has nothing to do with this change
    # (it is the same for every call with a string parameter) and TradingView
    # was probed with a string-typed `na` only.
    pytest.param(
        None,
        'str.substring(bar_index > 1000000 ? "abc" : na, '
        'bar_index >= 0 ? 0 : 1, bar_index >= 0 ? 2 : 0)',
        _hex(""),
        id="u-substr-source-na",
    ),
    pytest.param(
        None,
        'str.substring(bar_index > 1000000 ? "abc" : na, '
        'bar_index >= 0 ? 1 : 0, bar_index >= 0 ? 2 : 0)',
        "STOP:pine_string_error",
        id="u-substr-source-na-begin1",
    ),
    pytest.param(
        'str.substring("abc", 1, 1)',
        'str.substring("abc", bar_index >= 0 ? 1 : 0, '
        'bar_index >= 0 ? 1 : 0)',
        _hex(""),
        id="u-substr-eq",
    ),
    pytest.param(
        'str.substring("abc", 0, 10)',
        'str.substring("abc", bar_index >= 0 ? 0 : 1, '
        'bar_index >= 0 ? 10 : 0)',
        _hex("abc"),
        id="u-substr-over",
    ),
    pytest.param(
        'str.substring("abc", 3)',
        'str.substring("abc", bar_index >= 0 ? 3 : 0)',
        _hex(""),
        id="u-substr-two-len",
    ),
    pytest.param(
        'str.substring("abc", 4)',
        'str.substring("abc", bar_index >= 0 ? 4 : 0)',
        "STOP:pine_string_error",
        id="u-substr-two-over",
    ),
    pytest.param(
        'str.substring("abc", 4, 5)',
        'str.substring("abc", bar_index >= 0 ? 4 : 0, '
        'bar_index >= 0 ? 5 : 0)',
        "STOP:pine_string_error",
        id="u-substr-three-over",
    ),
    pytest.param(
        'str.substring("a\U0001f600b", 1, 3)',
        'str.substring("a\U0001f600b", bar_index >= 0 ? 1 : 0, '
        'bar_index >= 0 ? 3 : 0)',
        _hex("\U0001f600"),
        id="utf16-emoji",
    ),
    pytest.param(
        'str.substring("a\U0001f600b", 2, 1)',
        'str.substring("a\U0001f600b", bar_index >= 0 ? 2 : 0, '
        'bar_index >= 0 ? 1 : 0)',
        "STOP:pine_string_error",
        id="utf16-emoji-rev",
    ),
    pytest.param(
        'str.substring("a\u00e9b", 1, 2)',
        'str.substring("a\u00e9b", bar_index >= 0 ? 1 : 0, '
        'bar_index >= 0 ? 2 : 0)',
        _hex("\u00e9"),
        id="utf16-accent",
    ),
]


@pytest.mark.parametrize("constant_call,dynamic_call,expected", RANGE_ROWS)
@pytest.mark.parametrize("opt", ["-O0", "-O2", "-O3"])
def test_substring_tradingview_ranges(constant_call, dynamic_call, expected, opt):
    if constant_call is not None:
        assert _run(constant_call, opt=opt) == expected + "\n"
    assert _run(dynamic_call, opt=opt) == expected + "\n"


def _utf16_slice(text: str, begin: int, end: int | None = None) -> str:
    encoded = text.encode("utf-16-le", "surrogatepass")
    units = [
        int.from_bytes(encoded[index:index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    selected = units[begin:] if end is None else units[begin:min(end, len(units))]
    sliced = b"".join(unit.to_bytes(2, "little") for unit in selected)
    return sliced.decode("utf-16-le", "surrogatepass")


IN_RANGE_ROWS = [
    pytest.param("abc", 0, 0, id="ascii-empty"),
    pytest.param("abc", 0, 3, id="ascii-full"),
    pytest.param("abc", 1, 3, id="ascii-tail"),
    pytest.param("abc", 2, None, id="ascii-two-argument-tail"),
    pytest.param("a\u00e9b", 1, 2, id="accent"),
    pytest.param("a\U0001f600b", 1, 3, id="emoji"),
    pytest.param("a\U0001f600b", 2, 3, id="emoji-low-half"),
]


@pytest.mark.parametrize("text,begin,end", IN_RANGE_ROWS)
def test_substring_in_range_values_are_utf16_slices(text, begin, end):
    arguments = str(begin) if end is None else f"{begin}, {end}"
    expected = _utf16_slice(text, begin, end)
    assert _run(f'str.substring("{text}", {arguments})', opt="-O2") == _hex(expected) + "\n"


def test_substring_evaluates_arguments_once_in_source_order():
    cpp = transpile(
        HEADER
        + """
var int calls = 0
var string order = ""
source_text() =>
    calls += 1
    order += "s"
    "abcd"
position(int value, string marker) =>
    calls += 1
    order += marker
    value
result = str.substring(source_text(), position(1, "b"), position(3, "e"))
"""
    )
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        {1, 1, 1, 1, 1, 0}, {1, 1, 1, 1, 1, 1},
        {1, 1, 1, 1, 1, 2},
    };
    strategy.run(bars, 3);
    assert(strategy.result == "bc");
    assert(strategy.calls == 9);
    assert(strategy.order == "sbesbesbe");
    std::cout << strategy.calls << ":" << strategy.order << "\n";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="substring argument order") == (
        "9:sbesbesbe\n"
    )

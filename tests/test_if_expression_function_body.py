"""Conditional function bodies share statement-tail typing and returns."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = '''//@version=6
strategy("Conditional function body", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

'''
_ORDERS = '''if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
'''


def _source(
    definition: str,
    *,
    setup: str = "",
    candidate: str = "unitsFor(bar_index % 20 < 10)",
    units: str = "na(candidate) ? 1 : candidate",
) -> str:
    return (
        _HEADER + definition + "\n" + setup
        + f"candidate = {candidate}\nunits = {units}\n"
        + _ORDERS
    )


@dataclass(frozen=True)
class _Case:
    source: str
    signature: str
    expected: str


_CASES = {
    # The comment changes no value: 35 on bars 0..9 modulo 20, then int na.
    "header_with_comment": _Case(
        _source('''unitsFor(bool selected) => if selected // Inline conditional marker.
    35
'''),
        "int unitsFor(",
        "35 35\nna 1\n35 35\nna 1\n",
    ),
    # An int branch yields 41 while selected and int na otherwise; na uses 1.
    "header": _Case(
        _source('''unitsFor(bool selected) => if selected
    41
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # Moving the if below the arrow preserves 41, int na and the fallback 1.
    "block": _Case(
        _source('''unitsFor(bool selected) =>
    if selected
        41
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # Both int arms return their value: 41 for true and 9 for false.
    "header_with_else": _Case(
        _source('''unitsFor(bool selected) => if selected
    41
else
    9
'''),
        "int unitsFor(",
        "41 41\n9 9\n41 41\n9 9\n",
    ),
    # A bool receiver selects the same int 41 / int na result as a function.
    "method": _Case(
        _source(
            '''method unitsFor(bool selected) => if selected
    41
''',
            setup="selected = bar_index % 20 < 10\n",
            candidate="selected.unitsFor()",
        ),
        "int _udt_bool_unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # A fractional branch is a float: 41.5 on true, float na on false.
    "float": _Case(
        _source('''unitsFor(bool selected) => if selected
    41.5
'''),
        "double unitsFor(",
        "41.5 41.5\nna 1\n41.5 41.5\nna 1\n",
    ),
    # Missing strings are empty here, so the blank value selects fallback 1.
    "string": _Case(
        _source(
            '''unitsFor(bool selected) => if selected
    "ready"
''',
            units="na(candidate) ? 1 : str.length(candidate)",
        ),
        "std::string unitsFor(",
        "ready 5\n 1\nready 5\n 1\n",
    ),
    # Pine v6 bool has no na: the unmatched branch is false, choosing 1.
    "bool": _Case(
        _source(
            '''unitsFor(bool selected) => if selected
    true
''',
            units="candidate ? 41 : 1",
        ),
        "bool unitsFor(",
        "1 41\n0 1\n1 41\n0 1\n",
    ),
    # The second statement returns 19 + 22 = 41; an unmatched if is int na.
    "two_statements": _Case(
        _source('''unitsFor(bool selected) => if selected
    subtotal = 19
    subtotal + 22
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # na() sees the call's int na, so the ternary replaces it with int 1.
    "call_in_ternary": _Case(
        _source(
            '''unitsFor(bool selected) => if selected
    41
''',
            candidate=(
                "na(unitsFor(bar_index % 20 < 10)) ? 1 "
                ": unitsFor(bar_index % 20 < 10)"
            ),
        ),
        "int unitsFor(",
        "41 41\n1 1\n41 41\n1 1\n",
    ),
    # A one-line ternary already returns its selected int arm, 41 or 9.
    "single_expression": _Case(
        _source("unitsFor(bool selected) => selected ? 41 : 9\n"),
        "int unitsFor(",
        "41 41\n9 9\n41 41\n9 9\n",
    ),
    # Forwarding a nested helper preserves its int 41 and int na values.
    "nested_call": _Case(
        _source('''inner(bool selected) => if selected
    41
unitsFor(bool selected) => inner(selected)
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # Both helpers use conditional tails; each unmatched path is int na.
    "nested_conditional_calls": _Case(
        _source('''inner(bool selected) => if selected
    41
unitsFor(bool selected) => if selected
    inner(selected)
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
    # The outer arm is always taken; its inner unmatched arm still yields na.
    "nested_if": _Case(
        _source('''unitsFor(bool selected) => if true
    if selected
        41
'''),
        "int unitsFor(",
        "41 41\nna 1\n41 41\nna 1\n",
    ),
}


_VALUE_DRIVER = r'''
#include <iostream>
#include <type_traits>
template <typename T>
void print_value(const T& value) {
    if constexpr (std::is_same_v<T, std::string>) {
        std::cout << value;
    } else if constexpr (std::is_same_v<T, bool>) {
        std::cout << value;
    } else if (is_na(value)) {
        std::cout << "na";
    } else {
        std::cout << value;
    }
}
int main() {
    Bar bars[40];
    for (int i = 0; i < 40; ++i) {
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    }
    for (int count : {7, 17, 27, 37}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        print_value(strategy.candidate);
        std::cout << " " << strategy.units << "\n";
    }
}
'''


@pytest.mark.parametrize("name", _CASES)
def test_conditional_function_body_compiles_with_its_value_type(name: str) -> None:
    case = _CASES[name]
    cpp = transpile(case.source)
    compile_cpp(cpp, label=name)
    assert case.signature in cpp


@pytest.mark.parametrize("name", _CASES)
@pytest.mark.parametrize("opt", ("-O0", "-O2"))
def test_conditional_function_body_returns_its_branch_value(name: str, opt: str) -> None:
    case = _CASES[name]
    cpp = transpile(case.source)
    assert run_emitted_tu(cpp, _VALUE_DRIVER, opt=opt, label=name) == case.expected


_ORDER_DRIVER = r'''
#include <iostream>
int main() {
    Bar bars[40];
    for (int i = 0; i < 40; ++i) {
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    }
    for (int count : {7, 17, 27, 37}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.live_position_size() << "\n";
    }
}
'''


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        # Entries at bars 5/25 use 35; closes at bars 15/35 leave no position.
        ("header_with_comment", "35\n0\n35\n0\n"),
        # Entries at bars 5/25 use 41; closes at bars 15/35 leave no position.
        ("header", "41\n0\n41\n0\n"),
    ),
)
def test_bound_function_result_sets_order_quantity(name: str, expected: str) -> None:
    cpp = transpile(_CASES[name].source)
    compile_cpp(cpp, label=name)
    assert run_emitted_tu(cpp, _ORDER_DRIVER, opt="-O2", label=name) == expected


def test_header_and_block_conditionals_emit_identical_cpp() -> None:
    # One blank line aligns the source-line tokens of the following orders.
    header_source = _HEADER + "\n" + _CASES["header"].source.removeprefix(_HEADER)
    header_cpp = transpile(header_source)
    block_cpp = transpile(_CASES["block"].source)
    compile_cpp(header_cpp, label="header conditional")
    compile_cpp(block_cpp, label="block conditional")
    assert header_cpp == block_cpp

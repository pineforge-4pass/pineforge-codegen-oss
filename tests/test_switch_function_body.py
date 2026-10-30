"""Switch function bodies share statement-tail typing and returns."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import FuncDef, MethodDef, SwitchStmt
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = '''//@version=6
strategy("Syntax probe", overlay=true, initial_capital=1000000)
'''


def _source(
    definition: str,
    *,
    setup: str = "",
    call: str = "f(bar_index % 3)",
    quantity: str = "q",
) -> str:
    return (
        _HEADER + definition + setup
        + f"q = {call}\n"
        + 'if bar_index % 20 == 5\n'
        + f'    strategy.entry("L", strategy.long, qty={quantity})\n'
        + 'if bar_index % 20 == 15\n'
        + '    strategy.close("L")\n'
    )


@dataclass(frozen=True)
class _Case:
    source: str
    signature: str
    expected: str


_CASES = {
    # Selectors 0, 1 and 2 choose 1.0, 2.0 and default 3.0, repeated twice.
    "selector_header": _Case(
        _source('''f(int x) => switch x
    0 => 1.0
    1 => 2.0
    => 3.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # For x = 0/1/2, neither test / x > 0 / x > 1 selects 1.0/2.0/3.0.
    "conditions_header": _Case(
        _source('''f(x) => switch
    x > 1 => 3.0
    x > 0 => 2.0
    => 1.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # An indented selector switch chooses the same 1.0, 2.0 and default 3.0.
    "selector_block": _Case(
        _source('''f(int x) =>
    switch x
        0 => 1.0
        1 => 2.0
        => 3.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # An indented condition switch also maps x = 0/1/2 to 1.0/2.0/3.0.
    "conditions_block": _Case(
        _source('''f(x) =>
    switch
        x > 1 => 3.0
        x > 0 => 2.0
        => 1.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # Receivers 0, 1 and 2 select 1.0, 2.0 and default 3.0 through a method.
    "selector_method": _Case(
        _source(
            '''method f(int x) => switch x
    0 => 1.0
    1 => 2.0
    => 3.0
''',
            setup="selected = bar_index % 3\n",
            call="selected.f()",
        ),
        "double _udt_int_f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # The method's ordered conditions map receivers 0/1/2 to 1.0/2.0/3.0.
    "conditions_method": _Case(
        _source(
            '''method f(int x) => switch
    x > 1 => 3.0
    x > 0 => 2.0
    => 1.0
''',
            setup="selected = bar_index % 3\n",
            call="selected.f()",
        ),
        "double _udt_int_f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # Selectors 0, 1 and 2 choose the literal strings one, two and three.
    "string": _Case(
        _source(
            '''f(int x) => switch x
    0 => "one"
    1 => "two"
    => "three"
''',
            quantity="str.length(q)",
        ),
        "std::string f(",
        '"one"\n"two"\n"three"\n"one"\n"two"\n"three"\n',
    ),
    # Arm tails compute 4-3 = 1.0, (5+1)/3 = 2.0 and 8-5 = 3.0.
    "block_arms": _Case(
        _source('''f(int x) => switch x
    0 =>
        first = 4.0
        first -= 3.0
        first
    1 =>
        middle = 5.0
        middle += 1.0
        middle / 3.0
    =>
        third = 8.0
        third - 5.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # Comma-separated arm tails compute 4-3 = 1.0, 6/3 = 2.0 and 8-5 = 3.0.
    "comma_arms": _Case(
        _source('''f(int x) => switch x
    0 => first = 4.0, first - 3.0
    1 => middle = 6.0, middle / 3.0
    => third = 8.0, third - 5.0
'''),
        "double f(",
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n",
    ),
    # Selectors 0/1 return float 1.0/2.0; selector 2 has no arm and is float na.
    "float_without_default": _Case(
        _source(
            '''f(int x) => switch x
    0 => 1.0
    1 => 2.0
''',
            quantity="na(q) ? 1 : q",
        ),
        "double f(",
        "1.0\n2.0\nna\n1.0\n2.0\nna\n",
    ),
    # Selectors 0/1 return int 1/2; selector 2 has no arm and is int na.
    "int_without_default": _Case(
        _source(
            '''f(int x) => switch x
    0 => 1
    1 => 2
''',
            quantity="na(q) ? 1 : q",
        ),
        "int f(",
        "1\n2\nna\n1\n2\nna\n",
    ),
    # For x = 0/1 the conditions return one/two; x = 2 yields empty string na.
    "string_without_default": _Case(
        _source(
            '''f(int x) => switch
    x == 0 => "one"
    x == 1 => "two"
''',
            quantity="na(q) ? 1 : str.length(q)",
        ),
        "std::string f(",
        '"one"\n"two"\n""\n"one"\n"two"\n""\n',
    ),
    # Both matched arms yield true; unmatched x = 2 yields Pine v6 bool false.
    "bool_without_default": _Case(
        _source(
            '''f(int x) => switch
    x == 0 => true
    x == 1 => true
''',
            quantity="q ? 1 : 2",
        ),
        "bool f(",
        "true\ntrue\nfalse\ntrue\ntrue\nfalse\n",
    ),
}


_VALUE_DRIVER = r'''
#include <iomanip>
#include <iostream>
#include <type_traits>
template <typename T>
void print_value(const T& value) {
    if constexpr (std::is_same_v<T, std::string>) {
        std::cout << std::quoted(value);
    } else if constexpr (std::is_same_v<T, bool>) {
        std::cout << std::boolalpha << value;
    } else if (is_na(value)) {
        std::cout << "na";
    } else {
        std::cout << value;
    }
}
int main() {
    Bar bars[6];
    for (int i = 0; i < 6; ++i) {
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    }
    std::cout << std::fixed << std::setprecision(1);
    for (int count : {1, 2, 3, 4, 5, 6}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        print_value(strategy.q);
        std::cout << "\n";
    }
}
'''


@pytest.mark.parametrize("name", _CASES)
def test_switch_function_body_keeps_statement_tail(name: str) -> None:
    source = _CASES[name].source
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    functions = [
        node for node in program.body if isinstance(node, (FuncDef, MethodDef))
    ]
    assert len(functions) == 1  # Each source defines exactly one callable.
    function = functions[0]
    assert not function.is_single_expr  # A selection uses statement-tail returns.
    assert len(function.body) == 1  # The switch is the entire callable body.
    assert isinstance(function.body[0], SwitchStmt)


@pytest.mark.parametrize("name", _CASES)
def test_switch_function_body_compiles_with_its_value_type(name: str) -> None:
    case = _CASES[name]
    cpp = transpile(case.source)
    compile_cpp(cpp, label=name)
    assert case.signature in cpp


@pytest.mark.parametrize("name", _CASES)
@pytest.mark.parametrize("opt", ("-O0", "-O2"))
def test_switch_function_body_returns_its_arm_value(name: str, opt: str) -> None:
    case = _CASES[name]
    cpp = transpile(case.source)
    assert run_emitted_tu(cpp, _VALUE_DRIVER, opt=opt, label=name) == case.expected


_ORDER_DRIVER = r'''
#include <iostream>
int main() {
    Bar bars[60];
    for (int i = 0; i < 60; ++i) {
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    }
    for (int count : {7, 17, 27, 37, 47, 57}) {
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


@pytest.mark.parametrize("name", ("selector_header", "conditions_header"))
def test_switch_function_result_sets_order_quantity(name: str) -> None:
    cpp = transpile(_CASES[name].source)
    # Entries at 5/25/45 use 3/2/1 and fill one bar later; closes leave zero.
    assert run_emitted_tu(cpp, _ORDER_DRIVER, opt="-O2", label=name) == (
        "3\n0\n2\n0\n1\n0\n"
    )


@pytest.mark.parametrize(
    ("header", "block"),
    (
        ("selector_header", "selector_block"),
        ("conditions_header", "conditions_block"),
    ),
)
def test_header_and_block_switches_emit_identical_cpp(header: str, block: str) -> None:
    # A blank line aligns the arm and call locations with the indented form.
    header_source = _HEADER + "\n" + _CASES[header].source.removeprefix(_HEADER)
    assert transpile(header_source) == transpile(_CASES[block].source)


_ASSIGNMENT_SOURCE = '''//@version=6
strategy("Switch assignment", overlay=true, initial_capital=1000000)
q = switch bar_index % 3
    0 => 1.0
    1 => 2.0
    => 3.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
'''


def test_switch_assignment_compiles_and_returns_its_arm_value() -> None:
    cpp = transpile(_ASSIGNMENT_SOURCE)
    compile_cpp(cpp, label="switch assignment")
    # Assignment selectors 0/1/2 choose 1.0/2.0/3.0, repeated on bars 3/4/5.
    assert run_emitted_tu(cpp, _VALUE_DRIVER, opt="-O2", label="switch assignment") == (
        "1.0\n2.0\n3.0\n1.0\n2.0\n3.0\n"
    )

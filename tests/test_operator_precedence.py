"""Operator binding and constant remainder behavior."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import (
    BinOp,
    FuncCall,
    Identifier,
    NumberLiteral,
    UnaryOp,
)
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests.test_runtime_var_initialization import _compile_and_run


def _parse_expr(source: str):
    return Parser(Lexer(source + "\n").tokenize())._parse_expression()


def _script(body: str) -> str:
    return '//@version=6\nstrategy("operator precedence")\n' + body


def test_not_binds_before_each_comparison_level():
    node = _parse_expr("not x > 5")

    assert isinstance(node, BinOp)
    assert node.op == ">"
    assert isinstance(node.left, UnaryOp)
    assert node.left.op == "not"
    assert isinstance(node.left.operand, Identifier)
    assert node.left.operand.name == "x"
    assert isinstance(node.right, NumberLiteral)
    assert node.right.value == 5


def test_not_call_and_equality_use_the_unary_operand():
    equality = _parse_expr("not a == b")
    call = _parse_expr("not math.sign(close - open) > 0")

    assert isinstance(equality, BinOp)
    assert equality.op == "=="
    assert isinstance(equality.left, UnaryOp)
    assert isinstance(equality.left.operand, Identifier)
    assert equality.left.operand.name == "a"

    assert isinstance(call, BinOp)
    assert call.op == ">"
    assert isinstance(call.left, UnaryOp)
    assert isinstance(call.left.operand, FuncCall)


def test_relational_comparisons_bind_inside_equality_comparisons():
    equality_after_relation = _parse_expr("a > b == c")
    equality_before_relation = _parse_expr("a == b > c")

    assert equality_after_relation.op == "=="
    assert isinstance(equality_after_relation.left, BinOp)
    assert equality_after_relation.left.op == ">"
    assert equality_before_relation.op == "=="
    assert isinstance(equality_before_relation.right, BinOp)
    assert equality_before_relation.right.op == ">"


def test_same_level_comparison_chains_remain_left_associative():
    equality = _parse_expr("a == b == c")
    relation = _parse_expr("a < b < c")

    assert equality.op == "=="
    assert isinstance(equality.left, BinOp)
    assert equality.left.op == "=="
    assert relation.op == "<"
    assert isinstance(relation.left, BinOp)
    assert relation.left.op == "<"


@pytest.mark.parametrize(
    "body",
    [
        (
            "float x = close - open\n"
            "value = not (x > 5)\n"
        ),
        (
            "bool flip = close > open\n"
            "value = not flip\n"
        ),
        (
            "float a = close\n"
            "float b = open\n"
            "value = a > b and close < high\n"
        ),
        (
            "bool flag = close > open\n"
            "bool a = close > open\n"
            "bool b = open > low\n"
            "value = (a == b) == flag\n"
        ),
        (
            "float x = close\n"
            "float y = open\n"
            "value = x == y\n"
        ),
        (
            'string s = "a"\n'
            'value = s == "a"\n'
        ),
        "value = na(close)\n",
        (
            "float a = close\n"
            "float b = open\n"
            "value = a != b\n"
        ),
    ],
)
def test_existing_operator_controls_compile(body: str):
    cpp = transpile(_script(body))
    compile_cpp(cpp, label="operator control")


_CHAIN_SOURCE = _script(
    "bool a = true\n"
    "float b = close\n"
    "float c = open\n"
    "signal = a == b > c ? 1.0 : -2.0\n"
    "tradeQty = na(signal) ? 0.75 : "
    "(signal > 0 ? 1.25 : signal < 0 ? 2.5 : 3.75) "
    "+ math.abs(signal) / (1.0 + math.abs(signal))\n"
    "if bar_index == 0\n"
    '    strategy.entry("L", strategy.long, qty = tradeQty)\n'
)


def test_equality_and_relation_chain_books_the_boolean_branch_size():
    cpp = transpile(_CHAIN_SOURCE)
    compile_cpp(cpp, label="comparison chain")
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {{10.0, 11.0, 9.0, 11.0, 1.0, 60000LL}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) return 1;
    std::cout << std::setprecision(17) << strategy.live_position_size() << "\n";
}
'''
    # true == (11 > 10) selects 1.0, then 1.25 + 1 / 2 equals 1.75.
    assert _compile_and_run(cpp + driver) == "1.75\n"


_REMAINDER_SOURCE = _script(
    "float literal_int_negative = -7 % 3\n"
    "float literal_int_divisor = 7 % -3\n"
    "float literal_float_negative = -7.5 % 2\n"
    "float literal_positive = 7 % 3\n"
    "const int negative_int = -7\n"
    "const int positive_int = 3\n"
    "const int positive_seven = 7\n"
    "const int negative_three = -3\n"
    "const float negative_float = -7.5\n"
    "const float positive_float = 2.0\n"
    "float alias_int_negative = negative_int % positive_int\n"
    "float alias_int_divisor = positive_seven % negative_three\n"
    "float alias_float_negative = negative_float % positive_float\n"
    "float alias_positive = positive_int % positive_float\n"
)


def test_constant_remainder_keeps_the_dividend_sign():
    cpp = transpile(_REMAINDER_SOURCE)
    compile_cpp(cpp, label="constant remainder")
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {{1.0, 1.0, 1.0, 1.0, 1.0, 60000LL}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) return 1;
    std::cout << std::setprecision(17)
              << strategy.literal_int_negative << " "
              << strategy.literal_int_divisor << " "
              << strategy.literal_float_negative << " "
              << strategy.literal_positive << " "
              << strategy.alias_int_negative << " "
              << strategy.alias_int_divisor << " "
              << strategy.alias_float_negative << " "
              << strategy.alias_positive << "\n";
}
'''
    observed = tuple(
        float(value) for value in _compile_and_run(cpp + driver).split()
    )
    assert observed == (
        -1.0,  # -7 divided by 3 leaves a remainder of -1.
        1.0,   # 7 divided by -3 leaves a remainder of 1.
        -1.5,  # -7.5 divided by 2 leaves a remainder of -1.5.
        1.0,   # 7 divided by 3 leaves a remainder of 1.
        -1.0,  # The constant integer aliases keep the dividend sign.
        1.0,   # Reversing the integer dividend keeps the remainder positive.
        -1.5,  # Constant floating aliases use the same remainder rule.
        1.0,   # A positive floating dividend keeps the positive remainder.
    )

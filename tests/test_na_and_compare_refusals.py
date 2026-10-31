"""Located diagnostics for invalid proven operator type combinations."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


def _script(body: str) -> str:
    return '//@version=6\nstrategy("operator checks")\n' + body


def _assert_diagnostic(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    line: int,
    col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="operator_checks.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == arguments
    assert diagnostic.location.file == "operator_checks.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "source, line, col, operand_type",
    [
        (
            _script(
                "float x = close - open\n"
                "value = not x > 5\n"
            ),
            4,
            13,  # x starts after "value = not ".
            "float",
        ),
        (
            _script(
                "float a = close\n"
                "float b = open\n"
                "value = not a == b\n"
            ),
            5,
            13,  # a is the operand of not.
            "float",
        ),
        (
            _script("value = not math.sign(close - open) > 0\n"),
            3,
            13,  # math.sign(...) is the numeric operand of not.
            "float",
        ),
        (
            _script("value = not 1 > 0\n"),
            3,
            13,  # The integer literal is the operand of not.
            "int",
        ),
    ],
)
def test_not_rejects_proven_numeric_operands(
    source: str, line: int, col: int, operand_type: str
) -> None:
    _assert_diagnostic(
        source,
        "PF-E2048",
        f"The not operator requires a bool operand; found {operand_type}.",
        {"certain_type": operand_type},
        line,
        col,
    )


@pytest.mark.parametrize(
    "source, line, col, operator",
    [
        (
            _script(
                "bool a = true\n"
                "float b = close\n"
                "float c = open\n"
                "value = a > b == c\n"
            ),
            6,
            18,  # c is the numeric right operand of the equality.
            "==",
        ),
        (
            _script("value = close == open == close[1]\n"),
            3,
            26,  # close[1] is the numeric right operand of the equality.
            "==",
        ),
        (
            _script("value = 1 == close > open\n"),
            3,
            9,  # 1 is the numeric left operand of the equality.
            "==",
        ),
    ],
)
def test_equality_rejects_proven_bool_numeric_pairs(
    source: str, line: int, col: int, operator: str
) -> None:
    _assert_diagnostic(
        source,
        "PF-E2049",
        f"Cannot compare a bool value with a numeric value using {operator}.",
        {"op": operator},
        line,
        col,
    )


def test_direct_na_equality_reports_the_value_operand() -> None:
    _assert_diagnostic(
        _script("value = close == na\n"),
        "PF-E2050",
        'Cannot compare a value to "na" directly. Use the "na()" function instead.',
        {},
        3,
        9,  # close is the value being compared to na.
    )


def test_consecutive_not_reports_the_second_keyword() -> None:
    _assert_diagnostic(
        _script("bool choice = not not flip\n"),
        "PF-E2051",
        '"not" cannot be used as a variable or function name.',
        {},
        3,
        19,  # The second not begins after "bool choice = not ".
    )


def test_string_ordering_reports_the_first_string_operand() -> None:
    _assert_diagnostic(
        _script(
            'string a = "a"\n'
            'string b = "b"\n'
            "value = a < b\n"
        ),
        "PF-E2052",
        "Cannot order string values with <.",
        {"op": "<"},
        5,
        9,  # a is the first operand of the ordered comparison.
    )


@pytest.mark.parametrize(
    "body",
    [
        (
            "float x = close - open\n"
            "value = not (x > 5)\n"
        ),
        "bool flip = close > open\nvalue = not flip\n",
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
        "value = timeframe.multiplier * 1 > 0\n",
    ],
)
def test_valid_operator_controls_remain_accepted(body: str) -> None:
    cpp = transpile(_script(body))
    compile_cpp(cpp, label="accepted operator control")

"""Native regressions for composing requested helper-argument history.

The expected values come from the synthetic requested closes, not another
transpilation. These cases need the paired built engine. They do not provide
TradingView evidence.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from tests.test_security_helper_arg_history import (
    _HOUR_MS,
    _HOURLY_OPENS,
    _assert_completed,
    _assert_values,
    _difference,
    _hourly_closes,
    _run_requested,
    _sma,
)


_REPORTER = """//@version=6
strategy('Helper history')
f(x) => x[2] - x
g(v) => v * 2
a = ta.sma(close, 3)
b = f(a)
c = request.security(syminfo.tickerid, '60', g(b))
if c > 0
    strategy.entry('Long', strategy.long)
"""

_SCALAR_CASES = [
    pytest.param("b", "identity", "c > 0", id="direct-control"),
    pytest.param("b * 2", "twice", "c > 0", id="arithmetic-control"),
    pytest.param("b > 0", "positive", "c", id="comparison-control"),
    pytest.param("g(b)", "twice", "c > 0", id="user-positional"),
    pytest.param("g(v=b)", "twice", "c > 0", id="user-named"),
    pytest.param("math.sign(b)", "sign", "c > 0", id="builtin-positional"),
    pytest.param("math.sign(x=b)", "sign", "c > 0", id="builtin-named"),
    pytest.param("b > 0 ? 1.0 : 0.0", "positive", "c > 0", id="ternary"),
]

_COMPOSED_CASES = _SCALAR_CASES[3:]

# The one-hour closes are 9,3,12,12,6, so a one-slot difference at length 1
# is na,+6,-9,0,+6. Intrahour closes also change the provisional values.
_VARIED_CHILDREN = (
    (7.0, 11.0, 8.0, 9.0),
    (6.0, 2.0, 5.0, 3.0),
    (8.0, 15.0, 10.0, 12.0),
    (10.0, 13.0, 14.0, 12.0),
    (9.0, 5.0, 8.0, 6.0),
)


def _compose(value: float, operation: str) -> float:
    if operation == "positive":
        # A v6 comparison with na is false; it never propagates numeric na.
        return float(value > 0)
    if math.isnan(value):
        return math.nan
    if operation == "identity":
        return value
    if operation == "twice":
        return value * 2
    if operation == "sign":
        return float((value > 0) - (value < 0))
    raise AssertionError(operation)


def _expected(closes: list[float], operation: str, length=1, offset=1):
    return [
        _compose(value, operation)
        for value in _difference(_sma(closes, length), offset)
    ]


def _reporter(payload: str, condition: str) -> str:
    # The positional user-call row is the reporter's exact input.
    return (
        _REPORTER.replace("'60', g(b))", f"'60', {payload})")
        .replace("if c > 0", f"if {condition}")
    )


def _varied_source(payload: str, condition: str) -> str:
    return (
        _reporter(payload, condition)
        .replace("x[2]", "x[1]")
        .replace("ta.sma(close, 3)", "ta.sma(close, 1)")
        .replace(
            f"'60', {payload})",
            f"'60', {payload}, lookahead=barmerge.lookahead_on)",
        )
    )


def _feeds():
    return {
        "chart15": (15, _VARIED_CHILDREN),
        "chart30": (30, tuple((row[1], row[3]) for row in _VARIED_CHILDREN)),
        "chart60": (60, tuple((row[-1],) for row in _VARIED_CHILDREN)),
    }


def _assert_varied_rows(evaluations, operation: str) -> None:
    closes = [children[-1] for children in _VARIED_CHILDREN]
    for rows in evaluations.values():
        completed = [row for row in rows if row.complete]
        assert [row.timestamp for row in completed] == [
            i * _HOUR_MS for i in range(len(closes))
        ], rows
        _assert_values([row.open for row in completed], list(_HOURLY_OPENS))
        _assert_values([row.close for row in completed], closes)
        _assert_values([row.value for row in completed], _expected(closes, operation))
        for row in rows:
            # Only this requested slot's current close may be provisional.
            hour = row.timestamp // _HOUR_MS
            assert 0 <= hour < len(closes), row
            prefix = closes[:hour] + [row.close]
            _assert_values([row.value], [_expected(prefix, operation)[-1]])

    # Confirm that the finer chart actually exercised repeated evaluations.
    counts = [len(evaluations[tag]) for tag in ("chart15", "chart30", "chart60")]
    assert counts[0] > counts[1] > counts[2], counts


@pytest.mark.parametrize("payload,value_expression", [
    pytest.param("[close, close * 2]", "_req_sec_0_0", id="tuple-literal"),
    pytest.param("pair(close)", "std::get<0>(_req_sec_0)", id="tuple-helper"),
])
def test_tuple_observer_controls(payload, value_expression):
    source = f"""//@version=6
strategy("requested tuple observer control")
pair(float v) => [v, v * 2]
[firstValue, secondValue] = request.security(syminfo.tickerid, '60', {payload})
plot(firstValue + secondValue)
"""
    _assert_completed(
        _run_requested(transpile(source), value_expression=value_expression)["chart15"],
        _hourly_closes(),
    )


@pytest.mark.parametrize("payload,operation,condition", _SCALAR_CASES)
def test_reporter_compositions_use_requested_history(payload, operation, condition):
    cpp = transpile(_reporter(payload, condition))
    expected = _expected(_hourly_closes(), operation, length=3, offset=2)
    _assert_completed(_run_requested(cpp)["chart15"], expected)


@pytest.mark.parametrize("payload,operation,condition", _COMPOSED_CASES)
def test_compositions_preserve_sign_zero_and_provisional_slots(payload, operation, condition):
    cpp = transpile(_varied_source(payload, condition))
    _assert_varied_rows(_run_requested(cpp, _feeds()), operation)


@pytest.mark.parametrize("payload,value_template", [
    pytest.param(
        "[b, g(b), math.sign(b), b > 0 ? 1.0 : 0.0]",
        "_req_sec_0_{element}",
        id="tuple-literal",
    ),
    pytest.param(
        "pack(b)", "std::get<{element}>(_req_sec_0)", id="tuple-helper-positional"
    ),
    pytest.param(
        "pack(v=b)", "std::get<{element}>(_req_sec_0)", id="tuple-helper-named"
    ),
])
@pytest.mark.parametrize("element,operation", [
    pytest.param(0, "identity", id="history-element"),
    pytest.param(1, "twice", id="user-call-element"),
    pytest.param(2, "sign", id="builtin-element"),
    pytest.param(3, "positive", id="ternary-element"),
])
def test_tuple_compositions_preserve_each_requested_element(
    payload, value_template, element, operation
):
    source = f"""//@version=6
strategy("helper history tuple composition")
f(x) => x[1] - x
g(v) => v * 2
pack(float v) => [v, g(v), math.sign(v), v > 0 ? 1.0 : 0.0]
a = ta.sma(close, 1)
b = f(a)
[rawValue, twiceValue, signValue, positiveValue] = request.security(
  syminfo.tickerid, '60', {payload}, lookahead=barmerge.lookahead_on)
if positiveValue > 0
    strategy.entry("Long", strategy.long)
plot(rawValue + twiceValue + signValue)
"""
    cpp = transpile(source)
    _assert_varied_rows(
        _run_requested(
            cpp, _feeds(), value_expression=value_template.format(element=element)
        ),
        operation,
    )

"""Requested argument history across eager and lazy expression edges.

These independent synthetic value checks need the built engine. TradingView
validation of newly accepted expressions is a separate landing requirement.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from tests.test_security_helper_arg_history import (
    _HOUR_MS,
    _HOURLY_OPENS,
    _assert_sampled_sites,
    _assert_values,
    _run_requested,
    _source,
)


# Both directions of a provisional condition change are intentional. A global
# sample must keep advancing even when its consumer skips the final value.
_CHILDREN = (
    (3.0, 9.0, 5.0, 7.0),
    (8.0, 4.0, 2.0, 3.0),
    (5.0, 8.0, 10.0, 9.0),
    (11.0, 5.0, 6.0, 4.0),
    (5.0, 8.0, 11.0, 10.0),
)


def _feeds():
    return {
        "chart15": (15, _CHILDREN),
        "chart30": (30, tuple((row[1], row[3]) for row in _CHILDREN)),
        "chart60": (60, tuple((row[-1],) for row in _CHILDREN)),
    }


def _previous_completed_sample(hour):
    return _CHILDREN[hour - 1][-1] - _HOURLY_OPENS[hour - 1] if hour else math.nan


def _evaluations(cpp):
    _assert_sampled_sites(cpp, 1)
    evaluations = _run_requested(cpp, _feeds())
    for rows in evaluations.values():
        completed = [row for row in rows if row.complete]
        assert [row.timestamp for row in completed] == [
            hour * _HOUR_MS for hour in range(len(_CHILDREN))]
        _assert_values([row.open for row in completed], list(_HOURLY_OPENS))
        _assert_values([row.close for row in completed], [row[-1] for row in _CHILDREN])
        assert all(0 <= row.timestamp // _HOUR_MS < len(_CHILDREN) for row in rows)
    assert len(evaluations["chart15"]) > len(evaluations["chart30"]) > len(evaluations["chart60"])
    return evaluations


@pytest.mark.parametrize("selected,scale,replacement", [
    pytest.param("b", 1, None, id="direct"),
    pytest.param("twice(b)", 2, None, id="user-positional"),
    pytest.param("twice(v=b)", 2, None, id="user-named"),
    pytest.param("nz(b, -200.0)", 1, -200.0, id="builtin"),
])
def test_global_history_advances_even_when_a_consumer_skips_its_value(selected, scale, replacement):
    cpp = transpile(_source(
        "f(x) => x[1] - x\ntwice(v) => v + v\nb = f(close - open)\n",
        f"close > 6.0 ? {selected} : -100.0",
        lookahead="on",
    ))
    for rows in _evaluations(cpp).values():
        for row in rows:
            hour = row.timestamp // _HOUR_MS
            if row.close <= 6.0:
                expected = -100.0
            else:
                previous = _previous_completed_sample(hour)
                expected = scale * (previous - (row.close - _HOURLY_OPENS[hour]))
                if replacement is not None and math.isnan(expected):
                    expected = replacement
            _assert_values([row.value], [expected])


@pytest.mark.parametrize("payload", [
    "close > 6.0 and b > 0",
    "not (close <= 6.0 or b <= 0)",
])
def test_short_circuit_reads_preserve_the_globals_requested_clock(payload):
    cpp = transpile(_source(
        "f(x) => x[1] - x\nb = f(close - open)\n", payload, lookahead="on"))
    for rows in _evaluations(cpp).values():
        for row in rows:
            hour = row.timestamp // _HOUR_MS
            difference = _previous_completed_sample(hour) - (
                row.close - _HOURLY_OPENS[hour])
            if "not" in payload:
                # A comparison with numeric na is false in v6.
                expected = row.close > 6.0 and not (difference <= 0)
            else:
                expected = row.close > 6.0 and difference > 0
            _assert_values([row.value], [float(expected)])


def test_alias_and_builtin_reads_share_the_global_initializers_sample():
    cpp = transpile(_source(
        "f(x) => x[1] - x\nb = f(close - open)\naliasValue = b * 2\n",
        "close > 8.0 ? aliasValue + nz(b) : -300.0",
        lookahead="on",
    ))
    for rows in _evaluations(cpp).values():
        for row in rows:
            hour = row.timestamp // _HOUR_MS
            if row.close <= 8.0:
                expected = -300.0
            else:
                expected = 3 * (
                    _previous_completed_sample(hour) - (row.close - _HOURLY_OPENS[hour]))
            _assert_values([row.value], [expected])


def test_argument_is_evaluated_before_a_linear_helpers_branches_and_repeated_reads():
    cpp = transpile(_source(
        "f(x) => x[1] - x\n"
        "selectValue(v) =>\n"
        "    result = 0.0\n"
        "    if close > open\n"
        "        result := v + v\n"
        "    else\n"
        "        result := -v\n"
        "    result\n",
        "selectValue(v=f(close - open))",
        lookahead="on",
    ))
    for rows in _evaluations(cpp).values():
        for row in rows:
            hour = row.timestamp // _HOUR_MS
            previous = _previous_completed_sample(hour)
            difference = previous - (row.close - _HOURLY_OPENS[hour])
            expected = 2 * difference if row.close > row.open else -difference
            _assert_values([row.value], [expected])


@pytest.mark.parametrize("payload", [
    "twice(nz(f(close - open), -200.0))",
    "nz(twice(v=f(close - open)), -400.0)",
])
def test_nested_eager_user_and_builtin_arguments_sample_the_written_call_once(payload):
    cpp = transpile(_source(
        "f(x) => x[1] - x\ntwice(v) => v + v\n", payload,
        lookahead="on",
    ))
    for rows in _evaluations(cpp).values():
        for row in rows:
            hour = row.timestamp // _HOUR_MS
            difference = _previous_completed_sample(hour) - (
                row.close - _HOURLY_OPENS[hour])
            expected = -400.0 if math.isnan(difference) else 2 * difference
            _assert_values([row.value], [expected])

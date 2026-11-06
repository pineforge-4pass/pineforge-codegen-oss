"""Written calls to one history helper must retain distinct runtime results.

The eager outer-call case composes two sampled arguments; the ordinary
arithmetic control uses exactly the same calls and synthetic requested bars.
Expected values come only from those input bars and the written Pine formula.
These native tests require the paired built engine; they supply no TV evidence.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from tests.test_security_helper_arg_history import (
    _HOUR_MS,
    _HOURLY_OPENS,
    _assert_values,
    _run_requested,
    _source,
)


_CHILDREN = (
    (3.0, 9.0, 5.0, 7.0),
    (8.0, 4.0, 2.0, 3.0),
    (5.0, 8.0, 10.0, 9.0),
    (11.0, 5.0, 6.0, 4.0),
    (5.0, 8.0, 11.0, 10.0),
)

_DEFINITIONS = (
    "f(float x) => x[1] - x\n"
    "combine(float leftValue, float rightValue) => leftValue + 3.0 * rightValue\n"
)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            "f(close - open) + 3.0 * f(2.0 * close - open)",
            id="ordinary-control",
        ),
        pytest.param(
            "combine(f(close - open), f(2.0 * close - open))",
            id="eager-two-argument-composition",
        ),
    ],
)
def test_two_written_history_calls_keep_distinct_results(payload):
    # f's two argument sequences, then x[1] - x, computed from input bars.
    closes = [children[-1] for children in _CHILDREN]
    first_arguments = [
        close - open_ for open_, close in zip(_HOURLY_OPENS, closes)
    ]
    second_arguments = [
        2.0 * close - open_ for open_, close in zip(_HOURLY_OPENS, closes)
    ]
    first_results = [
        previous - current
        for previous, current in zip([math.nan] + first_arguments[:-1], first_arguments)
    ]
    second_results = [
        previous - current
        for previous, current in zip([math.nan] + second_arguments[:-1], second_arguments)
    ]
    # Every finite pair differs: sharing by function name changes the value.
    assert all(left != right for left, right in zip(first_results[1:], second_results[1:]))
    expected = [left + 3.0 * right for left, right in zip(first_results, second_results)]

    cpp = transpile(_source(_DEFINITIONS, payload, lookahead="on"))
    rows = _run_requested(cpp, {"chart15": (15, _CHILDREN)})["chart15"]
    completed = [row for row in rows if row.complete]
    assert [row.timestamp for row in completed] == [
        hour * _HOUR_MS for hour in range(len(_CHILDREN))
    ], rows
    _assert_values([row.open for row in completed], list(_HOURLY_OPENS))
    _assert_values([row.close for row in completed], closes)

    # No emission-shape assertion precedes native execution. A cache mutant
    # must reach this independent VALUE comparison, not fail a sampling pin.
    for hour, (row, wanted) in enumerate(zip(completed, expected)):
        detail = (
            f"requested two-call VALUE at hour {hour}: actual={row.value!r}, "
            f"expected={wanted!r}, first={first_results[hour]!r}, "
            f"second={second_results[hour]!r}; payload={payload}"
        )
        if math.isnan(wanted):
            assert math.isnan(row.value), detail
        else:
            assert row.value == pytest.approx(wanted), detail

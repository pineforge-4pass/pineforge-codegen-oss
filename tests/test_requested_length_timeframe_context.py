"""Requested TA lengths read timeframe metadata from the requested context."""

from __future__ import annotations

import math

import pytest

from tests._e2e import (
    Build,
    assert_run_stopped_with,
    chart_feed_head,
    execute_all,
    ok,
    same,
    skip_unless_e2e_env,
)


BARS = 1500

SMA_REQUESTED = """//@version=6
strategy("requested SMA timeframe")
x = request.security(syminfo.tickerid, "D", ta.sma(close, timeframe.isdaily ? 5 : 3))
// @pf-trace out=x
plot(x)
"""

SMA_LITERAL_5 = """//@version=6
strategy("requested SMA literal five")
x = request.security(syminfo.tickerid, "D", ta.sma(close, 5))
// @pf-trace out=x
plot(x)
"""

SMA_LITERAL_3 = """//@version=6
strategy("requested SMA literal three")
x = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
// @pf-trace out=x
plot(x)
"""

SMA_MULTIPLIER = """//@version=6
strategy("requested SMA multiplier")
x = request.security(syminfo.tickerid, "D", ta.sma(close, timeframe.multiplier == 1 ? 5 : 3))
// @pf-trace out=x
plot(x)
"""

EMA_ZERO_CHART_ARM = """//@version=6
strategy("requested EMA zero chart arm")
x = request.security(syminfo.tickerid, "D", ta.ema(close, timeframe.isdaily ? 0 : 3))
plot(x)
"""

EMA_ZERO_LITERAL = """//@version=6
strategy("requested EMA zero literal")
x = request.security(syminfo.tickerid, "D", ta.ema(close, 0))
plot(x)
"""

EMA_ZERO_REQUESTED_ARM = """//@version=6
strategy("requested EMA zero requested arm")
x = request.security(syminfo.tickerid, "D", ta.ema(close, timeframe.isdaily ? 3 : 0))
// @pf-trace out=x
plot(x)
"""

EMA_THREE_LITERAL = """//@version=6
strategy("requested EMA literal three")
x = request.security(syminfo.tickerid, "D", ta.ema(close, 3))
// @pf-trace out=x
plot(x)
"""


def _column(outcome, name: str) -> dict[int, float]:
    return {
        record["bar_index"]: record["value"]
        for record in outcome.traces["default"]
        if record["name"] == name
    }


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("requested_length_timeframe_context")
    feed = chart_feed_head(engine, base, BARS)
    builds = {
        "sma-requested": Build(SMA_REQUESTED, trace=True),
        "sma-literal-5": Build(SMA_LITERAL_5, trace=True),
        "sma-literal-3": Build(SMA_LITERAL_3, trace=True),
        "sma-multiplier": Build(SMA_MULTIPLIER, trace=True),
        "ema-zero-chart-arm": Build(EMA_ZERO_CHART_ARM),
        "ema-zero-literal": Build(EMA_ZERO_LITERAL),
        "ema-zero-requested-arm": Build(EMA_ZERO_REQUESTED_ARM, trace=True),
        "ema-three-literal": Build(EMA_THREE_LITERAL, trace=True),
    }
    return execute_all(engine, feed, base / "runs", builds)


def test_direct_requested_timeframe_length_matches_requested_literal(runs):
    subject = ok(runs, "sma-requested")
    five = ok(runs, "sma-literal-5")
    three = ok(runs, "sma-literal-3")
    subject_values = _column(subject, "out")
    five_values = _column(five, "out")
    three_values = _column(three, "out")

    compared = [
        (bar, subject_values[bar], five_values[bar])
        for bar in sorted(subject_values)
        if math.isfinite(subject_values[bar]) and math.isfinite(five_values[bar])
    ]
    assert compared
    assert all(same(actual, expected) for _bar, actual, expected in compared)

    differs_from_three = [
        bar
        for bar in sorted(subject_values)
        if math.isfinite(subject_values[bar])
        and math.isfinite(three_values[bar])
        and not same(subject_values[bar], three_values[bar])
    ]
    assert differs_from_three


def test_direct_requested_timeframe_multiplier_uses_requested_context(runs):
    subject = ok(runs, "sma-multiplier")
    five = ok(runs, "sma-literal-5")
    subject_values = _column(subject, "out")
    five_values = _column(five, "out")
    compared = [
        (bar, subject_values[bar], five_values[bar])
        for bar in sorted(subject_values)
        if math.isfinite(subject_values[bar]) and math.isfinite(five_values[bar])
    ]
    assert compared
    assert all(same(actual, expected) for _bar, actual, expected in compared)


def test_direct_requested_timeframe_zero_is_checked_in_requested_context(runs):
    message = (
        "Invalid value of the 'length' argument (0) in the 'ema' function. "
        "It must be > 0."
    )
    assert_run_stopped_with(
        runs, "ema-zero-chart-arm", "pine_invalid_argument", message
    )
    assert_run_stopped_with(
        runs, "ema-zero-literal", "pine_invalid_argument", message
    )


def test_direct_requested_timeframe_reversed_zero_arm_runs_like_literal(runs):
    outcome = ok(runs, "ema-zero-requested-arm")
    control = ok(runs, "ema-three-literal")
    subject_values = _column(outcome, "out")
    control_values = _column(control, "out")
    compared = [
        (bar, subject_values[bar], control_values[bar])
        for bar in sorted(subject_values)
        if math.isfinite(subject_values[bar]) and math.isfinite(control_values[bar])
    ]
    assert compared
    assert all(same(actual, expected) for _bar, actual, expected in compared)

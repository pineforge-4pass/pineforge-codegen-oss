"""TradingView half-away-from-zero rounding reaches constant TA lengths."""

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
INVALID_LENGTH_MESSAGE = (
    "Invalid value of the 'length' argument (0) in the 'ema' function. "
    "It must be > 0."
)


SMA_ROUNDED = """//@version=6
strategy("round length SMA")
x = ta.sma(close, math.round(9 / 2))
// @pf-trace x=x
plot(x)
"""

SMA_FIVE = """//@version=6
strategy("SMA five")
x = ta.sma(close, 5)
// @pf-trace x=x
plot(x)
"""

SMA_FOUR = """//@version=6
strategy("SMA four")
x = ta.sma(close, 4)
// @pf-trace x=x
plot(x)
"""

SMA_LARGE_POWER = """//@version=6
strategy("SMA with rounded power")
x = ta.sma(close, math.round(math.pow(2, 60)) % 7 + 1)
two = ta.sma(close, 2)
// @pf-trace x=x
// @pf-trace two=two
plot(x)
"""

EMA_ROUNDED = """//@version=6
strategy("round length EMA")
x = ta.ema(close, math.round(2.5))
// @pf-trace x=x
plot(x)
"""

EMA_THREE = """//@version=6
strategy("EMA three")
x = ta.ema(close, 3)
// @pf-trace x=x
plot(x)
"""

EMA_TWO = """//@version=6
strategy("EMA two")
x = ta.ema(close, 2)
// @pf-trace x=x
plot(x)
"""

EMA_ROUNDED_ZERO = """//@version=6
strategy("rounded zero length")
x = ta.ema(close, 1 - math.round(0.5))
// @pf-trace x=x
plot(x)
"""

EMA_LITERAL_ZERO = """//@version=6
strategy("literal zero length")
x = ta.ema(close, 0)
// @pf-trace x=x
plot(x)
"""

ROUND_VALUES = """//@version=6
strategy("round ties")
r1 = math.round(1.25, 1)
r2 = math.round(2.5)
r3 = math.round(-2.5)
r4 = math.round(-0.5)
r5 = math.round(0.125, 2)
r6 = math.round(2.675, 2)
r7 = math.round(4.5)
// @pf-trace r1=r1
// @pf-trace r2=r2
// @pf-trace r3=r3
// @pf-trace r4=r4
// @pf-trace r5=r5
// @pf-trace r6=r6
// @pf-trace r7=r7
plot(r1 + r2 + r3 + r4 + r5 + r6 + r7)
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("constant_math_round_length")
    feed = chart_feed_head(engine, base, BARS)
    return execute_all(
        engine,
        feed,
        base / "runs",
        {
            "sma-rounded": Build(SMA_ROUNDED, trace=True),
            "sma-five": Build(SMA_FIVE, trace=True),
            "sma-four": Build(SMA_FOUR, trace=True),
            "sma-large-power": Build(SMA_LARGE_POWER, trace=True),
            "ema-rounded": Build(EMA_ROUNDED, trace=True),
            "ema-three": Build(EMA_THREE, trace=True),
            "ema-two": Build(EMA_TWO, trace=True),
            "ema-rounded-zero": Build(EMA_ROUNDED_ZERO, trace=True),
            "ema-literal-zero": Build(EMA_LITERAL_ZERO, trace=True),
            "round-values": Build(ROUND_VALUES, trace=True),
        },
    )


def _trace(outcome, name: str) -> list[float]:
    records = [record for record in outcome.traces["default"] if record["name"] == name]
    assert [record["bar_index"] for record in records] == list(range(BARS))
    return [record["value"] for record in records]


def _assert_equal_where_defined(left: list[float], right: list[float]) -> None:
    compared = 0
    for actual, expected in zip(left, right):
        assert same(actual, expected)
        if math.isfinite(actual) and math.isfinite(expected):
            compared += 1
    assert compared > 0


def _assert_differs(left: list[float], right: list[float]) -> None:
    assert any(
        math.isfinite(actual) and math.isfinite(expected) and not same(actual, expected)
        for actual, expected in zip(left, right)
    )


def test_sma_rounds_nine_halves_to_five(runs):
    rounded = _trace(ok(runs, "sma-rounded"), "x")
    five = _trace(ok(runs, "sma-five"), "x")
    four = _trace(ok(runs, "sma-four"), "x")
    _assert_equal_where_defined(rounded, five)
    _assert_differs(rounded, four)


def test_sma_length_preserves_exact_rounded_power(runs):
    outcome = ok(runs, "sma-large-power")
    rounded = _trace(outcome, "x")
    _assert_equal_where_defined(rounded, _trace(outcome, "two"))
    _assert_differs(rounded, _trace(ok(runs, "sma-five"), "x"))


def test_ema_rounds_two_point_five_to_three(runs):
    rounded = _trace(ok(runs, "ema-rounded"), "x")
    three = _trace(ok(runs, "ema-three"), "x")
    two = _trace(ok(runs, "ema-two"), "x")
    _assert_equal_where_defined(rounded, three)
    _assert_differs(rounded, two)


def test_ema_rounded_zero_stops_like_literal_zero(runs):
    assert_run_stopped_with(
        runs, "ema-rounded-zero", "pine_invalid_argument", INVALID_LENGTH_MESSAGE,
    )
    assert_run_stopped_with(
        runs, "ema-literal-zero", "pine_invalid_argument", INVALID_LENGTH_MESSAGE,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("r1", 1.3),
        ("r2", 3),
        ("r3", -3),
        ("r4", -1),
        ("r5", 0.13),
        ("r6", 2.68),
        ("r7", 5),
    ),
)
def test_round_values_match_tradingview_capture(runs, name, expected):
    values = _trace(ok(runs, "round-values"), name)
    assert all(value == expected for value in values)

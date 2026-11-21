"""Compound actuals keep their grouping across callable TA lengths."""

from __future__ import annotations

import re

import pytest

from tests._e2e import (
    Build, assert_run_stopped_with, chart_feed_head, execute_all, ok, same,
    skip_unless_e2e_env,
)


INVALID_LENGTH_MESSAGE = (
    "Invalid value of the 'length' argument (0) in the 'ema' function. "
    "It must be > 0."
)


def _trace(outcome, name: str) -> list[dict]:
    return [record for record in outcome.traces["default"] if record["name"] == name]


def _assert_same_trace(outcomes, subject_key: str, control_key: str,
                       name: str) -> None:
    subject = _trace(ok(outcomes, subject_key), name)
    control = _trace(ok(outcomes, control_key), name)
    assert len(subject) == len(control) > 0, (subject_key, control_key, name)
    mismatches = [
        (left["bar_index"], left["value"], right["value"])
        for left, right in zip(subject, control)
        if left["timestamp"] != right["timestamp"]
        or not same(left["value"], right["value"])
    ]
    assert not mismatches, f"{subject_key} differs from {control_key}: {mismatches[:3]}"


def _assert_trace_differs(outcomes, subject_key: str, control_key: str,
                          name: str) -> None:
    subject = _trace(ok(outcomes, subject_key), name)
    control = _trace(ok(outcomes, control_key), name)
    assert len(subject) == len(control) > 0, (subject_key, control_key, name)
    assert any(
        left["timestamp"] != right["timestamp"]
        or not same(left["value"], right["value"])
        for left, right in zip(subject, control)
    ), f"{subject_key} unexpectedly equals {control_key}"


def _ctor_periods(cpp: str) -> list[str]:
    members = re.findall(r"(_ta_sma_\d+(?:_[A-Za-z0-9]+)*)\(", cpp)
    periods: list[str] = []
    for member in members:
        match = re.search(rf"\b{re.escape(member)}\(([^)]*)\)", cpp)
        assert match is not None
        periods.append(match.group(1).strip())
    return periods


def _builds() -> dict[str, Build]:
    return {
        "compound": Build(
            """//@version=6
strategy("forwarded compound")
f(simple int n) => ta.sma(close, 2 * n)
x = f(1 + 2)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "compound-control-6": Build(
            """//@version=6
strategy("forwarded compound control 6")
x = ta.sma(close, 6)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "compound-control-4": Build(
            """//@version=6
strategy("forwarded compound control 4")
x = ta.sma(close, 4)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "unary-conditional": Build(
            """//@version=6
strategy("forwarded unary conditional")
f(simple int n) => ta.sma(close, 2 * n)
unary = f(-(-3))
conditional = f(true ? 3 : 1)
// @pf-trace unary=unary
// @pf-trace conditional=conditional
plot(unary + conditional)
""",
            trace=True,
        ),
        "unary-conditional-control": Build(
            """//@version=6
strategy("forwarded unary conditional control")
unary = ta.sma(close, 6)
conditional = ta.sma(close, 6)
// @pf-trace unary=unary
// @pf-trace conditional=conditional
plot(unary + conditional)
""",
            trace=True,
        ),
        "invalid-compound": Build(
            """//@version=6
strategy("forwarded invalid compound")
f(simple int n) => ta.ema(close, 2 * n)
x = f(2 - 2)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "invalid-control": Build(
            """//@version=6
strategy("forwarded invalid control")
f(simple int n) => ta.ema(close, 2 * n)
x = f(0)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "two-callables": Build(
            """//@version=6
strategy("forwarded through two callables")
f(simple int n) => ta.sma(close, 2 * n)
g(simple int m) => f(m + 1)
x = g(1 + 1)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "two-callables-control": Build(
            """//@version=6
strategy("forwarded through two callables control")
x = ta.sma(close, 6)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "keyword-default": Build(
            """//@version=6
strategy("forwarded keyword default")
f(simple int n = 1 + 2) => ta.sma(close, 2 * n)
keyword = f(n = 1 + 2)
omitted = f()
// @pf-trace keyword=keyword
// @pf-trace omitted=omitted
plot(keyword + omitted)
""",
            trace=True,
        ),
        "keyword-default-control": Build(
            """//@version=6
strategy("forwarded keyword default control")
keyword = ta.sma(close, 6)
omitted = ta.sma(close, 6)
// @pf-trace keyword=keyword
// @pf-trace omitted=omitted
plot(keyword + omitted)
""",
            trace=True,
        ),
        "method": Build(
            """//@version=6
strategy("forwarded method compound")
method f(float s, simple int n) => ta.sma(s, 2 * n)
x = close.f(1 + 2)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "method-control": Build(
            """//@version=6
strategy("forwarded method compound control")
x = ta.sma(close, 6)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "atom-literal": Build(
            """//@version=6
strategy("forwarded atom literal")
f(simple int n) => ta.sma(close, 2 * n)
x = f(3)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
        "atom-input": Build(
            """//@version=6
strategy("forwarded atom input")
f(simple int n) => ta.sma(close, 2 * n)
n = input.int(3, "Length")
x = f(n)
// @pf-trace x=x
plot(x)
""",
            trace=True,
        ),
    }


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("callable_ta_forwarded_compound")
    feed = chart_feed_head(engine, base, 1500)
    return execute_all(engine, feed, base / "runs", _builds())


def test_binary_actual_keeps_the_ta_length_grouped(runs):
    _assert_same_trace(runs, "compound", "compound-control-6", "x")
    _assert_trace_differs(runs, "compound", "compound-control-4", "x")


def test_unary_and_conditional_actuals_keep_their_grouping(runs):
    for name in ("unary", "conditional"):
        _assert_same_trace(runs, "unary-conditional",
                           "unary-conditional-control", name)


def test_invalid_compound_actual_stops_like_zero_length_control(runs):
    assert_run_stopped_with(
        runs, "invalid-compound", "pine_invalid_argument",
        INVALID_LENGTH_MESSAGE,
    )
    assert_run_stopped_with(
        runs, "invalid-control", "pine_invalid_argument",
        INVALID_LENGTH_MESSAGE,
    )


def test_nested_callable_actuals_keep_their_grouping(runs):
    _assert_same_trace(runs, "two-callables", "two-callables-control", "x")


def test_keyword_and_omitted_compound_defaults_keep_their_grouping(runs):
    for name in ("keyword", "omitted"):
        _assert_same_trace(runs, "keyword-default",
                           "keyword-default-control", name)


def test_typed_method_actual_keeps_the_ta_length_grouped(runs):
    _assert_same_trace(runs, "method", "method-control", "x")


def test_atom_actuals_keep_the_constructor_text_shape(runs):
    literal_cpp = ok(runs, "atom-literal").transpiled["cpp"]
    input_cpp = ok(runs, "atom-input").transpiled["cpp"]
    assert _ctor_periods(literal_cpp) == ["6"]
    assert _ctor_periods(input_cpp) == ["6"]
    assert not re.search(r"_ta_sma_\d+(?:_[A-Za-z0-9]+)*\(\([0-9]+\)\)", literal_cpp)
    assert not re.search(r"_ta_sma_\d+(?:_[A-Za-z0-9]+)*\(\([0-9]+\)\)", input_cpp)
    for key in ("atom-literal", "atom-input"):
        _assert_same_trace(runs, key, "compound-control-6", "x")

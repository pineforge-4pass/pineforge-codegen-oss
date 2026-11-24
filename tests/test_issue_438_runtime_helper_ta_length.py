"""Runtime daily requests size a helper's SMA from the requested timeframe."""

import math

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, ok, same, skip_unless_e2e_env


LENGTH_PASSED = """//@version=6
strategy("length passed to a function, run-time timeframe")
chosen_tf = bar_index >= 0 ? "D" : str.tostring(bar_index)
average(float sample, simple int span) => ta.sma(sample, span)
x = request.security(syminfo.tickerid, chosen_tf, average(close, timeframe.isdaily ? 5 : 3))
plot(x)
"""

LENGTH_INSIDE = """//@version=6
strategy("TA call in a function, run-time timeframe")
chosen_tf = bar_index >= 0 ? "D" : str.tostring(bar_index)
avg() => ta.sma(close, timeframe.isdaily ? 5 : 3)
x = request.security(syminfo.tickerid, chosen_tf, avg())
plot(x)
"""

LITERAL = """//@version=6
strategy("literal daily SMA")
x = request.security(syminfo.tickerid, "D", ta.sma(close, {length}))
plot(x)
"""

BARS = 3000


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("issue_438")
    feed = chart_feed_head(engine, base, BARS)
    scripts = {
        "length-passed": LENGTH_PASSED,
        "length-inside": LENGTH_INSIDE,
        "daily-five": LITERAL.format(length=5),
        "daily-three": LITERAL.format(length=3),
    }
    builds = {
        name: Build(source + "// @pf-trace out=x\n", trace=True)
        for name, source in scripts.items()
    }
    return execute_all(engine, feed, base / "runs", builds)


def _values(runs, key):
    records = ok(runs, key).traces["default"]
    out = [record for record in records if record["name"] == "out"]
    assert [record["bar_index"] for record in out] == list(range(BARS)), key
    return [record["value"] for record in out]


@pytest.mark.parametrize("subject", ["length-passed", "length-inside"])
def test_runtime_helper_sma_length_matches_daily_five(runs, subject):
    actual = _values(runs, subject)
    five = _values(runs, "daily-five")
    three = _values(runs, "daily-three")
    assert any(
        math.isfinite(a) and math.isfinite(b) and a != b
        for a, b in zip(five, three, strict=True)
    ), "the feed must distinguish the two SMA lengths after warmup"
    for bar, (value, expected) in enumerate(zip(actual, five, strict=True)):
        assert same(value, expected), (subject, bar, value, expected)

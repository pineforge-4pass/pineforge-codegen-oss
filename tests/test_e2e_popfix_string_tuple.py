"""A user function's string tuple elements bind as strings.

``[tr, wt] = f_state()`` with ``f_state() => [t, w]`` of strings bound both
names as ``double`` (tuple bindings kept only the bool family), so
``tr == wt`` cast a ``std::string`` to double and ``f_score(tr)`` typed its
``state == "BULL"`` parameter double ("double == string"). String elements of
a user function's tuple now bind as strings; its other families keep the
historical double storage.

Replayed end to end beside a reference that spells the two strings as plain
variables: identical traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix string tuple", overlay = true)\n'
TAIL = """
model = tr == wt ? "Align" : "Mixed"
if model == "Align" and score > 0
    strategy.entry("L", strategy.long)
if model == "Mixed"
    strategy.close("L")
// @pf-trace score=score
// @pf-trace align=model == "Align" ? 1 : 0
"""
SHAPE = HEAD + """
f_state() =>
    t = close > open ? "BULL" : "BEAR"
    w = close > close[1] ? "BULL" : "NEUTRAL"
    [t, w]
f_score(state) =>
    switch state
        "BULL" => 1
        "BEAR" => -1
        => 0
[tr, wt] = f_state()
score = f_score(tr) + f_score(wt)
""" + TAIL
REFERENCE = HEAD + """
tr = close > open ? "BULL" : "BEAR"
wt = close > close[1] ? "BULL" : "NEUTRAL"
score = (tr == "BULL" ? 1 : tr == "BEAR" ? -1 : 0) + (wt == "BULL" ? 1 : wt == "BEAR" ? -1 : 0)
""" + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_string_tuple")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, trace=True), "reference": Build(REFERENCE, trace=True)})


def test_string_tuple_elements_run_like_plain_strings(runs):
    print("string tuple:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))

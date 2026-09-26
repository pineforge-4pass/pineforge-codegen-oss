"""An ``if``/``switch`` whose every arm yields a tuple selects that tuple.

``resolve(x) => if mode == "A" ... tpPoints(x) else [x + 5, x - 5]`` was typed
``double``, so ``_func_ret = tpPoints(x)`` assigned a ``std::tuple`` to a
double ("tuple assigned to double"), and ``[a, b] = switch ...`` was dropped as
``/* unsupported tuple assignment */`` (its names undeclared). The analyzer
now types such a selection as the arms' tuple (``_selection_tuple_shape``);
codegen materializes it -- na in every position when no arm runs -- and
returns or destructures it. ``na(s)`` of a string, which the same scripts
read, tests the empty string PineForge stores for a string na (it had no
``is_na`` overload) and warns that an authored ``""`` reads as na.

Replayed end to end beside a spelled-out reference, each element its own
ternary, at the default mode and an override: identical traces and trades.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile_full
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix tuple selection", overlay = true)\n'
TAIL = """
if u1 > u2 and p1 > 2 and close > sl[1]
    strategy.entry("L", strategy.long)
if close < open and close < tp[1] - 12
    strategy.close("L")
// @pf-trace tp=tp
// @pf-trace sl=sl
// @pf-trace p1=p1
// @pf-trace p2=p2
// @pf-trace u1=u1
// @pf-trace u2=u2
// @pf-trace c1=c1 == #10e0a0 ? 1 : 0
"""
SHAPE = HEAD + """
mode = input.string("Points", "Mode", options = ["Points", "Percent", "Fixed"])
tpPoints(float entry) =>
    tp = entry + 10.0
    sl = entry - 10.0
    [tp, sl]
tpPercent(float entry) =>
    [entry * 1.01, entry * 0.99]
resolve(float entry) =>
    if mode == "Points"
        tpPoints(entry)
    else if mode == "Percent"
        tpPercent(entry)
    else
        [entry + 5, entry - 5]
pick(int k) =>
    switch k
        1 => [1.0, 2.0]
        2 => tpPercent(close)
        => [3.0, 4.0]
[tp, sl] = resolve(close)
k = bar_index % 3
[p1, p2] = pick(k)
[u1, u2] = if close > open
    [high, low]
else
    [low, high]
[c1, c2] = switch
    close > open => [#10e0a0, #ff3860]
    => [#22d3ee, #f43f5e]
""" + TAIL
REFERENCE = HEAD + """
mode = input.string("Points", "Mode", options = ["Points", "Percent", "Fixed"])
tp = mode == "Points" ? close + 10.0 : mode == "Percent" ? close * 1.01 : close + 5
sl = mode == "Points" ? close - 10.0 : mode == "Percent" ? close * 0.99 : close - 5
k = bar_index % 3
p1 = k == 1 ? 1.0 : k == 2 ? close * 1.01 : 3.0
p2 = k == 1 ? 2.0 : k == 2 ? close * 0.99 : 4.0
u1 = close > open ? high : low
u2 = close > open ? low : high
c1 = close > open ? #10e0a0 : #22d3ee
""" + TAIL

STRING_NA = HEAD + """
var string side = na
if na(side) and close > open
    side := "L"
    strategy.entry("L", strategy.long)
else if not na(side) and close < open
    side := na
    strategy.close("L")
"""
STRING_NA_REFERENCE = HEAD + """
var string side = ""
if side == "" and close > open
    side := "L"
    strategy.entry("L", strategy.long)
else if side != "" and close < open
    side := ""
    strategy.close("L")
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_tuple_selection")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, overrides={"Mode": "Percent"}, trace=True),
        "reference": Build(REFERENCE, overrides={"Mode": "Percent"}, trace=True),
        "string_na": Build(STRING_NA),
        "string_na_reference": Build(STRING_NA_REFERENCE),
    })


def test_tuple_selections_run_like_their_elements(runs):
    print("tuple selection:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))


def test_na_of_a_string_reads_the_empty_string(runs):
    print("string na:", assert_same_runs(ok(runs, "string_na"),
                                         ok(runs, "string_na_reference")))
    warnings = [d.message for d in transpile_full(STRING_NA)["diagnostics"]]
    assert any("na() of a string reads the empty string as na" in m for m in warnings)

"""History on an operator expression reads the expression k bars ago.

``(close > ta.ema(close, n))[1]``, ``(high - low)[2]`` and
``(c ? p : q)[1]`` indexed a C++ scalar ("bool[int]" / "double[int]"): only a
call result had a synthetic history Series. Such a subscript now owns one too,
pushed once per evaluation on the chart and once per completed requested bar
inside ``request.security`` (where the inner TA is the requested-context
instance, not the chart's).

Replayed end to end beside the distributed reference --
``close[1] > ta.ema(close, n)[1]`` and so on, on the chart and in a
``lookahead_on`` request -- at the default length and an override: identical
traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix compound history", overlay = true)\n'
TAIL = """
if a and s and b > 0
    strategy.entry("L", strategy.long)
if not a
    strategy.close("L")
// @pf-trace a=a ? 1 : 0
// @pf-trace b=b
// @pf-trace c=c
// @pf-trace n=n
// @pf-trace s=s ? 1 : 0
// @pf-trace s2=s2
"""
SHAPE = HEAD + """
len = input.int(10, "Len")
a = (close > ta.ema(close, len))[1]
b = (high - low)[2]
c = (close > open ? close : open)[1]
n = (-close)[1]
s = request.security(syminfo.tickerid, "60", (close > ta.ema(close, len))[1], lookahead = barmerge.lookahead_on)
s2 = request.security(syminfo.tickerid, "60", (high - low)[1])
""" + TAIL
REFERENCE = HEAD + """
len = input.int(10, "Len")
a = close[1] > ta.ema(close, len)[1]
b = high[2] - low[2]
c = close[1] > open[1] ? close[1] : open[1]
n = -close[1]
s = request.security(syminfo.tickerid, "60", close[1] > ta.ema(close, len)[1], lookahead = barmerge.lookahead_on)
s2 = request.security(syminfo.tickerid, "60", high[1] - low[1])
""" + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_compound_history")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, overrides={"Len": 4}, trace=True),
        "reference": Build(REFERENCE, overrides={"Len": 4}, trace=True),
    })


def test_expression_history_runs_like_its_distributed_reference(runs):
    print("compound history:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))

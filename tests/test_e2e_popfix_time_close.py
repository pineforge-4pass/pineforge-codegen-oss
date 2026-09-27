"""``time_close`` inside ``request.security`` and ``time_close[k]``.

A ``time_close[k]`` read made ``time_close`` a history Series member of that
name, which shadowed the host's ``time_close()`` its value is pushed from --
so the member was never fed and every bare ``time_close`` called the Series
("type Series<int64_t> does not provide a call operator"). The member is now
escaped (``_time_close_``). Inside a request, ``time_close`` is the requested
bar's close on the requested timeframe, and ``time_close[k]`` its completed
requested bars' history, where it used to read the chart's.

On the corpus's 24x7 ETH feed a bar closes one interval after it opens, so the
shape is replayed beside ``time + 3600000`` (requested hour) and
``time + 900000`` (the 15m chart): identical traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix time_close", overlay = true)\n'
# Before its first completed hour, a requested ``int`` reads the int64 na
# sentinel through its double storage on either spelling; trace from bar 8.
TAIL = """
if bar_index >= 8 and a - b == 3600000 and c > 0
    strategy.entry("L", strategy.long)
if minute(time, "UTC") == 30
    strategy.close("L")
// @pf-trace a=a
// @pf-trace b=bar_index < 8 ? -1 : b
// @pf-trace c=na(c) ? -1 : c
// @pf-trace d=d
"""
SHAPE = HEAD + """
a = request.security(syminfo.tickerid, "60", time_close)
b = request.security(syminfo.tickerid, "60", time_close[1])
c = time_close[1]
d = time_close
""" + TAIL
REFERENCE = HEAD + """
a = request.security(syminfo.tickerid, "60", time) + 3600000
b0 = request.security(syminfo.tickerid, "60", time[1])
b = na(b0) ? na : b0 + 3600000
c = na(time[1]) ? na : time[1] + 900000
d = time + 900000
""" + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_time_close")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, trace=True), "reference": Build(REFERENCE, trace=True)})


def test_time_close_runs_like_open_plus_interval(runs):
    print("time_close:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))

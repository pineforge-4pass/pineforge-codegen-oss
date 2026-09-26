"""A ``request.security`` timeframe computed by a user function.

``tf = tfFromLabel(choice)`` then ``request.security(sym, tf, ...)`` crashed
the transpiler (``'CodeGen' object has no attribute '_all_member_names'``):
the registration-time timeframe rendered the user call before the member-name
set it consults existed. The set now precedes the security metadata, and the
function is called at registration with the input's override-aware value.

Replayed end to end beside the same selection spelled as a ternary, at the
default choice and an override: identical traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix udf timeframe", overlay = true)\n'
TAIL = """
[m, s] = request.security(syminfo.tickerid, tf, [ta.ema(close, 5), ta.sma(close, 20)])
if m > s
    strategy.entry("L", strategy.long)
else
    strategy.close("L")
// @pf-trace m=m
// @pf-trace s=s
"""
SHAPE = HEAD + """
tfLabel = input.string("1hr", "TF", options = ["1hr", "4hr"])
tfFromLabel(lbl) =>
    switch lbl
        "1hr" => "60"
        "4hr" => "240"
        => "60"
tf = tfFromLabel(tfLabel)
""" + TAIL
REFERENCE = HEAD + """
tfLabel = input.string("1hr", "TF", options = ["1hr", "4hr"])
tf = tfLabel == "1hr" ? "60" : tfLabel == "4hr" ? "240" : "60"
""" + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_udf_timeframe")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, overrides={"TF": "4hr"}, trace=True),
        "reference": Build(REFERENCE, overrides={"TF": "4hr"}, trace=True),
    })


def test_function_timeframe_runs_like_the_ternary(runs):
    print("udf timeframe:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))

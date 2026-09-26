"""A function ending in a void builtin returns no value.

``goLong() => strategy.entry(...) ; strategy.exit(...)`` was emitted as a
``double`` method ending in ``return strategy_exit(...)``: C++ that does not
compile ("void value not ignored"). Every builtin Pine types void -- the order
calls, ``strategy.risk.*``, ``log.*`` and ``runtime.error`` -- is now emitted
as a statement, also at the end of an ``if``/``switch`` arm, and the function
returns its default. ``strategy.risk.*`` last in a function used to crash the
transpiler with a ValueError.

The shape is replayed end to end beside its spelled-out reference, the same
calls inlined at the call sites: identical trades.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix void tail", overlay = true)\n'
SHAPE = HEAD + """
goLong() =>
    strategy.entry("L", strategy.long)
    strategy.exit("XL", from_entry = "L", limit = close * 1.004, stop = close * 0.996)
goShort() =>
    strategy.entry("S", strategy.short)
    strategy.exit("XS", from_entry = "S", limit = close * 0.996, stop = close * 1.004)
flat() =>
    strategy.close_all()
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
if ta.crossover(fast, slow)
    goLong()
if ta.crossunder(fast, slow)
    goShort()
if hour(time, "UTC") == 0 and minute(time, "UTC") == 0
    flat()
"""
REFERENCE = HEAD + """
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
if ta.crossover(fast, slow)
    strategy.entry("L", strategy.long)
    strategy.exit("XL", from_entry = "L", limit = close * 1.004, stop = close * 0.996)
if ta.crossunder(fast, slow)
    strategy.entry("S", strategy.short)
    strategy.exit("XS", from_entry = "S", limit = close * 0.996, stop = close * 1.004)
if hour(time, "UTC") == 0 and minute(time, "UTC") == 0
    strategy.close_all()
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_void_tail")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE), "reference": Build(REFERENCE)})


def test_void_tail_trades_like_the_inlined_calls(runs):
    print("void tail:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))


def test_every_void_builtin_ends_a_function_or_arm():
    cpp = transpile(HEAD + """
logIt() =>
    log.info("x")
fail() =>
    if close < 0
        runtime.error("negative close")
cancelAll() =>
    strategy.cancel_all()
risk() =>
    strategy.risk.allow_entry_in(strategy.direction.long)
logIt()
fail()
cancelAll()
risk()
if close > open
    strategy.entry("L", strategy.long)
""")
    assert "return strategy_" not in cpp and "return pine_log_info" not in cpp
    assert "_func_ret = pine_runtime_error" not in cpp
    compile_cpp(cpp, label="void tails")

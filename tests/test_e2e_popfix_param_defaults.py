"""User-function parameter defaults, keyword gaps, and a block's na handle.

``cell(t, c, r, txt, color clr = #DBDBDB, align = text.align_right,
color bg = na, float h = 0)`` called as ``cell(t, 0, r, "x", align = ...,
h = 0.5)`` passed too few arguments and shifted ``h`` into ``bg``
("no matching function for call to cell"): omitted parameters now take their
declared defaults (codegen, the analyzer's typing and TA lengths alike) and
keywords land on their own parameters. The same scripts declare
``bx = cond ? box.new(...) : na`` inside an ``if`` block: that hoisted
declaration now carries its handle type, so the ``na`` arm is ``Box{}``, not
``na<double>()`` ("incompatible operand types Box and double").

Replayed end to end: the defaulted calls beside the same calls with every
argument written (identical traces and trades), and the block-drawing script
beside its drawing-free twin (identical trades).
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("popfix param defaults", overlay = true)\n'
DEFAULTS_HEAD = HEAD + """
score(float src, int len = 14, float mult = 1.0, bool invert = false) =>
    v = ta.sma(src, len) * mult
    invert ? -v : v
lag(src = close, n = 2) =>
    src[n]
"""
DEFAULTS_TAIL = """
if c < 0 and d > e and a > 0
    strategy.entry("L", strategy.long)
if d < e
    strategy.close("L")
// @pf-trace a=a
// @pf-trace b=b
// @pf-trace c=c
// @pf-trace d=d
// @pf-trace e=e
"""
SHAPE = DEFAULTS_HEAD + """
a = score(close)
b = score(close, mult = 2.0)
c = score(high, 5, invert = true)
d = lag()
e = lag(open, n = 1)
""" + DEFAULTS_TAIL
REFERENCE = DEFAULTS_HEAD + """
a = score(close, 14, 1.0, false)
b = score(close, 14, 2.0, false)
c = score(high, 5, 1.0, true)
d = lag(close, 2)
e = lag(open, 1)
""" + DEFAULTS_TAIL

TRADES = """
if ta.crossover(ta.ema(close, 5), ta.ema(close, 20))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 5), ta.ema(close, 20))
    strategy.close("L")
"""
DRAWING = HEAD + """
showZones = input.bool(true, "Show zones")
type Level
    float price
    box zone
    line mid
var levels = array.new<Level>()
if close > open
    float px = high
    bx = showZones ? box.new(bar_index - 1, px, bar_index, px - 1.0, bgcolor = color.new(color.red, 80)) : na
    ml = showZones ? line.new(bar_index - 1, px, bar_index, px, color = color.red) : na
    array.push(levels, Level.new(px, bx, ml))
    if array.size(levels) > 20
        array.shift(levels)
""" + TRADES


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_param_defaults")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, trace=True),
        "reference": Build(REFERENCE, trace=True),
        "drawing": Build(DRAWING),
        "drawing_reference": Build(HEAD + TRADES),
    })


def test_defaulted_calls_run_like_written_arguments(runs):
    print("param defaults:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))


def test_block_drawing_ternary_runs_like_no_drawing(runs):
    print("block drawing:", assert_same_runs(ok(runs, "drawing"),
                                             ok(runs, "drawing_reference")))

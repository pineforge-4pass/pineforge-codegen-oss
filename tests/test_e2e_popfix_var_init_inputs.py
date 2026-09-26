"""A ``var`` collection sized from an input is built after the inputs are read.

``var bool[] holding = array.new_bool(gridLines, false)`` with ``gridLines =
input.int(20, ...)``: on_bar built a global first-bar ``var`` array, matrix,
map or UDT in the ``_var_initialized`` latch, which ran before the
``_inputs_initialized_`` block assigned the input members, so the initializer
read the member's zero and the array was empty -- "Index 0 is out of bounds.
Array size is 0" at the first ``array.get`` (the grid bots job-2352 / 2674 /
2696 / 2758, 13 run-error rows). TradingView requires constant input
defaults, so the inputs read nothing the latch builds; they are now read
before it.

The shapes -- an array and a matrix sized from inputs and a UDT built from
one -- are replayed end to end beside a reference that builds the same state in
the body on the first bar, at the defaults and under overrides: identical
traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = """//@version=6
strategy("popfix var init order", overlay = true)
n = input.int(5, "Levels")
step = input.float(0.5, "Step")
type Grid
    int count
    float step
"""
TAIL = """
array.set(levels, bar_index % n, close)
avg = array.avg(levels)
cell = matrix.get(grid, n - 1, 1)
if close > avg and cell == step and g.count == n
    strategy.entry("L", strategy.long)
if close < avg
    strategy.close("L")
// @pf-trace size=array.size(levels)
// @pf-trace avg=avg
// @pf-trace rows=matrix.rows(grid)
// @pf-trace cell=cell
// @pf-trace count=g.count
// @pf-trace gstep=g.step
"""
SHAPE = HEAD + """
var float[] levels = array.new_float(n, 0.0)
var matrix<float> grid = matrix.new<float>(n, 2, step)
var Grid g = Grid.new(n, step)
""" + TAIL
REFERENCE = HEAD + """
var float[] levels = array.new_float(0)
var matrix<float> grid = matrix.new<float>(0, 0)
var Grid g = Grid.new(0, 0.0)
if array.size(levels) == 0
    for i = 1 to n
        array.push(levels, 0.0)
    grid := matrix.new<float>(n, 2, step)
    g.count := n
    g.step := step
""" + TAIL
OVERRIDES = {"Levels": 8, "Step": 0.25}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_var_init_inputs")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, overrides=OVERRIDES, trace=True),
        "reference": Build(REFERENCE, overrides=OVERRIDES, trace=True),
    })


def test_var_collections_sized_from_inputs_run_like_body_built_state(runs):
    print("var init from inputs:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))

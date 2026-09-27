"""A function's last statement is its value, whatever statement it is.

TradingView returns the value of a function's last statement. The codegen
returned it only for an expression, an ``if`` or a ``switch``; any other
last statement fell through to the default ``return 0.0``. The Gaussian
channel of cs-lev-tradleware-gaussian-channel-stochrsi-eth ends its filter in
``_f := ...``, so the filter was identically 0 and the script never traded
(lane W2, F04: 9 population probes, 0 trades against TradingView's 2 to 551).
The same held for an ``if`` / ``switch`` arm whose last statement was one.

TradingView's own tapes (``fixtures/w2_trio_tv/tail_*``, lab tv --no-note)
pin every shape, and the functions now return them:

- ``x := e`` and each compound ``x op= e``: x's new value, typed as x;
- ``obj.f := e`` / ``obj.f += e``: the field's new value;
- a declaration ``[var] [T] x = e``: the declared variable;
- a tuple declaration ``[p, q] = f()``: the tuple;
- a ``for`` / ``for ... in`` / ``while`` loop: the value its body's last
  statement produced on the last iteration that reached it (a ``break`` or
  ``continue`` before it keeps the previous one), ``na`` when none did; an
  if without else or a switch without default ending the body is ``na``
  after an iteration that ran none of its arms.

Only a scalar or string value is returned: a drawing, UDT or collection
handle keeps the statement-then-default lowering it always compiled to.

A tuple reassignment ``[p, q] := f()`` is a TradingView syntax error
(CE10156), refused here too. Each tape is replayed on the corpus ETH 15m
feed from TradingView's first bar (2025-04-01 00:00 UTC), and the cs-lev
filter shape runs beside the same function with its variable spelled out as
the last line.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, derive_chart_feed,
    execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "w2_trio_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
TAPES = ("tail_reassign", "tail_decl", "tail_var_decl", "tail_tuple_decl",
         "tail_loop", "tail_loop_edges", "tail_loop_if")
# TradingView's run starts at its window; the replay feed starts there too,
# so a var's first-bar value and a series' history agree bar for bar.
WINDOW_START_MS = int(dt.datetime(2025, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
WINDOW_END_MS = int(dt.datetime(2025, 4, 4, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000), row["Signal"])
    return list(trades.values())


def _window_feed(engine: Path, base: Path) -> Path:
    full = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "window.csv"
    with full.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if WINDOW_START_MS <= ts < WINDOW_END_MS:
                out.write(line)
    return feed


@pytest.fixture(scope="module")
def tape_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_tail_tapes")
    feed = _window_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        name: Build((FIXTURES / f"{name}.pine").read_text()) for name in TAPES})
    return engine, base, feed, runs


@pytest.mark.parametrize("name", TAPES)
def test_the_tail_value_replays_the_tradingview_tape(tape_runs, name):
    engine, base, feed, runs = tape_runs
    ok(runs, name)
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, base / name, feed)}
    tape = _tape(name)
    assert len(tape) == 7
    misses = []
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (twin["exit_time"], twin["exit_comment"])
        if got != trade["exit"]:
            misses.append(f"tape {trade['exit']}, engine {got}")
    assert not misses, misses[:3]
    print(f"{name}: {len(tape)}/{len(tape)} TradingView exit comments")


HEAD = '//@version=6\nstrategy("w2 function tail value", overlay = true)\n'
FILTER = """f_filt(float _a, float _s) =>
    float _f = .0
    _f := _a * nz(_s) + (1 - _a) * nz(_f[1])
"""
TAIL = """x = f_filt(0.3, close)
// @pf-trace x=x
if ta.crossover(close, x)
    strategy.entry("L", strategy.long)
if ta.crossunder(close, x)
    strategy.close("L")
"""


@pytest.fixture(scope="module")
def filter_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_tail_filter")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(HEAD + FILTER + TAIL, trace=True),
        "reference": Build(HEAD + FILTER + "    _f\n" + TAIL, trace=True)})


def test_a_filter_ending_in_its_reassignment_returns_it(filter_runs):
    """cs-lev's shape: the filter's history (``_f[1]``) is its own output."""
    shape = ok(filter_runs, "shape")
    summary = assert_same_runs(shape, ok(filter_runs, "reference"))
    assert any(rec["value"] != 0 for rec in shape.traces["default"])
    print("filter:", summary)


def test_a_tuple_reassignment_is_refused_like_tradingview():
    with pytest.raises(CompileError):
        transpile(HEAD + "pair(float v) => [v, v * 2]\n"
                  "f(float v) =>\n    float p = 0.0\n    float q = 0.0\n"
                  "    [p, q] := pair(v)\n")


# A drawing, UDT or collection handle as a function's (or arm's, or loop's)
# last value keeps the statement-then-default lowering: returning it into the
# function's double slot failed the C++ compile (review of c8bb777).
HANDLE_TAILS = HEAD + """type Obj
    float v = 1.5
    array<float> arr
    line ln
f_draw(float y) =>
    var line ln = na
    line.delete(ln)
    ln := line.new(bar_index - 10, y, bar_index, y)
f_redraw(float y) =>
    var label lb = na
    if y > 0
        label.delete(lb)
        lb := label.new(bar_index, y, "x")
setArr(Obj o) =>
    o.arr := array.new_float(3, 0.0)
setLine(Obj o, float y) =>
    o.ln := line.new(bar_index, y, bar_index + 1, y)
mkObj() =>
    Obj o = Obj.new(close)
mkArr() =>
    a = array.new_float(0)
loopArr() =>
    for i = 0 to 1
        a = array.new_float(0)
loopLbl(float x) =>
    for i = 0 to 1
        label lb = label.new(bar_index, x, "a")
buildText(int n) =>
    string s = ""
    for i = 0 to n
        s += str.tostring(i)
pairS(string s, float v) =>
    [s + "x", v * 2]
gS(string s, float v) =>
    [t, d] = pairS(s, v)
var Obj g = Obj.new()
f_draw(high)
f_redraw(low)
setArr(g)
setLine(g, close)
mkObj()
mkArr()
loopArr()
loopLbl(close)
buildText(3)
[t1, d1] = gS("a", close)
if d1 > 0 and str.length(t1) == 2
    strategy.entry("L", strategy.long)
"""


def test_handle_tails_keep_compiling():
    from tests._compile import compile_cpp
    compile_cpp(transpile(HANDLE_TAILS), label="handle tails")


# The value is typed as the assignment's target: a float field or local
# updated by an int right side stays a float, in a function, a method and an
# if arm (review of c8bb777: they compiled as int functions).
TYPED = HEAD + """type Obj
    float v = 1.5
bump(Obj o) =>
    o.v += 1{bump_tail}
method quarter(Obj this, float x) =>
    float y = x
    y /= 4{quarter_tail}
fArm(float v) =>
    float y = v
    if v > 1850
        y += 1{arm_tail}
    else
        y -= 1{arm_tail}
var Obj g = Obj.new()
r1 = bump(g)
r2 = g.quarter(close)
r3 = fArm(close)
// @pf-trace r1=r1
// @pf-trace r2=r2
// @pf-trace r3=r3
if r2 > r2[1]
    strategy.entry("L", strategy.long)
if r3 < r3[1]
    strategy.close("L")
"""


@pytest.fixture(scope="module")
def typed_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_tail_typed")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(TYPED.format(bump_tail="", quarter_tail="", arm_tail=""), trace=True),
        "reference": Build(TYPED.format(bump_tail="\n    o.v", quarter_tail="\n    y",
                                        arm_tail="\n        y"), trace=True)})


def test_tail_values_are_typed_as_their_target(typed_runs):
    shape = ok(typed_runs, "shape")
    summary = assert_same_runs(shape, ok(typed_runs, "reference"))
    first = {rec["name"]: rec["value"] for rec in shape.traces["default"][:3]}
    assert first["r1"] == 2.5, first
    print("typed tails:", summary)

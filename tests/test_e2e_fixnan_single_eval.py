"""``fixnan(x)`` evaluates ``x`` once per execution.

``fixnan(x)`` lowered to ``(is_na(x) ? prev : (prev = x))``: the argument's
C++ appeared twice, so a stateful call inside it ran twice per bar. In
TradingView's DMI/ADX helpers (``plus = fixnan(100 * ta.rma(plusDM, len) /
truerange)``) the RMA stepped twice per bar and ADX inflated: lane W2's F03
(deepwintrader-legend-buy-sell read ADX 29.94 where TradingView's is 21.77 and
booked reversals TradingView never did). The argument is now bound once, the
way ``nz`` binds its own.

The shape runs end to end beside a spelled-out fixnan (a ``var`` holding the
last non-na value): identical per-bar ADX, +DI, -DI and held values, and
identical trades. TradingView's tape of the same helpers
(``fixtures/w2_trio_tv/fixnan_adx``) is replayed on the corpus ETH 15m feed:
after the warm-up every exit comment spells the engine's values.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, derive_chart_feed,
    execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "w2_trio_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone

HEAD = '//@version=6\nstrategy("w2 fixnan single evaluation", overlay = false)\n'
DMI = """
dirmov(len) =>
    up = ta.change(high)
    down = -ta.change(low)
    plusDM = na(up) ? na : (up > down and up > 0 ? up : 0)
    minusDM = na(down) ? na : (down > up and down > 0 ? down : 0)
    truerange = ta.rma(ta.tr, len)
{fix}
    [plus, minus]
adx(dilen, adxlen) =>
    [plus, minus] = dirmov(dilen)
    sum = plus + minus
    100 * ta.rma(math.abs(plus - minus) / (sum == 0 ? 1 : sum), adxlen)
[diPlus, diMinus] = dirmov(14)
sig = adx(14, 14)
"""
FIXNAN = """    plus = fixnan(100 * ta.rma(plusDM, len) / truerange)
    minus = fixnan(100 * ta.rma(minusDM, len) / truerange)"""
SPELLED = """    rawPlus = 100 * ta.rma(plusDM, len) / truerange
    rawMinus = 100 * ta.rma(minusDM, len) / truerange
    var float plus = na
    var float minus = na
    if not na(rawPlus)
        plus := rawPlus
    if not na(rawMinus)
        minus := rawMinus"""
HELD_FIXNAN = 'held = fixnan(ta.rma(close, 5) * (minute(time, "UTC") == 30 ? na : 1.0))\n'
HELD_SPELLED = """rawHeld = ta.rma(close, 5) * (minute(time, "UTC") == 30 ? na : 1.0)
var float held = na
if not na(rawHeld)
    held := rawHeld
"""
TAIL = """// @pf-trace adx=sig
// @pf-trace di_plus=diPlus
// @pf-trace di_minus=diMinus
// @pf-trace held=held
if ta.crossover(diPlus, diMinus) and sig > 25
    strategy.entry("L", strategy.long)
if ta.crossunder(diPlus, diMinus) and sig > 25
    strategy.entry("S", strategy.short)
if held > held[1] * 1.002
    strategy.close_all()
"""
SHAPE = HEAD + DMI.format(fix=FIXNAN) + HELD_FIXNAN + TAIL
REFERENCE = HEAD + DMI.format(fix=SPELLED) + HELD_SPELLED + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_fixnan")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, trace=True), "reference": Build(REFERENCE, trace=True)})


def test_fixnan_traces_and_trades_like_a_spelled_out_fixnan(runs):
    print("fixnan:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000), row["Signal"])
    return list(trades.values())


def _numbers(comment: str) -> list[float]:
    return [float("nan") if part == "NaN" else float(part) for part in comment.split("|")]


def test_fixnan_adx_replays_the_tradingview_tape(tmp_path_factory):
    """TradingView starts its run at the window (2025-04-01) and the corpus
    feed years earlier, so the RMAs agree once their seed has decayed:
    ``(13/14)**n`` is below 1e-15 after five days of 15m bars."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_fixnan_tape")
    feed = derive_chart_feed(engine, base / "chart.csv")
    runs = execute_all(engine, feed, base, {
        "tape": Build((FIXTURES / "fixnan_adx.pine").read_text())})
    ok(runs, "tape")
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, base / "tape", feed)}
    warm = int(dt.datetime(2025, 4, 6, tzinfo=dt.timezone.utc).timestamp() * 1000)
    tape = [t for t in _tape("fixnan_adx") if t["entry"][0] >= warm]
    assert len(tape) >= 100
    agree, misses = 0, []
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (twin["exit_time"], _numbers(twin["exit_comment"]))
        want = (trade["exit"][0], _numbers(trade["exit"][1]))
        if got is not None and got[0] == want[0] and len(got[1]) == len(want[1]) and all(
                abs(a - b) <= 2e-6 for a, b in zip(got[1], want[1])):
            agree += 1
        elif len(misses) < 3:
            misses.append(f"tape {trade['exit'][1]!r}, engine "
                          f"{None if twin is None else twin['exit_comment']!r}")
    assert agree == len(tape), misses
    print(f"fixnan ADX: {agree}/{len(tape)} TradingView exit comments after the warm-up")


def test_fixnan_emits_its_argument_once():
    cpp = transpile(HEAD + "x = fixnan(ta.rma(close, 5) * (bar_index % 3 == 0 ? na : 1.0))\n"
                    "if x > 0\n    strategy.entry(\"L\", strategy.long)\n")
    statement = next(line for line in cpp.splitlines() if "_prev_fixnan_1" in line
                     and ".compute(" in line)
    assert len(re.findall(r"_ta_rma_\d+\.compute\(", statement)) == 1, statement

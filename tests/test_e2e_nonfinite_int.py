"""A Pine ``int`` holding ``math.floor(100 / 0)``, replayed against TradingView.

TradingView keeps +Infinity in an ``int`` a ``math.floor``/``math.ceil``/
``math.round`` of a division by zero fills: ``>``/``>=`` against any number
hold and ``<``/``<=`` do not, while ``==`` and ``!=`` are both false,
``str.tostring`` prints NaN, ``na()`` is true, ``nz(v, -1)`` replaces it, and
``strategy.entry(qty = v)`` trades the default quantity -- as na does. The
codegen narrowed the value into a C++ ``int`` at the assignment, where the
infinity became ``na<int>()`` and ordered as na: ``if shares > 0`` never held
where TradingView entered at the default quantity (the job-2545 MR VWAP SVP
TDV strategy's VWAP reset bars, ``shares = math.floor(riskPerTrade /
riskDiff)``; R5 lane TAIL-C).

A plain global int fed only by those calls and read by an ordering comparison
is now stored as the ``double`` its value already is (``_nonfinite_int_names``);
every read of it keeps the int's old answers -- an infinity reads na -- except
an ordering comparison's operand, which reads the number. TradingView's tape of ``fixtures/nonfinite_int_tv/
int_inf.pine`` is replayed end to end: every leg's entry id and quantity and
every exit comment -- the read-outs ``i=11110000,NaN,true,-1`` and
``n=0000,NaN,true,-1`` -- equal TradingView's.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env


FIXTURES = Path(__file__).parent / "fixtures" / "nonfinite_int_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
# TradingView's range: 2025-04-01 00:00 UTC through the 2025-04-04 00:00 bar.
RANGE_MS = (1743465600000, 1743724800000)


def _tape() -> list[dict]:
    """``entry`` (ms, signal, qty) and ``exit`` (ms, signal) per closed tape
    trade; the range end's trade (an empty exit signal) is left out."""
    trades: dict[str, dict] = {}
    with (FIXTURES / "int_inf_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            ms = int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000)
            trade = trades.setdefault(row["Trade number"], {})
            if row["Type"].startswith("Entry"):
                trade["entry"] = (ms, row["Signal"], float(row["Size (qty)"]))
            else:
                trade["exit"] = (ms, row["Signal"])
    return [t for t in trades.values() if t["exit"][1]]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("nonfinite_int")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            if RANGE_MS[0] <= int(line.split(",", 1)[0]) <= RANGE_MS[1]:
                out.write(line)
    outcomes = execute_all(engine, feed, base, {
        "inf": Build((FIXTURES / "int_inf.pine").read_text()),
    })
    return engine, feed, base, outcomes


def test_every_leg_and_read_out_is_the_tapes(run):
    engine, feed, base, outcomes = run
    ok(outcomes, "inf")
    tape = _tape()
    assert len(tape) == 66
    assert {t["exit"][1] for t in tape} == {"i=11110000,NaN,true,-1|n=0000,NaN,true,-1"}
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, base / "inf", feed)}
    misses = []
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (
            twin["entry_id"], round(twin["qty"], 8), twin["exit_time"], twin["exit_comment"])
        want = (trade["entry"][1], trade["entry"][2], *trade["exit"])
        if got != want:
            misses.append(f"{want} -> {got}")
    assert not misses, "\n".join(misses)
    print("nonfinite int:", len(tape), "trades equal TradingView's")


def test_only_a_floor_fed_global_keeps_the_double():
    """The storage rule's reach: a global int every binding of which is a
    one-argument math.floor/ceil/round, read by an ordering comparison and
    otherwise only by a comparison, na, nz, str.tostring or an entry quantity.
    Anything else is stored and read as before (a name reassigned from another
    value was a double already)."""
    head = ('//@version=6\nstrategy("t")\nz = close - close\n')
    cpp = transpile(head + (
        "shares = math.floor(100 / z)\n"
        "if shares > 0\n"
        "    strategy.entry(\"L\", strategy.long, qty = shares)\n"))
    assert "double shares = 0.0;" in cpp
    assert "shares = std::floor(" in cpp
    # The ordering operand reads the number; the quantity reads na for it.
    assert "auto _pna_l = (shares);" in cpp
    assert "(std::isfinite(shares) ? shares : na<double>())" in cpp
    for body in (
        "shares = math.floor(100 / z)\nstrategy.entry(\"L\", strategy.long, qty = shares)\n",
        "var int shares = na\nshares := math.floor(100 / z)\nplot(shares > 0 ? 1 : 0)\n",
        "shares = math.floor(100 / z)\nplot(shares[1])\n",
        "shares = math.floor(100 / z)\nx = shares + 1\nplot(x > 0 ? 1 : 0)\n",
        "f(int v) => v > 0\nshares = math.floor(100 / z)\nplot(f(shares) ? 1 : 0)\n",
        "shares = math.floor(100 / z)\nshares := 3\nplot(shares > 0 ? 1 : 0)\n",
        "shares = math.floor(100 / z, 2)\nplot(shares > 0 ? 1 : 0)\n",
    ):
        cpp = transpile(head + body)
        assert "std::isfinite(shares) ? shares" not in cpp, body

"""``syminfo.mincontract`` reads the symbol fact the run declares.

It was emitted as ``na<double>()``, so a script sizing to the exchange's lot
-- blitz-locked-macd-pullback-sniper-trend-adx-filtered-strategy's ``step =
syminfo.mincontract > 0 ? syminfo.mincontract : 1.0`` -- floored every
quantity to whole units: sub-unit sizes became 0 and booked no trade on
btcusdt-15/1d, ethusdtp-1d and xauusd-1d, and the other lanes lost the
fraction (lane W2, F06: 8 population probes). It now reads the run's syminfo
metadata key ``"mincontract"`` (``runtime_overrides.syminfo_metadata``, the
channel the lanes already declare ``qty_step`` through) and stays na when the
run declares none.

lab tv --no-note read-outs give TradingView's value per lane: 0.0001 on
BINANCE:ETHUSDT.P, 0.00001 on BINANCE:BTCUSDT, 0.01 on OANDA:EURUSD and
OANDA:XAUUSD, 1 on NASDAQ:AAPL, NYSE:F, NSE:NIFTY, CME_MINI:ES1! and NQ1!.
The ETH one (``fixtures/w2_trio_tv/mincontract_exit``) replays on the corpus
feed with the fact declared: every exit comment and quantity equals
TradingView's.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile_full
from tests._e2e import (
    Build, chart_feed_head, closed_trades, derive_chart_feed, execute_all, ok,
    skip_unless_e2e_env, summary, trade_count,
)


FIXTURES = Path(__file__).parent / "fixtures" / "w2_trio_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
ETH_MINCONTRACT = 0.0001  # TradingView's syminfo.mincontract on BINANCE:ETHUSDT.P


def _declared(value: float) -> dict:
    return {"runtime_overrides": {"syminfo_metadata": {"mincontract": value}}}


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000),
                row["Signal"], float(row["Size (qty)"]))
    return list(trades.values())


@pytest.fixture(scope="module")
def readout(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_mincontract_tape")
    feed = derive_chart_feed(engine, base / "chart.csv")
    runs = execute_all(engine, feed, base, {
        "tape": Build((FIXTURES / "mincontract_exit.pine").read_text(),
                      _declared(ETH_MINCONTRACT))})
    ok(runs, "tape")
    return engine, base / "tape", feed, runs["tape"]


def test_the_declared_mincontract_replays_the_tradingview_tape(readout):
    engine, workdir, feed, _ = readout
    by_entry = {t["entry_time"]: t for t in closed_trades(
        engine, workdir, feed, syminfo_metadata={"mincontract": ETH_MINCONTRACT})}
    tape = _tape("mincontract_exit")
    assert len(tape) == 8
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        assert twin is not None, trade
        got = (twin["exit_time"], twin["exit_comment"], round(twin["qty"], 10))
        assert got == trade["exit"], f"tape {trade['exit']}, engine {got}"
    print(f"mincontract: {len(tape)}/{len(tape)} TradingView exit comments and quantities")


def test_without_a_declared_fact_it_is_na(readout):
    """A run that declares no mincontract reads na: the comments spell NaN
    and the na quantity falls back to the strategy's default of 1."""
    engine, workdir, feed, _ = readout
    trades = closed_trades(engine, workdir, feed)
    assert trades
    assert {t["exit_comment"] for t in trades} == {"mincontract=NaN|floored=NaN"}
    assert {t["qty"] for t in trades} == {1.0}


SIZING = """//@version=6
strategy("w2 mincontract sizing", overlay = true, initial_capital = 100000)
step = {step}
q = math.floor((0.5 + 0.01 * (bar_index % 7)) / step) * step
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow) and q > 0
    strategy.entry("L", strategy.long, qty = q)
if ta.crossunder(fast, slow)
    strategy.close("L")
"""
LOT = "syminfo.mincontract > 0 ? syminfo.mincontract : 1.0"


@pytest.fixture(scope="module")
def sizing_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_mincontract_sizing")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SIZING.format(step=LOT), _declared(0.01)),
        "lot_001": Build(SIZING.format(step="0.01")),
        "lot_1": Build(SIZING.format(step="1.0")),
    })


def test_sizing_to_the_declared_lot_matches_that_lot(sizing_runs):
    """blitz's sizing: the declared 0.01 lot sizes like a literal 0.01, and
    with no fact declared the script's own fallback (1.0) applies."""
    shape = ok(sizing_runs, "shape")
    with_fact = ok(sizing_runs, "lot_001").trades["default"]
    fallback = ok(sizing_runs, "lot_1").trades["default"]
    assert trade_count(with_fact) > 0 and trade_count(fallback) == 0
    assert shape.trades["override"] == with_fact, (
        f"declared 0.01: {summary(shape.trades['override'])}, literal 0.01: {summary(with_fact)}")
    assert shape.trades["default"] == fallback
    print("sizing:", summary(with_fact))


def test_the_warning_names_the_run_fact():
    result = transpile_full('//@version=6\nstrategy("t")\nx = syminfo.mincontract\n'
                            'if x > 0\n    strategy.entry("L", strategy.long)\n')
    messages = [getattr(d, "message", str(d)) for d in result["diagnostics"]]
    assert any("unless the run declares" in m and "mincontract" in m for m in messages), messages
    assert 'get_syminfo_metadata("mincontract")' in result["cpp"]

"""``request.security`` timeframes through nested helpers, against TradingView.

``g(tf) => request.security(syminfo.tickerid, tf, ta.sma(close, 3))`` is
reached as ``h(tfA)``, ``h(tfB)``, ``k("60")`` and ``k(tfB)`` with ``h(tf) =>
g(tf)`` and ``k(tf) => h(tf)``: every call used to read the chart's
timeframe. TradingView's tape of ``xa_nested_tf_only`` (``fixtures/xsym_tv``,
BINANCE:ETHUSDT.P 15) spells the four values on each close: the 60-minute
SMA, the 240-minute SMA, the 60-minute one again and the 240-minute one
again. The inputs are read at registration: under overrides the probe trades
exactly like its literal twin.
"""

from __future__ import annotations

from pathlib import Path

from tests._e2e import Build, closed_trades, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"

# ``xa_nested_tf_only`` with each helper call replaced by the request it
# reaches, at the timeframes the override below gives the inputs.
LITERAL_TWIN = """//@version=6
strategy("PF xa nested tf only twin", overlay = true)
a = request.security(syminfo.tickerid, "120", ta.sma(close, 3))
b = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
c = request.security(syminfo.tickerid, "60", ta.sma(close, 3))
d = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
s(x) => na(x) ? "na" : str.tostring(x)
m = minute(time, "UTC")
if m == 0 or m == 30
    strategy.entry("L", strategy.long)
if m == 15 or m == 45
    strategy.close("L", comment = s(a) + "|" + s(b) + "|" + s(c) + "|" + s(d))
"""


def test_nested_helper_timeframes_replay_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_nested_tf_only")
    exits = replay(engine, base, {"probe": Build(source("xa_nested_tf_only", XSYM_TV)),
                                  "twin": Build(LITERAL_TWIN)})
    tape = tape_exits("xa_nested_tf_only", XSYM_TV)
    assert len(tape) == 265
    # The close sent on the 02:45 UTC bar (exit at 03:00): the 02:00 hour has
    # completed, no 4-hour bar yet.
    assert tape[1743476400000] == "1831.75|na|1831.75|na"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    # Overrides of both inputs move a, b and d; the literal "60" stays.
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv",
                               {"A": "120", "B": "D"})
    assert {t["exit_time"]: t["exit_comment"] for t in overridden} == exits["twin"]
    print(f"nested helper timeframes: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; under A=120, B=D the probe's {len(overridden)} exits "
          "equal its literal twin's")

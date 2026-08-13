"""Symbol-clock D/W/M anchors reach the engine (finding 423).

TradingView's daily bar is the SYMBOL's session day (17:00 ET on OANDA forex,
09:30 ET RTH on NASDAQ equities, 00:00 UTC on a 24x7 UTC symbol). Three
chart-level consumers of "the symbol's daily bar" previously reached the
engine without syminfo.timezone / syminfo.session and therefore keyed on UTC
midnight: ta.vwap's default anchor, timeframe.change(tf) and
time()/time_close() with a D/W/M timeframe. This file covers time()/time_close() (switch c); each must now thread the symbol
clock through so the engine can key on the same session-day helper that
request.security aggregation uses.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\nplot(close)\n'


SYM_TAIL = "PF_PINE_TIME_SESSION_DAY_ARGS(syminfo_.timezone, syminfo_.session))"


def _calls(cpp: str, symbol: str) -> list[str]:
    """Every `symbol(...)` call expression in the generated C++ (one line each)."""
    return [m.group(0) for m in re.finditer(rf"\b{re.escape(symbol)}\([^;]*", cpp)]


def test_time_and_time_close_thread_symbol_clock():
    cpp = transpile(_pine(
        't1 = time("D")\n'
        't2 = time("D", "0930-1600", "America/New_York")\n'
        't3 = time("W", "Europe/Prague")\n'
        'c1 = time_close("D")\n'
        'c2 = time_close("M", "0930-1600")\n'
    ))
    times = _calls(cpp, "pine_time")
    closes = _calls(cpp, "pine_time_close")
    assert len(times) >= 3 and len(closes) >= 2, cpp
    for call in times + closes:
        assert call.startswith(("pine_time(current_bar_.timestamp, ",
                                "pine_time_close(current_bar_.timestamp, ")), call
        # The explicit session / tz arguments stay in their slots (they only
        # filter); the symbol clock is appended after script_tf_.
        assert "script_tf_ " + SYM_TAIL in call, call
    assert any('std::string("0930-1600"), std::string("America/New_York"), script_tf_ ' + SYM_TAIL in c
               for c in times), times


def test_time_prelude_shim_gated_on_usage():
    cpp = transpile(_pine('t = time("D")'))
    assert "#ifdef PF_PINE_TIME_HAS_SESSION_DAY" in cpp
    assert "#define PF_PINE_TIME_SESSION_DAY_ARGS(tz, sess) , tz, sess" in cpp
    cpp = transpile(_pine("x = time\ny = hour(time)"))
    assert "PF_PINE_TIME" not in cpp

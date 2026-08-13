"""Symbol-clock D/W/M anchors reach the engine (finding 423).

TradingView's daily bar is the SYMBOL's session day (17:00 ET on OANDA forex,
09:30 ET RTH on NASDAQ equities, 00:00 UTC on a 24x7 UTC symbol). Three
chart-level consumers of "the symbol's daily bar" previously reached the
engine without syminfo.timezone / syminfo.session and therefore keyed on UTC
midnight: ta.vwap's default anchor, timeframe.change(tf) and
time()/time_close() with a D/W/M timeframe. This file covers timeframe.change (switch b); each must now thread the symbol
clock through so the engine can key on the same session-day helper that
request.security aggregation uses.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\nplot(close)\n'


SYM_TAIL = "syminfo_.timezone, syminfo_.session)"


def _calls(cpp: str, symbol: str) -> list[str]:
    """Every `symbol(...)` call expression in the generated C++ (one line each)."""
    return [m.group(0) for m in re.finditer(rf"\b{re.escape(symbol)}\([^;]*", cpp)]


def test_timeframe_change_uses_symbol_clock_overload():
    cpp = transpile(_pine('nd = timeframe.change("D")\nnw = timeframe.change("W")'))
    calls = _calls(cpp, "tf_change")
    assert len(calls) >= 2, cpp
    for call in calls:
        assert call.startswith("tf_change(prev_bar_timestamp_, current_bar_.timestamp, "), call
        assert call.rstrip().endswith(SYM_TAIL), call

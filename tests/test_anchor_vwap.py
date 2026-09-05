"""Symbol-clock D/W/M anchors reach the engine (finding 423).

TradingView's daily bar is the SYMBOL's session day (17:00 ET on OANDA forex,
09:30 ET RTH on NASDAQ equities, 00:00 UTC on a 24x7 UTC symbol). Three
chart-level consumers of "the symbol's daily bar" previously reached the
engine without syminfo.timezone / syminfo.session and therefore keyed on UTC
midnight: ta.vwap's default anchor, timeframe.change(tf) and
time()/time_close() with a D/W/M timeframe. This file covers ta.vwap (switch a); each must now thread the symbol
clock through so the engine can key on the same session-day helper that
request.security aggregation uses.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\nplot(close)\n'


SYM_TAIL = "PF_VWAP_SESSION_ANCHOR_ARGS(syminfo_.timezone, syminfo_.session))"


def _calls(cpp: str, symbol: str) -> list[str]:
    """Every `symbol(...)` call expression in the generated C++ (one line each)."""
    return [m.group(0) for m in re.finditer(rf"\b{re.escape(symbol)}\([^;]*", cpp)]


def test_ta_vwap_scalar_threads_symbol_clock():
    cpp = transpile(_pine("v = ta.vwap(hlc3)"))
    computes = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "current_bar_" in c]
    assert computes, cpp
    for call in computes:
        assert "current_bar_.volume, current_bar_.timestamp " + SYM_TAIL in call, call
    recomputes = _calls(cpp, "_ta_vwap_1.recompute")
    assert recomputes, cpp
    for call in recomputes:
        assert SYM_TAIL in call, call


def test_ta_vwap_precalc_path_threads_symbol_clock():
    # The historical precalculation loop feeds bars[i] instead of current_bar_
    # and must carry the same symbol clock.
    cpp = transpile(_pine("v = ta.vwap(close)"))
    precalc = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "bars[i]" in c]
    assert precalc, cpp
    for call in precalc:
        assert "bars[i].volume, bars[i].timestamp " + SYM_TAIL in call, call


def test_ta_vwap_bare_property_read_threads_symbol_clock():
    # The bare property is the VWAP of hlc3 (tests/test_vwap_property_source.py).
    cpp = transpile(_pine("v = ta.vwap\nw = ta.vwap"))
    hlc3 = "((current_bar_.high + current_bar_.low + current_bar_.close) / 3.0)"
    calls = [c for c in _calls(cpp, "compute") if hlc3 in c and "vwap" in c]
    assert calls, cpp
    for call in calls:
        assert hlc3 + ", current_bar_.volume, current_bar_.timestamp " + SYM_TAIL in call, call


def test_ta_vwap_bands_form_threads_symbol_clock():
    cpp = transpile(_pine('[v, u, l] = ta.vwap(close, timeframe.change("D"), 1.0)'))
    calls = [c for c in _calls(cpp, "compute") if "vwap" in c and "volume" in c]
    assert calls, cpp
    for call in calls:
        assert SYM_TAIL in call, call


def test_vwap_prelude_shim_gated_on_usage():
    cpp = transpile(_pine("v = ta.vwap(close)"))
    assert "#ifdef PF_VWAP_HAS_SESSION_ANCHOR" in cpp
    assert "#define PF_VWAP_SESSION_ANCHOR_ARGS(tz, sess) , tz, sess" in cpp
    cpp = transpile(_pine("v = ta.sma(close, 5)"))
    assert "PF_VWAP" not in cpp

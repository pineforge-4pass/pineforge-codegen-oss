"""Regression (BUG B): request.security OHLC history-offset pushes must gate on
``is_complete``.

``request.security(tickerid, "D", [high[1], low[1], ...], ...)`` reads HTF OHLC
at past-bar offsets backed by per-field Series. ``_eval_security_N`` fires on
every (partial) chart bar; only the bar completing the HTF aggregate has
``is_complete == true``. The OHLC-history pushes were emitted unconditionally,
so the offset history advanced every chart bar instead of per completed HTF bar
— ``high[1]`` resolved to a recent partial bar, not the prior completed HTF bar.
The pushes must be wrapped in a single ``if (is_complete) { ... }`` block.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _eval_body(cpp: str) -> str:
    m = re.search(r"void _eval_security_\d+.*?\n    \}", cpp, re.S)
    assert m is not None, "no _eval_security_N method found"
    return m.group(0)


def test_tuple_ohlc_hist_pushes_gated_on_is_complete():
    src = """//@version=6
strategy("t", overlay=true)
[h1, l1, h2, l2, a] = request.security(syminfo.tickerid, "D", [high[1], low[1], high[2], low[2], ta.atr(14)[1]], lookahead=barmerge.lookahead_on)
if not na(h1) and high > h1
    strategy.entry("L", strategy.long)
plot(close)
"""
    body = _eval_body(transpile(src))
    # The HTF-history pushes must live inside an is_complete guard.
    assert "if (is_complete) {" in body
    # Every hist push for this sec sits inside the guard, not before it.
    guard_idx = body.index("if (is_complete) {")
    for push in re.finditer(r"_sec0_hist_\w+\.push\(bar\.\w+\);", body):
        assert push.start() > guard_idx, f"ungated push: {push.group(0)}"


def test_scalar_ohlc_hist_push_gated_on_is_complete():
    src = """//@version=6
strategy("t", overlay=true)
h1 = request.security(syminfo.tickerid, "D", high[1], lookahead=barmerge.lookahead_on)
if not na(h1) and high > h1
    strategy.entry("L", strategy.long)
plot(close)
"""
    body = _eval_body(transpile(src))
    assert "if (is_complete) {" in body
    guard_idx = body.index("if (is_complete) {")
    push = re.search(r"_sec0_hist_high\.push\(bar\.high\);", body)
    assert push is not None and push.start() > guard_idx


def test_security_time_identifier_uses_security_bar_timestamp():
    src = """//@version=6
strategy("t", overlay=true)
ht = request.security(syminfo.tickerid, "60", time, lookahead=barmerge.lookahead_off)
if not na(ht)
    strategy.entry("L", strategy.long)
plot(close)
"""
    body = _eval_body(transpile(src))
    assert "_req_sec_0 = bar.timestamp;" in body
    assert "current_bar_.timestamp" not in body


def test_security_time_history_uses_completed_htf_history():
    src = """//@version=6
strategy("t", overlay=true)
ht = request.security(syminfo.tickerid, "60", time[1], lookahead=barmerge.lookahead_off)
if not na(ht)
    strategy.entry("L", strategy.long)
plot(close)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "Series<int64_t> _sec0_hist_time" in cpp
    assert "_req_sec_0 = _sec0_hist_time[0];" in body
    assert "time[1]" not in body
    assert "current_bar_.timestamp" not in body
    guard_idx = body.index("if (is_complete) {")
    push = re.search(r"_sec0_hist_time\.push\(bar\.timestamp\);", body)
    assert push is not None and push.start() > guard_idx


def test_security_time_history_allows_input_backed_offset():
    src = """//@version=6
strategy("t", overlay=true)
len = input.int(5, "Len")
ht = request.security(syminfo.tickerid, "60", time[len], lookahead=barmerge.lookahead_off)
if not na(ht)
    strategy.entry("L", strategy.long)
plot(close)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "Series<int64_t> _sec0_hist_time" in cpp
    assert "int _hidx = (int)(get_input_int(\"Len\", 5));" in body
    assert "return (_hidx <= 0) ? bar.timestamp : _sec0_hist_time[_hidx - 1];" in body
    assert "time[len]" not in body

"""The bare ``ta.vwap`` property is the VWAP of hlc3, bound to its own site.

Pine v6 reference: ``ta.vwap`` "uses hlc3 as its source series". TradingView's
read equals hlc3 on a daily bar (lab tv notrade-session-vwap-f1d, NYSE:F 1D
2025-07-01..08-31, 42/42 bars, 2026-09-05) where the generated code used to
feed the bar's close (therealbouga-apex-mtf-index-model F@1D: ``close < vwap``
never true, 0 engine trades vs 9). And the property read is one TA site per
read, keyed on its own AST node: a bare ``ta.vwap`` inside
``request.security()`` is that evaluator's own ``_sec<id>__ta_vwap_<n>``
member (advanced by ``security_series_slot_is_new``), not the first live
CHART member advanced by the chart bar and every requested sub-bar
(nightowlxtrader-azt F@1D ``vwap5 = request.security(tickerid,
timeframe.period, ta.vwap)``).
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


HLC3_CHART = "((current_bar_.high + current_bar_.low + current_bar_.close) / 3.0)"
HLC3_SEC = "((bar.high + bar.low + bar.close) / 3.0)"
SYM_TAIL = "PF_VWAP_SESSION_ANCHOR_ARGS(syminfo_.timezone, syminfo_.session))"


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\nplot(close)\n'


def _calls(cpp: str, symbol: str) -> list[str]:
    return [m.group(0) for m in re.finditer(rf"\b{re.escape(symbol)}\([^;]*", cpp)]


def test_bare_property_reads_hlc3_not_close():
    cpp = transpile(_pine("v = ta.vwap"))
    computes = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "current_bar_" in c]
    assert computes, cpp
    for call in computes:
        assert call.startswith("_ta_vwap_1.compute(" + HLC3_CHART + ", current_bar_.volume, current_bar_.timestamp " + SYM_TAIL), call
        assert "current_bar_.close, current_bar_.volume" not in call, call
    for call in _calls(cpp, "_ta_vwap_1.recompute"):
        assert call.startswith("_ta_vwap_1.recompute(" + HLC3_CHART), call
    # The historical precalculation loop feeds bars[i] with the same source.
    precalc = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "bars[i]" in c]
    assert precalc, cpp
    for call in precalc:
        assert call.startswith("_ta_vwap_1.compute(((bars[i].high + bars[i].low + bars[i].close) / 3.0), bars[i].volume"), call


def test_property_and_explicit_hlc3_call_agree():
    cpp = transpile(_pine("v = ta.vwap\nw = ta.vwap(hlc3)"))
    v_calls = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "current_bar_" in c]
    w_calls = [c for c in _calls(cpp, "_ta_vwap_2.compute") if "current_bar_" in c]
    assert v_calls and w_calls, cpp
    assert v_calls[0].replace("_ta_vwap_1", "X") == w_calls[0].replace("_ta_vwap_2", "X")


def test_two_bare_reads_are_two_sites_each_advanced_once():
    cpp = transpile(_pine("v = ta.vwap\nw = ta.vwap"))
    body = cpp[cpp.index("void on_bar("):]
    body = body[: body.index("void precalculate(")] if "void precalculate(" in body else body
    assert len([c for c in _calls(body, "_ta_vwap_1.compute")]) == 1, body
    assert len([c for c in _calls(body, "_ta_vwap_2.compute")]) == 1, body


def test_bare_property_inside_security_binds_to_its_own_evaluator_member():
    cpp = transpile(_pine(
        'v = ta.vwap\n'
        's = request.security(syminfo.tickerid, "D", ta.vwap)\n'
        'if v > s\n'
        '    strategy.entry("L", strategy.long)\n'
    ))
    evaluator = cpp[cpp.index("void _eval_security_0("):]
    evaluator = evaluator[: evaluator.index("}\n")]
    # Its own member, advanced by the requested context's slot rule, fed the
    # requested bar's hlc3 -- never the chart member or the chart tick rule.
    assert "security_series_slot_is_new(0) ? _sec0__ta_vwap_2.compute(" + HLC3_SEC + ", bar.volume, bar.timestamp " + SYM_TAIL in evaluator, evaluator
    assert "_ta_vwap_1" not in evaluator, evaluator
    assert "history_advances_new_bar()" not in evaluator, evaluator
    assert "current_bar_" not in evaluator, evaluator
    assert "ta::VWAP _sec0__ta_vwap_2;" in cpp, cpp
    # The chart read keeps its own member and hlc3 source.
    chart = [c for c in _calls(cpp, "_ta_vwap_1.compute") if "current_bar_" in c]
    assert chart and chart[0].startswith("_ta_vwap_1.compute(" + HLC3_CHART), chart


def test_bare_property_inside_security_same_tf_as_chart():
    # nightowlxtrader's shape: timeframe.period on the chart's own timeframe.
    cpp = transpile(_pine(
        'vw = request.security(syminfo.tickerid, timeframe.period, ta.vwap, lookahead=barmerge.lookahead_off)\n'
        'if close > vw\n'
        '    strategy.entry("L", strategy.long)\n'
    ))
    evaluator = cpp[cpp.index("void _eval_security_0("):]
    evaluator = evaluator[: evaluator.index("}\n")]
    assert "_sec0__ta_vwap_1.compute(" + HLC3_SEC in evaluator, evaluator
    assert "history_advances_new_bar()" not in evaluator, evaluator

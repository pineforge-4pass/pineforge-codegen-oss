"""issue #178: ta.atr / ta.tr take the previous CHART bar's close.

TradingView pin (2026-09-06, lab tv i178-sparse-atr-sense, BINANCE:BTCUSDT 60,
398/398 sparse executions): ``ta.atr(n)`` called inside a block that does not
execute every bar advances its RMA on the executions only, but its true range
always reads ``close[1]`` of the CHART, never the close of the site's previous
execution. The engine's per-object ``prev_close`` is the refuted model, so the
chart-context compute() passes ``BacktestEngine::prev_chart_close()`` as a 4th
argument; the precalc pre-pass (which walks ``bars[i]`` itself) passes
``bars[i - 1].close``; a request.security context keeps the 3-arg form.
"""

from __future__ import annotations

import re

import pineforge_codegen as pc

PRELUDE = '//@version=6\nstrategy("t", overlay=true)\n'


def _gen(src: str) -> str:
    return pc.transpile(PRELUDE + src, check_support=False)


def test_sparse_atr_chart_context_passes_prev_chart_close():
    cpp = _gen(
        "reach = close[1] > open[1] and close < open\n"
        "var float a = na\n"
        "if reach\n"
        "    a := ta.atr(3)\n"
        "plot(a)\n"
    )
    assert (
        "_ta_atr_1.compute(current_bar_.high, current_bar_.low, current_bar_.close, prev_chart_close())"
        in cpp
    ), cpp
    assert (
        "_ta_atr_1.recompute(current_bar_.high, current_bar_.low, current_bar_.close, prev_chart_close())"
        in cpp
    ), cpp


def test_every_bar_tr_passes_prev_chart_close_and_precalc_uses_bars_i_minus_1():
    cpp = _gen("t = ta.tr(true)\nplot(t)\n")
    assert (
        "_ta_tr_1.compute(current_bar_.high, current_bar_.low, current_bar_.close, prev_chart_close())"
        in cpp
    ), cpp
    # The precalc pre-pass, when emitted, walks the bar array itself.
    for m in re.finditer(r"_precalc__ta_tr_1\[i\] = _ta_tr_1\.compute\(([^;]*)\);", cpp):
        assert m.group(1) == "bars[i].high, bars[i].low, bars[i].close, (i > 0 ? bars[i - 1].close : na<double>())", m.group(0)
    assert "prev_chart_close()" not in "".join(
        l for l in cpp.splitlines() if "_precalc_" in l
    ), cpp


def test_security_context_atr_keeps_three_arg_form():
    cpp = _gen(
        'a = request.security(syminfo.tickerid, "D", ta.atr(14))\n'
        "plot(a)\n"
    )
    sec_lines = [l for l in cpp.splitlines() if "compute(bar.high, bar.low, bar.close" in l]
    assert sec_lines, cpp
    assert all("prev_chart_close()" not in l for l in sec_lines), sec_lines

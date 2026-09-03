"""Pine evaluates ``ta.*`` LAZILY: a call in an untaken ternary branch or a
short-circuited ``and``/``or`` operand does NOT advance its series state on
that bar.

``_emit_security_evaluators`` used to hoist *every* collected TA site of a
``request.security`` expression into an unconditional
``auto _secval_N = ...compute(bar.close);`` prologue, so each site advanced on
every HTF bar regardless of whether Pine reached it. On the
``(ema20 > ema50 and close > ema200) ? 1 : (ema20 < ema50 and close < ema200) ? -1 : 0``
MTF-trend shape that desynchronised the HTF EMAs against TradingView.

The fix drops conditionally-reached sites from that prologue.
``_build_security_expr`` then emits their
``(security_series_slot_is_new(N) ? m.compute(a) : m.recompute(a))`` inline in
expression position, where C++'s own ``&&`` / ``||`` / ``?:`` short-circuit
advances the series on exactly the bars Pine does.

The classifier is deliberately conservative, and these tests pin both halves:
what becomes lazy, and what must stay eager (multi-reach sites, globals,
history-offset sites, mutable-global securities) so scripts without
conditional security TA keep regenerating byte-identically.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile

HOIST_RE = re.compile(r"auto (_secval_\w+) = security_series_slot_is_new")
INLINE_RE = re.compile(
    r"security_series_slot_is_new\(\d+\) \? (_sec\d+_\w+)\.compute"
)


def _strategy(body: str) -> str:
    return f"""//@version=6
strategy("t", overlay=true)
{body}
if not na(v)
    strategy.entry("L", strategy.long)
plot(close)
"""


def _eval_body(cpp: str, sec_id: int = 0) -> str:
    m = re.search(rf"void _eval_security_{sec_id}\(.*?\n    \}}", cpp, re.S)
    assert m is not None, f"no _eval_security_{sec_id} method found"
    return m.group(0)


def _hoisted(body: str) -> list[str]:
    """TA sites evaluated eagerly in the evaluator prologue."""
    return HOIST_RE.findall(body)


def _inlined(body: str) -> list[str]:
    """TA members evaluated inline in expression position (lazily)."""
    assign = body[body.index("_req_sec_"):]
    return INLINE_RE.findall(assign)


# ----------------------------------------------------------------------
# Lazy: the site sits behind a Pine short-circuit / untaken branch
# ----------------------------------------------------------------------


def test_ternary_branches_are_lazy():
    """Neither ``?:`` branch may be hoisted: Pine evaluates only one."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " close > open ? ta.ema(close, 20) : ta.sma(close, 20),"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == []
    assert sorted(_inlined(body)) == ["_sec0__ta_ema_1", "_sec0__ta_sma_2"]


def test_and_right_operand_is_lazy_left_stays_eager():
    """``and`` runs its LHS every bar; its RHS is short-circuited."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " ta.ema(close, 20) > close and ta.sma(close, 50) > close,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0"]
    assert "_sec0__ta_ema_1.compute" in body.split("_req_sec_")[0]
    assert _inlined(body) == ["_sec0__ta_sma_2"]


def test_or_right_operand_is_lazy_left_stays_eager():
    """``or`` short-circuits its RHS exactly like ``and``."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " ta.ema(close, 20) > close or ta.sma(close, 50) > close,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0"]
    assert _inlined(body) == ["_sec0__ta_sma_2"]


def test_nested_ternary_and_composition_matches_pine_reach():
    """The MTF-trend shape that motivated the fix.

    ``(e20 > e50 and close > e200) ? 1 : (e20b < e50b and close < e200b) ? -1 : 0``

    Only the two operands of the outer condition's ``and`` LHS are reached on
    every bar. ``e200`` sits behind that ``and``; the whole inner ternary sits
    in the outer's untaken branch; ``e200b`` is behind a second ``and``.
    """
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " (ta.ema(close,20) > ta.ema(close,50) and close > ta.ema(close,200)) ? 1"
        " : (ta.ema(close,20) < ta.ema(close,50) and close < ta.ema(close,200)) ? -1 : 0,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0", "_secval_1"]
    assert _inlined(body) == [
        "_sec0__ta_ema_3",
        "_sec0__ta_ema_4",
        "_sec0__ta_ema_5",
        "_sec0__ta_ema_6",
    ]
    # The lazy sites must land inside the short-circuiting structure, i.e.
    # after the ternary's `?` / the `&&`, never before the assignment.
    assert "_sec0__ta_ema_3" not in body.split("_req_sec_")[0]


def test_laziness_propagates_through_single_expression_helper():
    """``f(x) => ta.ema(x, 20)`` called in a branch is reached only there.

    Each call site gets its own TA variant member, so both branches of
    ``c ? f(close) : f(close) * 2`` are independently lazy.
    """
    body = _eval_body(transpile(_strategy(
        "f(x) => ta.ema(x, 20)\n"
        'v = request.security(syminfo.tickerid, "60",'
        " close > open ? f(close) : f(close) * 2.0,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == []
    assert sorted(_inlined(body)) == [
        "_sec0__ta_ema_1_v0",
        "_sec0__ta_ema_1_v1",
    ]


# ----------------------------------------------------------------------
# Eager: unconditional Pine statements still advance every bar
# ----------------------------------------------------------------------


def test_unconditional_sites_stay_eager():
    """No conditional in sight -> byte-for-byte the pre-fix hoisted form."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " ta.ema(close, 20) + ta.sma(close, 50),"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0", "_secval_1"]
    assert _inlined(body) == []
    assert "_req_sec_0 = (_secval_0 + _secval_1);" in body


def test_ternary_condition_operands_stay_eager():
    """A ternary's *condition* is evaluated on every bar."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " ta.ema(close, 20) > ta.sma(close, 50) ? 1.0 : 0.0,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0", "_secval_1"]
    assert _inlined(body) == []


def test_global_binding_read_under_a_conditional_stays_eager():
    """``e = ta.ema(close, 20)`` is its own unconditional top-level statement.

    In the requested context it evaluates on every HTF bar however it is read,
    so laziness must not propagate through the global binding. This is what
    keeps chart-side ``t0``-shaped code exact.
    """
    body = _eval_body(transpile(_strategy(
        "e = ta.ema(close, 20)\n"
        'v = request.security(syminfo.tickerid, "60",'
        " close > open ? e : 0.0, lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0"]
    assert _inlined(body) == []


def test_history_offset_site_under_a_conditional_stays_eager():
    """``ta.ema(close, 20)[1]`` needs its committed ``_secval_*`` to push."""
    body = _eval_body(transpile(_strategy(
        'v = request.security(syminfo.tickerid, "60",'
        " close > open ? ta.ema(close, 20)[1] : 0.0,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0"]
    assert _inlined(body) == []
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body


def test_mutable_global_security_stays_eager():
    """Securities that rebind mutable globals bail out of the analysis.

    Their rebind statements are lowered by a separate statement emitter that
    consumes ``ta_results`` outside this expression, so a site moved inline
    could advance twice.
    """
    body = _eval_body(transpile(_strategy(
        "var float acc = 0.0\n"
        "acc := acc + 1.0\n"
        'v = request.security(syminfo.tickerid, "60",'
        " close > open ? ta.ema(close, 20) + acc : 0.0,"
        " lookahead=barmerge.lookahead_off)"
    )))
    assert _hoisted(body) == ["_secval_0"]
    assert _inlined(body) == []


# ----------------------------------------------------------------------
# Chart context: already inline, and must stay that way
# ----------------------------------------------------------------------


def test_chart_context_conditional_ta_is_inline_without_history_read():
    """The chart path keeps its reached-only ``?:`` / ``&&`` compute unless the
    call's own history is read (2026-09-04: hoisting ``c ? ta.ema(...) : 0``
    shapes broke quantbyboji/ycelestine77/oliver1002/louislapis9 on ETH, all
    exact at 100% on this lowering). Never ``_secval_`` on the chart path."""
    cpp = transpile(_strategy(
        "c = close > open\n"
        "v = c ? ta.ema(close, 20) : 0.0\n"
        "w = c and ta.rsi(close, 14) > 50.0"
    ))
    assign = next(ln for ln in cpp.splitlines() if ln.strip().startswith("v = ("))
    assert "? ((history_advances_new_bar() ? _ta_ema_1.compute" in assign
    assert "_secval_" not in assign
    w_assign = next(ln for ln in cpp.splitlines() if ln.strip().startswith("w = ("))
    assert "_ta_rsi_2.compute" in w_assign
    assert "_pf_every_bar_ta_" not in cpp


def test_chart_context_conditional_ta_with_history_read_is_hoisted_every_bar():
    """With ``[1]`` on the call TradingView advances the built-in every bar
    (lab tv 2026-09-03, ``... and close > ta.ema(close, 5)[1]`` 23/23 vs
    per-call 27): codegen hoists it before the statement."""
    cpp = transpile(_strategy(
        "c = close > open\n"
        "v = c ? ta.ema(close, 20)[1] : 0.0"
    ))
    lines = cpp.splitlines()
    hoist = next(ln for ln in lines if ln.strip().startswith("const auto _pf_every_bar_ta_1 = "))
    assert "_ta_ema_1.compute" in hoist
    assign = next(ln for ln in lines if ln.strip().startswith("v = ("))
    assert assign.strip() == "v = ((c) ? (_hist_call_1[(int)(1)]) : (0.0));"
    assert lines.index(hoist) < lines.index(assign)


def test_chart_context_unconditional_ta_unchanged():
    """An unconditional chart ``ta.*`` still evaluates once per bar, inline."""
    cpp = transpile(_strategy("v = ta.ema(close, 20) + ta.sma(close, 50)"))
    assign = next(ln for ln in cpp.splitlines() if ln.strip().startswith("v = ("))
    assert "_ta_ema_1.compute" in assign
    assert "_ta_sma_2.compute" in assign

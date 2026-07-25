"""Tests for compiler.transpiler.support_checker.

Each test exercises one rule bucket. Helpers parse the source with the real
lexer/parser so the tests double as parser smoke tests for the constructs.
"""

from __future__ import annotations

import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.support_checker import (
    SupportChecker,
    HARD_REJECT_FUNC,
    HARD_REJECT_NAMESPACE,
    DIVERGENT_VARS,
    DIVERGENT_VARS_ERROR,
    NOT_YET_FUNC,
    SECURITY_ALLOWED_PARAMS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PRELUDE = '//@version=6\nstrategy("T")\n'


def _check(src: str):
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    return SupportChecker(ast, filename="<test>").check()


def _errors(src: str):
    return [d for d in _check(src) if d.level == Level.ERROR]


def _warnings(src: str):
    return [d for d in _check(src) if d.level == Level.WARNING]


def _expect_error(src: str, needle: str) -> None:
    errs = _errors(src)
    assert errs, f"expected error containing {needle!r}, got nothing"
    assert any(needle in d.message or (d.hint and needle in d.hint) for d in errs), \
        f"no error matching {needle!r} in: {[d.message for d in errs]}"


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

def test_strategy_decl_passes():
    assert _errors(PRELUDE + "x = close\n") == []


def test_indicator_decl_rejected():
    src = '//@version=6\nindicator("T")\nx = close\n'
    _expect_error(src, "indicator() declarations are not supported")


# ---------------------------------------------------------------------------
# Divergent built-in variables — WARN unless a true mis-alias enters the ERROR subset
# ---------------------------------------------------------------------------

_DIVERGENT_WARN_ONLY = sorted(set(DIVERGENT_VARS) - DIVERGENT_VARS_ERROR)


@pytest.mark.parametrize("var_name", _DIVERGENT_WARN_ONLY)
def test_divergent_variables_warn(var_name: str):
    src = PRELUDE + f"x = {var_name}\n"
    assert _errors(src) == [], f"{var_name} should warn, not error"
    warns = _warnings(src)
    assert any("diverges" in d.message for d in warns), \
        f"expected divergence warning for {var_name}, got {[d.message for d in warns]}"


def test_last_bar_index_warns_about_fed_window_chart_history():
    src = PRELUDE + "x = last_bar_index\n"
    assert _errors(src) == []
    warns = [d for d in _warnings(src) if "last_bar_index" in d.message]
    assert len(warns) == 1
    detail = f"{warns[0].message} {warns[0].hint or ''}".lower()
    assert "final index" in detail
    assert "fed data window" in detail
    assert "chart history" in detail
    assert "current bar index" not in detail


def test_time_close_variable_accepted():
    """The bare ``time_close`` variable is faithfully supported: codegen lowers
    it to the engine ``time_close()`` accessor (true bar-close = bar open +
    chart-timeframe duration), so it is neither an error nor a divergence
    warning."""
    src = PRELUDE + "x = time_close\n"
    assert _errors(src) == [], "time_close is now faithfully emitted, not rejected"
    assert not any("diverges" in d.message for d in _warnings(src))


def test_bar_index_still_warns():
    src = PRELUDE + "x = bar_index\n"
    assert _errors(src) == [], "bar_index must remain a WARNING, not ERROR"
    assert any("diverges" in d.message for d in _warnings(src))


def test_timenow_still_warns():
    src = PRELUDE + "x = timenow\n"
    assert _errors(src) == [], "timenow must remain a WARNING, not ERROR"
    assert any("diverges" in d.message for d in _warnings(src))


def test_divergent_error_subset_is_subset():
    assert DIVERGENT_VARS_ERROR <= set(DIVERGENT_VARS)
    assert "bar_index" not in DIVERGENT_VARS_ERROR
    assert "timenow" not in DIVERGENT_VARS_ERROR
    # time_close is no longer rejected — codegen lowers it to the faithful
    # engine time_close() accessor.
    assert "time_close" not in DIVERGENT_VARS_ERROR
    assert "last_bar_index" not in DIVERGENT_VARS_ERROR
    assert not DIVERGENT_VARS_ERROR


def test_time_close_function_call_not_flagged_as_divergent_var():
    """The session-aware ``time_close(...)`` FUNCTION is distinct from the
    bare ``time_close`` variable and must not trip the divergent-var ERROR."""
    src = PRELUDE + 'tc = time_close("D")\n'
    assert _errors(src) == [], (
        "time_close(...) is a supported function; only the bare variable "
        "should be rejected"
    )
    assert not any("diverges" in d.message for d in _warnings(src))


def test_time_close_session_function_call_not_flagged():
    src = PRELUDE + 'int tc = time_close(timeframe.period, "0930-1600", "UTC")\n'
    assert _errors(src) == []


# ---------------------------------------------------------------------------
# Hard-reject functions / namespaces
# ---------------------------------------------------------------------------

def test_request_security_lower_tf_accepted():
    """request.security_lower_tf is supported as of the lower-TF array PR.
    Element type rejection (UDT/color/string) is enforced by the analyzer,
    not the support_checker — tested in tests/test_request_security_lower_tf.py."""
    src = PRELUDE + 'a = request.security_lower_tf(syminfo.tickerid, "1", close)\n'
    # No CompileError expected; analyzer + codegen handle parameter validation.
    from pineforge_codegen import transpile
    cpp = transpile(src)
    assert "_req_sec_lower_tf" in cpp


def test_request_financial_rejected():
    src = PRELUDE + 'a = request.financial(syminfo.tickerid, "REVENUE", "FY")\n'
    _expect_error(src, "request.financial")


def test_unknown_request_function_rejected():
    src = PRELUDE + 'a = request.totally_unknown(syminfo.tickerid, "FY")\n'
    _expect_error(src, "Only request.security")


def test_color_from_gradient_warns_not_rejected():
    # Cosmetic charting helper: no backtest-logic effect. Warned (no-op),
    # not rejected; codegen emits a default color.
    src = PRELUDE + "c = color.from_gradient(close, 0, 100, color.red, color.green)\n"
    assert not _errors(src)
    assert any("from_gradient" in w.message for w in _warnings(src))


def test_unknown_color_function_rejected():
    src = PRELUDE + "c = color.not_real(close)\n"
    _expect_error(src, "color.not_real")


def test_unknown_timeframe_function_rejected():
    src = PRELUDE + 'x = timeframe.not_real("60")\n'
    _expect_error(src, "timeframe.not_real")


def test_unknown_runtime_function_rejected():
    src = PRELUDE + 'x = runtime.not_real("boom")\n'
    _expect_error(src, "runtime.not_real")


def test_library_declaration_rejected():
    src = '//@version=6\nlibrary("L")\n'
    _expect_error(src, "library() declarations are not supported")


def test_varip_rejected_in_batch_backtests():
    """Phase C: varip is now an error (was a warning).

    Batch backtests have no intrabar tick semantics; silently demoting
    varip to var produces wrong state accumulation. See
    tests/test_support_checker_varip.py for the full rejection contract.
    """
    src = PRELUDE + "varip int ticks = 0\n"
    errs = _errors(src)
    assert any("varip" in d.message for d in errs), (
        f"expected error containing 'varip', got {[d.message for d in errs]}"
    )


def test_varip_int_emits_error():
    """varip int x = 0 must error (formerly warned)."""
    src = PRELUDE + "varip int x = 0\n"
    errs = _errors(src)
    varip_errs = [d for d in errs if "varip" in d.message]
    assert len(varip_errs) >= 1, (
        f"expected at least one varip error, got {len(varip_errs)}: "
        f"{[d.message for d in varip_errs]}"
    )
    assert "batch" in varip_errs[0].message, (
        f"error should mention 'batch': {varip_errs[0].message}"
    )


def test_ticker_namespace_rejected():
    src = PRELUDE + 't = ticker.new(syminfo.prefix, syminfo.ticker)\n'
    _expect_error(src, "ticker")


# ---------------------------------------------------------------------------
# Not-yet-implemented bucket
# ---------------------------------------------------------------------------

def test_str_format_time_supported():
    src = PRELUDE + 'x = str.format_time(time, "yyyy-MM-dd", "UTC")\n'
    assert _errors(src) == []


def test_max_bars_back_accepted():
    """max_bars_back is WIRED now (sizes the Series<T> ring buffer via the
    engine's Series<T>(int max_len) ctor) — it is no longer rejected."""
    src = PRELUDE + "max_bars_back(close, 500)\n"
    assert _errors(src) == [], "max_bars_back is wired and must not be rejected"


def test_max_bars_back_not_in_not_yet():
    assert "max_bars_back" not in NOT_YET_FUNC


def test_bare_barssince_rejected():
    src = PRELUDE + "x = barssince(close > open)\n"
    _expect_error(src, "barssince")


def test_strategy_risk_warns_partial_runtime_support():
    src = PRELUDE + "strategy.risk.max_drawdown(50, strategy.percent_of_equity)\n"
    assert _errors(src) == []
    warns = _warnings(src)
    assert any("strategy.risk" in d.message for d in warns)
    assert not any("silently dropped" in d.message for d in warns)


# ---------------------------------------------------------------------------
# ta.sum hint
# ---------------------------------------------------------------------------

def test_ta_sum_rejected_with_math_sum_hint():
    src = PRELUDE + "x = ta.sum(close, 14)\n"
    errs = _errors(src)
    assert any("math.sum" in (d.hint or "") for d in errs)


# ---------------------------------------------------------------------------
# request.security parameter rules
# ---------------------------------------------------------------------------

def test_request_security_minimal_passes():
    src = PRELUDE + 'a = request.security(syminfo.tickerid, "60", close)\n'
    assert _errors(src) == []


def test_request_security_kwarg_minimal_passes():
    src = PRELUDE + 'a = request.security(symbol=syminfo.tickerid, timeframe="60", expression=close)\n'
    assert _errors(src) == []


def test_request_security_lookahead_off_kwarg_passes():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'lookahead=barmerge.lookahead_off)\n')
    assert _errors(src) == []


def test_request_security_lookahead_on_kwarg_warns():
    # lookahead_on is engine-supported (base.py forwards the flag, emit_top.py
    # registers it, engine_security.cpp dispatches the partial HTF eval). It is
    # allowed but flagged as a data-sensitive parity warning, not rejected.
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'lookahead=barmerge.lookahead_on)\n')
    assert _errors(src) == []
    assert any("lookahead_on" in d.message for d in _warnings(src))


def test_request_security_lookahead_on_positional_warns():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'barmerge.gaps_off, barmerge.lookahead_on)\n')
    assert _errors(src) == []
    assert any("lookahead_on" in d.message for d in _warnings(src))


def test_request_security_gaps_barmerge_kwarg_passes():
    for val in ("gaps_on", "gaps_off"):
        src = (PRELUDE +
               'a = request.security(syminfo.tickerid, "60", close, '
               f'gaps=barmerge.{val})\n')
        assert _errors(src) == [], f"{val} should pass"


def test_request_security_gaps_on_with_lookahead_off_passes():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'gaps=barmerge.gaps_on, lookahead=barmerge.lookahead_off)\n')
    assert _errors(src) == []


def test_request_security_lookahead_non_barmerge_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'lookahead=true)\n')
    _expect_error(src, "barmerge.lookahead")


def test_request_security_gaps_non_barmerge_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'gaps=true)\n')
    _expect_error(src, "barmerge.gaps")


def test_request_security_currency_kwarg_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'currency=currency.USD)\n')
    _expect_error(src, "currency")


def test_request_security_ignore_invalid_symbol_kwarg_accepted():
    # ignore_invalid_symbol is a guaranteed no-op: codegen forces the current
    # chart symbol (always valid), so the flag can never change the result.
    # Accept it (codegen drops it) rather than reject a valid, harmless script.
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'ignore_invalid_symbol=true)\n')
    assert _errors(src) == []


def test_request_security_positional_gaps_lookahead_passes():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'barmerge.gaps_off, barmerge.lookahead_off)\n')
    assert _errors(src) == []


def test_request_security_positional_currency_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'barmerge.gaps_off, barmerge.lookahead_off, '
           'true, currency.USD)\n')
    _expect_error(src, "Extra positional arguments")


def test_request_security_alternate_symbol_rejected():
    src = PRELUDE + 'a = request.security("BINANCE:BTCUSDT", "60", close)\n'
    _expect_error(src, "current chart symbol")


def test_request_security_syminfo_ticker_passes():
    src = PRELUDE + 'a = request.security(syminfo.ticker, "60", close)\n'
    assert _errors(src) == []


# ---------------------------------------------------------------------------
# request.security symbol holes: register_security_eval carries no symbol, so
# an accepted non-chart symbol silently backtests the CHART feed. A divergent
# ``:=`` rebind must be rejected at the exact symbol argument.
# ---------------------------------------------------------------------------

def _expect_error_at(src: str, needle: str, line: int, col: int) -> None:
    """Assert a matching error AND pin its precise file:line:col.

    ``file`` comes from the SourceLocation the PARSER stamped on the offending
    node (``<input>`` for :func:`_check`'s sources), not from the checker's own
    ``filename`` argument.
    """
    _expect_error(src, needle)
    matching = [
        d for d in _errors(src)
        if needle in d.message or (d.hint and needle in d.hint)
    ]
    located = [
        f"{d.location.file}:{d.location.line}:{d.location.col}" for d in matching
    ]
    assert f"<input>:{line}:{col}" in located, located


def test_request_security_reassigned_symbol_identifier_rejected():
    """``var sym = syminfo.tickerid`` … ``sym := "EXCH:OTHER"`` must reject.

    ``_scalar_defs`` only ever saw the DECLARATION, so the divergent ``:=``
    rebind used to transpile clean and run on the chart feed.
    """
    src = (
        PRELUDE
        + 'var sym = syminfo.tickerid\n'
        + 'sym := "EXCH:OTHER"\n'
        + 'a = request.security(sym, "60", close)\n'
    )
    _expect_error_at(src, "current chart symbol", line=5, col=22)


def test_request_security_reassigned_symbol_after_call_rejected():
    """A divergent rebind LATER in the source must reject too."""
    src = (
        PRELUDE
        + 'var sym = syminfo.tickerid\n'
        + 'a = request.security(sym, "60", close)\n'
        + 'sym := "EXCH:OTHER"\n'
    )
    _expect_error(src, "current chart symbol")


def test_request_security_symbol_rebound_to_current_symbol_passes():
    src = (
        PRELUDE
        + 'var sym = syminfo.tickerid\n'
        + 'sym := syminfo.ticker\n'
        + 'a = request.security(sym, "60", close)\n'
    )
    assert _errors(src) == []


def test_request_security_symbol_compound_rebind_rejected():
    """A string-built symbol reaches the same rule through its RHS operand."""
    src = (
        PRELUDE
        + 'var sym = syminfo.tickerid\n'
        + 'sym += "X"\n'
        + 'a = request.security(sym, "60", close)\n'
    )
    _expect_error(src, "current chart symbol")


# ---------------------------------------------------------------------------
# Unknown built-ins (silent stub avoidance)
# ---------------------------------------------------------------------------

def test_unknown_ta_function_rejected():
    src = PRELUDE + "x = ta.totally_made_up(close, 14)\n"
    _expect_error(src, "ta.totally_made_up")


def test_unknown_math_function_rejected():
    src = PRELUDE + "x = math.bogus(close)\n"
    _expect_error(src, "math.bogus")


def test_math_random_seed_supported():
    src = PRELUDE + "x = math.random(0, 1, 42)\n"
    assert _errors(src) == []


def test_unknown_syminfo_member_rejected():
    src = PRELUDE + "x = syminfo.not_a_real_field\n"
    _expect_error(src, "syminfo.not_a_real_field")


@pytest.mark.parametrize(
    "var_name",
    ["barstate.islast", "barstate.islastconfirmedhistory"],
)
def test_barstate_final_fed_bar_approximation_warns(var_name: str):
    src = PRELUDE + f"x = {var_name}\n"
    assert _errors(src) == []
    warns = [d for d in _warnings(src) if var_name in d.message]
    assert len(warns) == 1
    detail = f"{warns[0].message} {warns[0].hint or ''}".lower()
    assert "true only on the final bar" in detail
    assert "fed chart-data window" in detail
    assert "direct" in detail
    assert "chart-scope" in detail
    assert "inside a request.security() history index" in detail
    assert "false branch" in detail
    assert "requested-context" in detail


def test_unsupported_strategy_entry_params_warn():
    src = PRELUDE + 'strategy.entry("L", strategy.long, oca_name="g", qty_type=strategy.cash)\n'
    assert _errors(src) == []
    warns = _warnings(src)
    assert not any("strategy.entry" in d.message and "oca_name" in d.message for d in warns)
    assert not any("strategy.entry" in d.message and "qty_type" in d.message for d in warns)


@pytest.mark.parametrize(
    ("call", "param_name"),
    [
        ('strategy.close_all(alert_message="filled")', "alert_message"),
        ('strategy.close_all(alert_message="")', "alert_message"),
        ('strategy.close_all(alert_message=str.tostring(close))', "alert_message"),
        ("strategy.close_all(disable_alert=true)", "disable_alert"),
        ("strategy.close_all(disable_alert=false)", "disable_alert"),
        ("strategy.close_all(disable_alert=close > open)", "disable_alert"),
        ('strategy.close_all("flat", "filled")', "alert_message"),
        ('strategy.close_all("flat", "", false, true)', "disable_alert"),
        ('strategy.close("L", alert_message="filled")', "alert_message"),
        ('strategy.close("L", alert_message="")', "alert_message"),
        ('strategy.close("L", alert_message=str.tostring(close))', "alert_message"),
        ('strategy.close("L", disable_alert=true)', "disable_alert"),
        ('strategy.close("L", disable_alert=false)', "disable_alert"),
        ('strategy.close("L", disable_alert=close > open)', "disable_alert"),
        ('strategy.close("L", "flat", 1, 100, "filled")', "alert_message"),
        ('strategy.close("L", "flat", 1, 100, "", false, true)', "disable_alert"),
    ],
)
def test_strategy_close_alert_controls_warn_named_and_positional(call, param_name):
    src = PRELUDE + call + "\n"
    assert _errors(src) == []
    function_name = "close_all" if "strategy.close_all" in call else "close"
    warns = _warnings(src)
    assert any(
        d.message == (
            f"strategy.{function_name} parameter '{param_name}' is not "
            "supported by PineForge and is ignored."
        )
        for d in warns
    )


@pytest.mark.parametrize(
    "call",
    [
        'strategy.close_all(comment="flat", immediately=true)',
        'strategy.close_all("flat", "", true)',
        'strategy.close_all("flat", "", true, false)',
        'strategy.close("L", comment="flat", immediately=true)',
        'strategy.close("L", "flat", 1, 100, "", true)',
        'strategy.close("L", "flat", 1, 100, "", true, false)',
    ],
)
def test_strategy_close_comment_and_immediately_supported_named_and_positional(call):
    src = PRELUDE + call + "\n"
    assert _errors(src) == []
    warns = _warnings(src)
    assert not any(
        "strategy.close" in d.message
        and ("parameter 'comment'" in d.message or "parameter 'immediately'" in d.message)
        for d in warns
    )


def test_strategy_exit_qty_supported():
    """``strategy.exit(..., qty=...)`` is honoured by the runtime (Pine v6
    semantics: absolute exit qty per bracket), so the support checker
    must not flag it as unsupported. Companion runtime test in
    pineforge-engine: tests/test_strategy_oca.cpp::
    test_strategy_exit_two_brackets_independent_oca_groups."""
    src = PRELUDE + 'strategy.exit("X", "L", qty=1, stop=100)\n'
    assert _errors(src) == []
    assert not any("strategy.exit" in d.message and "qty" in d.message for d in _warnings(src))


def test_strategy_exit_positional_qty_supported():
    src = PRELUDE + 'strategy.exit("X", "L", 1, stop=100)\n'
    assert _errors(src) == []
    assert not any("strategy.exit" in d.message and "qty" in d.message for d in _warnings(src))


def test_strategy_exit_oca_name_supported():
    """``strategy.exit(..., oca_name=...)`` is now plumbed to the runtime
    so two brackets in different OCA groups fire independently."""
    src = PRELUDE + 'strategy.exit("X", "L", stop=100, oca_name="GRP_A")\n'
    assert _errors(src) == []
    assert not any("strategy.exit" in d.message and "oca_name" in d.message for d in _warnings(src))


def test_strategy_exit_without_price_rejected():
    src = PRELUDE + 'strategy.exit("X", "L", qty_percent=50)\n'
    _expect_error(src, "strategy.exit")


def test_unknown_strategy_function_rejected():
    src = PRELUDE + 'strategy.not_a_real_function(1)\n'
    _expect_error(src, "strategy.not_a_real_function")


def test_unknown_closedtrades_method_rejected():
    src = PRELUDE + 'x = strategy.closedtrades.not_a_real_method(0)\n'
    _expect_error(src, "strategy.closedtrades.not_a_real_method")


def test_strategy_risk_expression_warns_side_effect():
    src = PRELUDE + 'x = strategy.risk.max_position_size(10)\n'
    assert _errors(src) == []
    assert any("strategy.risk" in d.message for d in _warnings(src))


def test_ta_stoch_tuple_assignment_rejected():
    src = PRELUDE + '[k, d] = ta.stoch(close, high, low, 14)\n'
    _expect_error(src, "ta.stoch")


# ---------------------------------------------------------------------------
# Visual-skip warnings (not errors)
# ---------------------------------------------------------------------------

def test_plot_emits_warning_not_error():
    src = PRELUDE + "plot(close)\n"
    assert _errors(src) == []
    assert any("visual only" in d.message for d in _warnings(src))


def test_label_geometry_accepted_visual_setter_warns():
    # Drawing-objects-as-data: label.new is REAL geometry (a label in the
    # per-type arena) — accepted, no hard error and no "visual only" warning.
    assert _errors(PRELUDE + 'label.new(bar_index, high, "x")\n') == []
    # A pure-visual setter (label.set_color) is the part that is a no-op: it is
    # accepted but warned, never rejected.
    src2 = PRELUDE + 'lb = label.new(bar_index, high, "x")\nlabel.set_color(lb, color.red)\n'
    assert _errors(src2) == []
    assert any("visual no-op" in d.message for d in _warnings(src2))
    # An UNKNOWN drawing method rejects loudly (not silently emitted).
    assert _errors(PRELUDE + 'lb = label.new(bar_index, high, "x")\nlabel.bogus(lb)\n')


def test_table_cell_visual_wrapper_allows_style_constants():
    src = PRELUDE + """
cell(table t, int c, int r, string txt, align) =>
    t.cell(c, r, txt, text_halign = align)

var table dash = table.new(position.top_right, 1, 1)
cell(dash, 0, 0, "ok", text.align_right)
"""
    assert _errors(src) == []


def test_visual_style_constant_still_rejected_in_non_visual_wrapper():
    src = PRELUDE + """
passthrough(x) => x
v = passthrough(text.align_right)
"""
    _expect_error(src, "text.align_right")


def test_udt_drawing_field_history_rejected():
    src = PRELUDE + """
type DrawState
    line ln = na
var DrawState d = DrawState.new()
d.ln := line.new(bar_index, close, bar_index + 1, close)
prev = d.ln[1]
"""
    _expect_error(src, "UDT drawing fields")


def test_udt_array_drawing_field_history_rejected():
    src = PRELUDE + """
type DrawState
    array<line> lines = na
var DrawState d = DrawState.new(array.new<line>())
prev = d.lines[1]
"""
    _expect_error(src, "UDT drawing fields")


def test_udt_bracket_array_drawing_field_history_rejected():
    src = PRELUDE + """
type DrawState
    line[] lines = na
var DrawState d = DrawState.new(array.new<line>())
prev = d.lines[1]
"""
    _expect_error(src, "UDT drawing fields")


def test_udt_drawing_field_current_bar_zero_allowed():
    src = PRELUDE + """
type DrawState
    line ln = na
var DrawState d = DrawState.new()
d.ln := line.new(bar_index, close, bar_index + 1, close)
same = d.ln[0]
"""
    assert _errors(src) == []


def test_udt_non_drawing_field_history_allowed():
    src = PRELUDE + """
type State
    float n = 0.0
var State d = State.new()
prev = d.n[1]
"""
    assert _errors(src) == []


def test_tuple_destructured_drawing_handle_history_rejected():
    src = PRELUDE + """
makePair() => [line.new(bar_index, close, bar_index + 1, close), line.new(bar_index, open, bar_index + 1, open)]
[la, lb] = makePair()
prev = la[1]
"""
    _expect_error(src, "tuple-destructured drawing handles")


def test_tuple_literal_drawing_handle_history_rejected():
    src = PRELUDE + """
[la, lb] = [line.new(bar_index, close, bar_index + 1, close), line.new(bar_index, open, bar_index + 1, open)]
prev = lb[1]
"""
    _expect_error(src, "tuple-destructured drawing handles")


def test_tuple_ternary_drawing_handle_history_rejected():
    src = PRELUDE + """
makePair() => [close > open ? line.new(bar_index, close, bar_index + 1, close) : na, line.new(bar_index, open, bar_index + 1, open)]
[la, lb] = makePair()
prev = la[1]
"""
    _expect_error(src, "tuple-destructured drawing handles")


def test_numeric_tuple_element_history_still_allowed():
    src = PRELUDE + """
makePair() => [close, open]
[a, b] = makePair()
prev = a[1]
"""
    assert _errors(src) == []


# ---------------------------------------------------------------------------
# Identifier-resolution edge cases
# ---------------------------------------------------------------------------

def test_user_function_with_builtin_name_does_not_trigger_unknown_ta():
    """User functions that shadow nothing in built-ins must not be flagged."""
    src = PRELUDE + "myFunc(x) =>\n    x * 2\ny = myFunc(close)\n"
    assert _errors(src) == []


def test_user_method_accepted():
    src = (PRELUDE +
           "type bar\n    float v = 0.0\n"
           "method scale(bar self, float k) =>\n    self.v * k\n")
    assert _errors(src) == []


# ---------------------------------------------------------------------------
# Rule-table integrity guards
# ---------------------------------------------------------------------------

def test_security_allowed_params_locked():
    """Lock the policy so future edits are deliberate.

    G2 sprint: backadjustment / settlement_as_close / adjustment added so scripts
    using those request.security kwargs compile instead of being hard-rejected.
    Codegen silently drops them (engine uses fixed unadjusted data).
    ignore_invalid_symbol added: a guaranteed no-op on the forced chart symbol.
    """
    assert SECURITY_ALLOWED_PARAMS == frozenset(
        {"symbol", "timeframe", "expression", "gaps", "lookahead",
         "ignore_invalid_symbol",
         "backadjustment", "settlement_as_close", "adjustment"}
    )


def test_hard_reject_includes_external_request_feeds():
    """request.financial / dividends / earnings / splits / seed / quandl / currency_rate
    remain hard-rejected because PineForge has no auxiliary-data ingestion path.
    request.security_lower_tf was removed from this list when lower-TF arrays landed."""
    for fn in (
        "request.financial",
        "request.dividends",
        "request.earnings",
        "request.splits",
        "request.seed",
        "request.quandl",
        "request.currency_rate",
    ):
        assert fn in HARD_REJECT_FUNC, f"{fn} should remain hard-rejected"
    assert "request.security_lower_tf" not in HARD_REJECT_FUNC, (
        "request.security_lower_tf is now supported; it should not be in HARD_REJECT_FUNC"
    )


def test_hard_reject_namespace_includes_ticker():
    # G2 sprint: ticker blanket-reject moved to per-function entries in HARD_REJECT_FUNC
    # (ticker.inherit and ticker.standard are now passthroughs, not rejects).
    assert "ticker" not in HARD_REJECT_NAMESPACE, (
        "ticker blanket-reject was converted to per-function entries (G2 sprint); "
        "ticker.inherit/standard are now passthrough."
    )
    # ticker.heikinashi is contextually supported for the chart's own symbol
    # (handled in _visit_FuncCall), so it is NOT in the blanket hard-reject set.
    assert "ticker.heikinashi" not in HARD_REJECT_FUNC
    # The remaining 6 chart-type modifier / cross-symbol construction functions
    # stay hard-rejected.
    for fn in (
        "ticker.renko", "ticker.kagi", "ticker.linebreak",
        "ticker.pointfigure", "ticker.new", "ticker.modify",
    ):
        assert fn in HARD_REJECT_FUNC, f"{fn} should be hard-rejected per G2 spec"


def test_not_yet_excludes_format_time():
    assert "str.format_time" not in NOT_YET_FUNC


def test_divergent_includes_bar_index():
    assert "bar_index" in DIVERGENT_VARS


# ---------------------------------------------------------------------------
# G2 sprint: ticker per-function split tests
# ---------------------------------------------------------------------------

def test_ticker_heikinashi_chart_symbol_accepted():
    # ticker.heikinashi(syminfo.tickerid) is the chart's OWN symbol with a causal
    # Heikin-Ashi candle transform; the engine applies it inside request.security
    # (register_security_eval heikinashi flag). Supported — directly and via alias.
    src = (PRELUDE
           + 'ha = ticker.heikinashi(syminfo.tickerid)\n'
           + 'v = request.security(ha, "60", close, lookahead = barmerge.lookahead_off)\n'
           + 'plot(v)\n')
    assert _errors(src) == []
    # Inline (un-aliased) symbol form is equally accepted.
    src2 = (PRELUDE
            + 'v = request.security(ticker.heikinashi(syminfo.tickerid), "60", close)\n'
            + 'plot(v)\n')
    assert _errors(src2) == []


def test_ticker_heikinashi_cross_symbol_rejected():
    # Heikin-Ashi of a DIFFERENT symbol is genuine cross-symbol construction —
    # PineForge cannot load an alternate symbol's HA candles.
    src = PRELUDE + 't = ticker.heikinashi("BINANCE:BTCUSDT")\n'
    _expect_error(src, "ticker.heikinashi")


def test_ticker_renko_rejected():
    src = PRELUDE + 't = ticker.renko(syminfo.tickerid, "ATR", 10)\n'
    _expect_error(src, "ticker.renko")


def test_ticker_new_rejected():
    src = PRELUDE + 't = ticker.new(syminfo.prefix, syminfo.ticker)\n'
    _expect_error(src, "ticker.new")


def test_ticker_modify_rejected():
    src = PRELUDE + 't = ticker.modify(syminfo.tickerid)\n'
    _expect_error(src, "ticker.modify")


def test_ticker_inherit_not_rejected():
    """ticker.inherit is a passthrough — should not produce a hard-reject error."""
    src = PRELUDE + 'sym = ticker.inherit(syminfo.tickerid)\n'
    assert _errors(src) == [], \
        f"ticker.inherit should not be rejected, got: {[d.message for d in _errors(src)]}"


def test_ticker_inherit_passthrough_in_request_security():
    """ticker.inherit(syminfo.tickerid) used directly as symbol arg should compile."""
    from pineforge_codegen import transpile
    src = PRELUDE + 'data = request.security(ticker.inherit(syminfo.tickerid), "D", close)\n'
    # Should not raise a CompileError — ticker.inherit is accepted as current-symbol passthrough
    cpp = transpile(src)
    assert cpp  # generated non-empty C++


def test_ticker_standard_not_rejected():
    """ticker.standard is a passthrough — should not produce a hard-reject error."""
    src = PRELUDE + 'sym = ticker.standard(syminfo.tickerid)\n'
    assert _errors(src) == [], \
        f"ticker.standard should not be rejected, got: {[d.message for d in _errors(src)]}"


def test_ticker_standard_passthrough_in_request_security():
    """ticker.standard(syminfo.tickerid) used directly as symbol arg should compile."""
    from pineforge_codegen import transpile
    src = PRELUDE + 'data = request.security(ticker.standard(syminfo.tickerid), "D", close)\n'
    cpp = transpile(src)
    assert cpp  # generated non-empty C++


# ---------------------------------------------------------------------------
# G2 sprint: syminfo na-accept + silent-gap warnings
# ---------------------------------------------------------------------------

def test_syminfo_prefix_accepted():
    """syminfo.prefix was silently emitting 0; now accepted as na<string>()."""
    assert _errors(PRELUDE + 'x = syminfo.prefix\n') == []


def test_syminfo_root_accepted():
    assert _errors(PRELUDE + 'x = syminfo.root\n') == []


def test_syminfo_pricescale_accepted():
    assert _errors(PRELUDE + 'x = syminfo.pricescale\n') == []


def test_syminfo_minmove_accepted():
    assert _errors(PRELUDE + 'x = syminfo.minmove\n') == []


def test_syminfo_sector_accepted():
    assert _errors(PRELUDE + 'x = syminfo.sector\n') == []


def test_syminfo_industry_accepted():
    assert _errors(PRELUDE + 'x = syminfo.industry\n') == []


def test_syminfo_isin_accepted():
    assert _errors(PRELUDE + 'x = syminfo.isin\n') == []


def test_syminfo_expiration_date_accepted():
    assert _errors(PRELUDE + 'x = syminfo.expiration_date\n') == []


def test_syminfo_current_contract_accepted():
    assert _errors(PRELUDE + 'x = syminfo.current_contract\n') == []


def test_syminfo_mincontract_accepted():
    assert _errors(PRELUDE + 'x = syminfo.mincontract\n') == []


def test_syminfo_main_tickerid_accepted():
    assert _errors(PRELUDE + 'x = syminfo.main_tickerid\n') == []


def test_syminfo_country_accepted():
    assert _errors(PRELUDE + 'x = syminfo.country\n') == []


def test_syminfo_sector_conditional_warns():
    """syminfo.sector in if-condition should produce silent-gap warning."""
    src = PRELUDE + 'if syminfo.sector == "Technology"\n    strategy.entry("L", strategy.long)\n'
    warns = _warnings(src)
    assert any("sector" in d.message and "returns na" in d.message for d in warns), \
        f"Expected silent-gap warning for syminfo.sector, got: {[d.message for d in warns]}"


def test_syminfo_industry_conditional_warns():
    src = PRELUDE + 'if syminfo.industry == "Software"\n    strategy.entry("L", strategy.long)\n'
    warns = _warnings(src)
    assert any("industry" in d.message and "returns na" in d.message for d in warns)


def test_syminfo_isin_conditional_warns():
    src = PRELUDE + 'if syminfo.isin != ""\n    strategy.entry("L", strategy.long)\n'
    warns = _warnings(src)
    assert any("isin" in d.message and "returns na" in d.message for d in warns)


def test_syminfo_sector_non_conditional_warns():
    """A silent-gap field used OUTSIDE a conditional must ALSO warn now — the
    field still slips out as na, so the read deserves the same signal as a
    conditional use (previously it was silently dropped)."""
    src = PRELUDE + 'x = syminfo.sector\n'
    warns = _warnings(src)
    assert any("sector" in d.message and "returns na" in d.message for d in warns), \
        f"Expected silent-gap warning for plain syminfo.sector, got: {[d.message for d in warns]}"
    # Stays a WARNING, never escalated to ERROR.
    assert _errors(src) == []


# ---------------------------------------------------------------------------
# G2 sprint: backadjustment / settlement_as_close constants
# Phase C: these were previously silently accepted then dropped by codegen,
# producing different prices from TradingView with no warning. The new
# contract REJECTS active values; only the no-op set is allowed (see
# tests/test_support_checker_security_adjustment.py for full coverage).
# ---------------------------------------------------------------------------

def test_backadjustment_on_in_request_security_rejected():
    """Phase C: backadjustment.on is a silent-wrong-result kwarg — reject."""
    src = (PRELUDE +
           'data = request.security(syminfo.tickerid, "D", close, backadjustment=backadjustment.on)\n')
    _expect_error(src, "backadjustment")


def test_settlement_as_close_on_in_request_security_rejected():
    """Phase C: settlement_as_close.on is a silent-wrong-result kwarg — reject."""
    src = (PRELUDE +
           'data = request.security(syminfo.tickerid, "D", close, settlement_as_close=settlement_as_close.on)\n')
    _expect_error(src, "settlement_as_close")


def test_adjustment_dividends_in_request_security_rejected():
    """Phase C: adjustment.dividends is a silent-wrong-result kwarg — reject."""
    src = (PRELUDE +
           'data = request.security(syminfo.tickerid, "D", close, adjustment=adjustment.dividends)\n')
    _expect_error(src, "adjustment")

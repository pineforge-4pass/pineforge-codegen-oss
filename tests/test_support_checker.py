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
# Divergent built-in variables — warn, don't reject
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var_name", sorted(DIVERGENT_VARS))
def test_divergent_variables_warn(var_name: str):
    src = PRELUDE + f"x = {var_name}\n"
    assert _errors(src) == [], f"{var_name} should warn, not error"
    warns = _warnings(src)
    assert any("diverges" in d.message for d in warns), \
        f"expected divergence warning for {var_name}, got {[d.message for d in warns]}"


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


def test_color_from_gradient_rejected():
    src = PRELUDE + "c = color.from_gradient(close, 0, 100, color.red, color.green)\n"
    _expect_error(src, "color.from_gradient")


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


def test_max_bars_back_rejected():
    src = PRELUDE + "max_bars_back(close, 500)\n"
    _expect_error(src, "max_bars_back")


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


def test_request_security_lookahead_on_kwarg_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'lookahead=barmerge.lookahead_on)\n')
    _expect_error(src, "lookahead_on")


def test_request_security_lookahead_on_positional_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'barmerge.gaps_off, barmerge.lookahead_on)\n')
    _expect_error(src, "lookahead_on")


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


def test_request_security_ignore_invalid_symbol_kwarg_rejected():
    src = (PRELUDE +
           'a = request.security(syminfo.tickerid, "60", close, '
           'ignore_invalid_symbol=true)\n')
    _expect_error(src, "ignore_invalid_symbol")


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


def test_barstate_approximation_warns():
    src = PRELUDE + "x = barstate.islast\n"
    assert _errors(src) == []
    assert any("barstate.islast" in d.message for d in _warnings(src))


def test_unsupported_strategy_entry_params_warn():
    src = PRELUDE + 'strategy.entry("L", strategy.long, oca_name="g", qty_type=strategy.cash)\n'
    assert _errors(src) == []
    warns = _warnings(src)
    assert not any("strategy.entry" in d.message and "oca_name" in d.message for d in warns)
    assert not any("strategy.entry" in d.message and "qty_type" in d.message for d in warns)


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


def test_label_namespace_emits_warning_not_error():
    src = PRELUDE + 'label.new(bar_index, high, "x")\n'
    # bar_index now warns (divergent); label.new also warns (visual).
    warnings = _warnings(src)
    assert any("visual only" in d.message for d in warnings)


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
    """
    assert SECURITY_ALLOWED_PARAMS == frozenset(
        {"symbol", "timeframe", "expression", "gaps", "lookahead",
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
    # The 7 chart-type modifier / cross-symbol construction functions remain hard-rejected.
    for fn in (
        "ticker.heikinashi", "ticker.renko", "ticker.kagi", "ticker.linebreak",
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

def test_ticker_heikinashi_rejected():
    src = PRELUDE + 't = ticker.heikinashi(syminfo.tickerid)\n'
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


def test_syminfo_sector_non_conditional_no_warn():
    """Using syminfo.sector outside a conditional should NOT produce silent-gap warning."""
    src = PRELUDE + 'x = syminfo.sector\n'
    warns = _warnings(src)
    assert not any("returns na" in d.message for d in warns), \
        f"Unexpected silent-gap warning outside conditional: {[d.message for d in warns]}"


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

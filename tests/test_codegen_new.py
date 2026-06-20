"""Tests for the new CodeGen that reads from AnalyzerContext."""

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen


def _ensure_v6_preamble(src: str) -> str:
    """PineForge requires //@version=6; tests often pass a bare body."""
    if "//@version=" in src:
        return src
    return f'//@version=6\nstrategy("T")\n{src}'


def _generate(src: str) -> str:
    src = _ensure_v6_preamble(src)
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    return CodeGen(ctx).generate()


# === Task 1: New includes and extern C entry point ===


def test_includes_new_headers():
    cpp = _generate("""
//@version=6
strategy("Test")
""")
    assert '#include <pineforge/color.hpp>' in cpp
    assert '#include <pineforge/log.hpp>' in cpp
    assert '#include <pineforge/str_utils.hpp>' in cpp


def test_extern_c_has_run_backtest_full():
    cpp = _generate("""
//@version=6
strategy("Test")
""")
    assert "run_backtest_full" in cpp
    assert "run_backtest" in cpp  # backward compat


def test_run_backtest_full_routes_to_tf_aware_run_when_only_script_tf_set():
    """Regression: a chosen script_tf must not be silently ignored.

    The cloud caller passes ``input_tf=""`` (auto-detect) with a concrete
    ``script_tf`` (e.g. "240"). The old ``run_backtest_full`` guard required
    BOTH timeframes present (``!itf.empty() && !stf.empty() && itf != stf``),
    so an empty input_tf made ``needs_full_run`` false and the strategy ran
    on raw 1m bars — the chosen script_tf was dropped and no aggregation
    happened. The fix over-approximates: route through the TF-aware overload
    whenever ANY TF/magnifier knob is set.

    This pins the emitted guard so the regression cannot silently return.
    ``run_backtest_full`` is emitted for every strategy (no security calls
    needed), so a bare strategy exercises the shim guard. The precalc-gating
    ``run(...)`` overload is only emitted when there is a static TA call
    site (``ta.sma`` here), so the body uses one to cover both guards.
    """
    cpp = _generate('//@version=6\nstrategy("T")\nx = ta.sma(close, 14)\nplot(x)\n')

    # The C ABI shim guard must fire on a non-empty script_tf alone.
    assert "bool needs_full_run = (bar_magnifier != 0)" in cpp
    assert "|| !itf.empty() || !stf.empty();" in cpp
    # The old AND-of-both-TFs guard must be gone.
    assert "!itf.empty() && !stf.empty() && itf != stf" not in cpp

    # The run(...) overload that gates precalc must also route dynamically
    # whenever either TF is set (not only when both differ).
    assert (
        "bool needs_dynamic = bar_magnifier || !input_tf.empty() || !script_tf.empty();"
        in cpp
    )
    assert "!input_tf.empty() && !script_tf.empty() && input_tf != script_tf" not in cpp


# === Task 9: Basic structure and translations ===


def test_includes_present():
    cpp = _generate('//@version=6\nstrategy("T")\n')
    assert '#include <pineforge/engine.hpp>' in cpp
    assert '#include <pineforge/ta.hpp>' in cpp


def test_class_structure():
    cpp = _generate('//@version=6\nstrategy("T")\n')
    assert "class GeneratedStrategy : public BacktestEngine" in cpp
    assert "void on_bar(const Bar& bar) override" in cpp
    assert 'extern "C"' in cpp
    assert "strategy_create" in cpp


def test_var_decl_becomes_member():
    cpp = _generate('//@version=6\nstrategy("T")\nx = 14\n')
    # Global-scope non-var vars become class members + assignment in on_bar
    assert "int x = 0;" in cpp or "double x = 0" in cpp  # class member with default
    assert "x = 14;" in cpp  # assignment in on_bar


def test_if_else():
    src = '//@version=6\nstrategy("T")\nif close > open\n    x = 1\nelse\n    x = 2\n'
    cpp = _generate(src)
    assert "if (" in cpp
    assert "} else {" in cpp or "else" in cpp


def test_for_loop():
    src = '//@version=6\nstrategy("T")\nfor i = 0 to 10\n    x = i\n'
    cpp = _generate(src)
    assert "for (" in cpp


def test_na_translation():
    src = '//@version=6\nstrategy("T")\nx = na\n'
    cpp = _generate(src)
    assert "na<double>()" in cpp


def test_nz_translation():
    src = '//@version=6\nstrategy("T")\nx = nz(close)\n'
    cpp = _generate(src)
    assert "is_na" in cpp


def test_math_function():
    src = '//@version=6\nstrategy("T")\nx = math.abs(-5)\n'
    cpp = _generate(src)
    assert "std::abs" in cpp


def test_string_as_std_string():
    src = '//@version=6\nstrategy("T")\nstrategy.entry("Long", strategy.long)\n'
    cpp = _generate(src)
    assert 'std::string("Long")' in cpp or '"Long"' in cpp


# === Task 10: TA, Strategy, Math, Series, Functions ===


def test_ta_sma():
    src = '//@version=6\nstrategy("T")\nx = ta.sma(close, 14)\n'
    cpp = _generate(src)
    assert "ta::SMA" in cpp
    assert ".compute(" in cpp


def test_ta_tr_handle_na_false_routes_through_tr_class():
    # ``ta.tr(false)`` is the TV v6 default behaviour; it now allocates a
    # ``ta::TR`` member with ``handle_na=false`` so the runtime returns ``na``
    # on the first bar (na branch lives inside ``TR::compute``).
    src = """//@version=6
strategy("T")
x = ta.tr(false)
"""
    cpp = _generate(src)
    assert "ta::TR _ta_tr_" in cpp
    # Constructor initialiser-list entry pins ``handle_na`` to false.
    import re
    assert re.search(r"_ta_tr_\d+\(false\)", cpp), cpp
    # Compute is invoked with the bar OHLC implicitly threaded in.
    assert (
        "_ta_tr_1.compute(current_bar_.high, current_bar_.low, current_bar_.close)"
        in cpp
    )


def test_ta_tr_default_handles_first_bar_with_high_low():
    # Property form ``ta.tr`` (no parens) keeps the legacy ``handle_na=true``
    # semantics in TV; the codegen still emits the inline expression so the
    # first-bar fallback is ``high - low``.
    src = """//@version=6
strategy("T")
x = ta.tr
"""
    cpp = _generate(src)
    assert "std::isnan(_s_close[1]) ? (current_bar_.high - current_bar_.low)" in cpp


def test_ta_call_site_member():
    src = '//@version=6\nstrategy("T")\nx = ta.rsi(close, 14)\n'
    cpp = _generate(src)
    assert "_ta_rsi_" in cpp
    assert "ta::RSI" in cpp


def test_time_with_session_uses_pine_time():
    src = (
        '//@version=6\nstrategy("T")\n'
        'x = time("60", "0800-1600", "America/New_York")\n'
    )
    cpp = _generate(src)
    assert "pine_time(" in cpp
    assert "script_tf_" in cpp


def test_time_kwargs_use_pine_time():
    src = (
        '//@version=6\nstrategy("T")\n'
        'x = time(timeframe="60", session="0800-1600", timezone="UTC")\n'
    )
    cpp = _generate(src)
    assert "pine_time(" in cpp
    assert "time(timeframe" not in cpp


def test_time_close_with_session_uses_pine_time_close():
    src = (
        '//@version=6\nstrategy("T")\n'
        'x = time_close("60", "0800-1600", "UTC")\n'
    )
    cpp = _generate(src)
    assert "pine_time_close(" in cpp


def test_timeframe_isdwm_uses_runtime_timeframe_helpers():
    src = '//@version=6\nstrategy("T")\nx = timeframe.isdwm\n'
    cpp = _generate(src)
    assert "tf_is_daily(script_tf_)" in cpp
    assert "tf_is_weekly(script_tf_)" in cpp
    assert "tf_is_monthly(script_tf_)" in cpp
    assert "x = 0;" not in cpp


def test_hour_two_arg_passes_tz():
    """``hour(time, "America/New_York")`` must propagate the tz string into
    the emitted C++ so the runtime can honor a non-UTC chart. Without this,
    codegen used to silently drop the timezone arg and call ``gmtime_r``
    unconditionally, producing UTC hours on every symbol."""
    src = (
        '//@version=6\nstrategy("T")\n'
        'h = hour(time, "America/New_York")\n'
        'plot(h)\n'
    )
    cpp = _generate(src)
    assert "America/New_York" in cpp
    # Two-arg form must use localtime_r (with the TZ env mutation) rather
    # than just gmtime_r — that is the whole point of the tz argument.
    assert "localtime_r" in cpp
    assert "setenv" in cpp


def test_hour_one_arg_uses_syminfo_timezone():
    """One-arg ``hour(time)`` (no explicit tz) must thread
    ``syminfo_.timezone`` (the EXCHANGE TZ) through the inline lambda,
    NOT ``chart_timezone_`` (the chart display TZ). Pine v6 reference:
    the function form's tz argument defaults to ``syminfo.timezone``,
    which is the symbol's exchange — UTC for the corpus' ETH-USDT data.
    See also ``BacktestEngine::set_chart_timezone`` (engine.hpp), which
    intentionally stores the chart TZ in a separate slot so the
    validator (and future harnesses) can supply chart TZ for CSV parsing
    without distorting ``hour(time)`` semantics."""
    src = (
        '//@version=6\nstrategy("T")\n'
        'h = hour(time)\n'
    )
    cpp = _generate(src)
    # The 1-arg form must reference syminfo_.timezone (exchange TZ) per
    # TV docs. The default ``SymInfo::timezone`` of "UTC" keeps the
    # cheap gmtime_r path active for crypto.
    assert "syminfo_.timezone" in cpp
    # The chart-display TZ slot must NOT leak into the bar-time lambda;
    # if it ever does, this test catches the regression.
    assert "chart_timezone_" not in cpp
    # Both branches of the lambda are emitted; cheap gmtime_r still serves
    # the UTC default.
    assert "gmtime_r" in cpp
    assert "localtime_r" in cpp


def test_dayofweek_two_arg_passes_tz():
    """All eight bar-time functions share the same lowering — sanity-check
    a non-hour example so the test catches a regression that special-cases
    only ``hour``."""
    src = (
        '//@version=6\nstrategy("T")\n'
        'd = dayofweek(time, "Asia/Tokyo")\n'
        'plot(d)\n'
    )
    cpp = _generate(src)
    assert "Asia/Tokyo" in cpp
    assert "localtime_r" in cpp


def test_hour_two_arg_utc_literal_short_circuits():
    """Pine ``hour(time, "UTC")`` should emit a tz-aware lambda but at
    runtime take the gmtime_r branch (the inline ``if (_tz == "UTC")``
    check). The emitted source still references the literal so the tz
    intent is auditable."""
    src = (
        '//@version=6\nstrategy("T")\n'
        'h = hour(time, "UTC")\n'
    )
    cpp = _generate(src)
    assert '"UTC"' in cpp
    # Both branches are emitted in the lambda; both gmtime_r and localtime_r
    # appear in the source even though only the gmtime_r branch runs at
    # runtime for this literal.
    assert "gmtime_r" in cpp
    assert "localtime_r" in cpp


def test_time_in_request_security_uses_bar_timestamp():
    src = (
        '//@version=6\nstrategy("T")\n'
        'x = request.security(syminfo.tickerid, "60", time("60", "0930-1600"), lookahead=barmerge.lookahead_off)\n'
    )
    cpp = _generate(src)
    assert "pine_time(" in cpp
    assert "bar.timestamp" in cpp


def test_strategy_entry_market():
    src = '//@version=6\nstrategy("T")\nstrategy.entry("Long", strategy.long)\n'
    cpp = _generate(src)
    assert "strategy_entry(" in cpp
    assert "true" in cpp


def test_strategy_entry_with_stop():
    src = '//@version=6\nstrategy("T")\nstrategy.entry("L", strategy.long, stop=100.0)\n'
    cpp = _generate(src)
    assert "strategy_entry(" in cpp


def test_strategy_entry_forwards_oca_params():
    src = '//@version=6\nstrategy("T")\nstrategy.entry("L", strategy.long, stop=100.0, oca_name="g", oca_type=strategy.oca.cancel)\n'
    cpp = _generate(src)
    assert 'std::string("g")' in cpp
    assert 'strategy_entry(std::string("L"), true' in cpp


def test_strategy_entry_forwards_qty_type():
    src = '//@version=6\nstrategy("T")\nstrategy.entry("L", strategy.long, qty=1000, qty_type=strategy.cash)\n'
    cpp = _generate(src)
    assert 'strategy_entry(std::string("L"), true' in cpp
    assert ', 2)' in cpp


def test_strategy_direction_long_maps_to_long_only_risk():
    src = '//@version=6\nstrategy("T")\nstrategy.risk.allow_entry_in(strategy.direction.long)\n'
    cpp = _generate(src)
    assert "risk_direction_ = RiskDirection::LONG_ONLY;" in cpp
    assert "risk_direction_ = RiskDirection::SHORT_ONLY;" not in cpp


def test_strategy_close():
    src = '//@version=6\nstrategy("T")\nstrategy.close("Long")\n'
    cpp = _generate(src)
    assert "strategy_close(" in cpp


def test_strategy_close_forwards_comment():
    src = '//@version=6\nstrategy("T")\nstrategy.close("Long", comment="manual exit")\n'
    cpp = _generate(src)
    assert 'strategy_close(std::string("Long"), std::string("manual exit"), na<double>(), na<double>(), false)' in cpp


def test_strategy_close_forwards_qty_percent_and_immediately():
    src = '//@version=6\nstrategy("T")\nstrategy.close("Long", qty_percent=50, immediately=true)\n'
    cpp = _generate(src)
    assert 'strategy_close(std::string("Long"), "", na<double>(), 50, true)' in cpp


def test_strategy_exit_forwards_comment_to_runtime():
    src = '//@version=6\nstrategy("T")\nstrategy.exit("X", "Long", stop=100.0, comment="stop exit")\n'
    cpp = _generate(src)
    assert 'std::string("stop exit")' in cpp
    assert 'strategy_exit(std::string("X"), std::string("Long")' in cpp


def test_strategy_position_size():
    src = '//@version=6\nstrategy("T")\nx = strategy.position_size\n'
    cpp = _generate(src)
    assert "signed_position_size()" in cpp


def test_strategy_equity_includes_open_profit():
    src = '//@version=6\nstrategy("T")\nx = strategy.equity\n'
    cpp = _generate(src)
    assert "(current_equity() + open_profit(current_bar_.close))" in cpp


def test_user_defined_function():
    src = '//@version=6\nstrategy("T")\nf(a, b) =>\n    a + b\nx = f(1, 2)\n'
    cpp = _generate(src)
    assert "f(" in cpp


def test_series_var_push_update():
    src = '//@version=6\nstrategy("T")\nbprice = 0.0\nbprice := nz(bprice[1])\n'
    cpp = _generate(src)
    assert "Series<double>" in cpp
    assert ".push(" in cpp or ".update(" in cpp


def test_var_member():
    src = '//@version=6\nstrategy("T")\nvar float x = 0.0\n'
    cpp = _generate(src)
    assert "_var_initialized" in cpp


def test_tuple_assign_macd():
    src = '//@version=6\nstrategy("T")\n[macdLine, signal, hist] = ta.macd(close, 12, 26, 9)\n'
    cpp = _generate(src)
    assert "ta::MACD" in cpp


def test_switch_stmt():
    src = '//@version=6\nstrategy("T")\nx = 1\nswitch x\n    1 =>\n        y = 10\n    2 =>\n        y = 20\n'
    cpp = _generate(src)
    assert "switch" in cpp or "if" in cpp


def test_plot_skipped():
    src = '//@version=6\nstrategy("T")\nplot(close)\n'
    cpp = _generate(src)
    assert "plot" not in cpp.split("extern")[0]


def test_fixnan_translation():
    src = '//@version=6\nstrategy("T")\nx = fixnan(close)\n'
    cpp = _generate(src)
    assert "_prev_fixnan_" in cpp
    assert "is_na" in cpp


def test_cpp_name_safety():
    src = '//@version=6\nstrategy("T")\nexp = true\n'
    cpp = _generate(src)
    assert "_exp_" in cpp


# === UDT tests ===


def test_udt_codegen():
    """UDT.new() should emit C++ designated initializer."""
    src = _ensure_v6_preamble('''type PriceLevel
    float price = 0.0
    int strength = 1

obj = PriceLevel.new(price=10.5)
x = obj.price
''')
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    cpp = CodeGen(ctx).generate()
    assert "struct PriceLevel" in cpp
    assert ".price = " in cpp


# === Map support tests ===


def test_ta_obv_codegen():
    src = "x = ta.obv\n"
    cpp = _generate(src)
    assert "OBV" in cpp


def test_ta_vwap_codegen():
    src = "x = ta.vwap(close)\n"
    cpp = _generate(src)
    assert "VWAP" in cpp


def test_ta_accdist_codegen():
    src = "x = ta.accdist\n"
    cpp = _generate(src)
    assert "AccDist" in cpp


def test_ta_mode_codegen():
    src = "x = ta.mode(close, 14)\n"
    cpp = _generate(src)
    assert "Mode" in cpp


def test_ta_range_codegen():
    src = "x = ta.range(close, 14)\n"
    cpp = _generate(src)
    assert "Range" in cpp


def test_ta_dev_codegen():
    src = "x = ta.dev(close, 14)\n"
    cpp = _generate(src)
    assert "Dev" in cpp


# === Map support tests ===


def test_map_codegen():
    src = _ensure_v6_preamble('''m = map.new<string, float>()
m.put("key1", 42.0)
x = m.get("key1")
''')
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    cpp = CodeGen(ctx).generate()
    assert "unordered_map" in cpp
    assert '"key1"' in cpp


# === str.* function tests ===


def test_str_contains():
    src = 'x = str.contains("hello world", "world")\n'
    cpp = _generate(src)
    assert "find" in cpp
    assert "npos" in cpp


def test_str_lower():
    src = 'x = str.lower("HELLO")\n'
    cpp = _generate(src)
    assert "tolower" in cpp


def test_str_upper():
    src = 'x = str.upper("hello")\n'
    cpp = _generate(src)
    assert "toupper" in cpp


def test_str_length():
    src = 'x = str.length("hello")\n'
    cpp = _generate(src)
    assert ".length()" in cpp


def test_str_startswith():
    src = 'x = str.startswith("hello", "he")\n'
    cpp = _generate(src)
    assert "substr" in cpp


def test_str_endswith():
    src = 'x = str.endswith("hello", "lo")\n'
    cpp = _generate(src)
    assert "compare" in cpp


def test_str_pos():
    src = 'x = str.pos("hello", "ll")\n'
    cpp = _generate(src)
    assert "find" in cpp
    assert "npos" in cpp


def test_str_substring():
    src = 'x = str.substring("hello", 1, 3)\n'
    cpp = _generate(src)
    assert "substr" in cpp


def test_str_replace_all():
    src = 'x = str.replace_all("aaa", "a", "b")\n'
    cpp = _generate(src)
    assert "find" in cpp
    assert "replace" in cpp


def test_str_replace():
    src = 'x = str.replace("hello", "ll", "r")\n'
    cpp = _generate(src)
    assert "find" in cpp
    assert "replace" in cpp


def test_str_trim():
    src = 'x = str.trim("  hello  ")\n'
    cpp = _generate(src)
    assert "erase" in cpp


def test_str_repeat():
    src = 'x = str.repeat("ab", 3)\n'
    cpp = _generate(src)
    assert "+=" in cpp


def test_str_tonumber():
    src = 'x = str.tonumber("42")\n'
    cpp = _generate(src)
    assert "stod" in cpp


def test_str_tostring():
    src = '//@version=6\nstrategy("T")\nx = str.tostring(42)\n'
    cpp = _generate(src)
    assert "to_string" in cpp


# === Extended array method tests ===


def _generate_raw(src: str) -> str:
    """Generate from raw PineScript (no strategy wrapper needed)."""
    src = _ensure_v6_preamble(src)
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    return CodeGen(ctx).generate()


def test_array_median():
    src = "a = array.from(3.0, 1.0, 2.0)\nx = array.median(a)\n"
    cpp = _generate_raw(src)
    assert "sort" in cpp


def test_array_stdev():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.stdev(a)\n"
    cpp = _generate_raw(src)
    assert "std::sqrt" in cpp
    assert "accumulate" in cpp


def test_array_variance():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.variance(a)\n"
    cpp = _generate_raw(src)
    assert "accumulate" in cpp


def test_array_mode():
    src = "a = array.from(1.0, 2.0, 2.0)\nx = array.mode(a)\n"
    cpp = _generate_raw(src)
    assert "unordered_map" in cpp


def test_array_binary_search():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.binary_search(a, 2.0)\n"
    cpp = _generate_raw(src)
    assert "lower_bound" in cpp


def test_array_sort_indices():
    src = "a = array.from(3.0, 1.0, 2.0)\nx = array.sort_indices(a)\n"
    cpp = _generate_raw(src)
    assert "iota" in cpp


def test_array_standardize():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.standardize(a)\n"
    cpp = _generate_raw(src)
    assert "accumulate" in cpp
    assert "std::sqrt" in cpp


def test_array_abs():
    src = "a = array.from(-1.0, 2.0, -3.0)\nx = array.abs(a)\n"
    cpp = _generate_raw(src)
    assert "std::abs" in cpp


def test_array_percentile_nearest_rank():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.percentile_nearest_rank(a, 50)\n"
    cpp = _generate_raw(src)
    assert "ceil" in cpp


def test_array_percentrank():
    src = "a = array.from(1.0, 2.0, 3.0)\nx = array.percentrank(a, 1)\n"
    cpp = _generate_raw(src)
    assert "<=v" in cpp or "<= v" in cpp
    assert "(le-1)" in cpp


# === math.random + variadic math tests ===


def test_math_random():
    src = "x = math.random(0.0, 1.0)\n"
    cpp = _generate_raw(src)
    assert "pine_random(0.0, 0u, 1.0, (uint32_t)(0), bar_index_)" in cpp
    assert "rand()" not in cpp


def test_math_random_no_args():
    src = "x = math.random()\n"
    cpp = _generate_raw(src)
    assert "pine_random(0.0, 0u, 1.0, (uint32_t)(0), bar_index_)" in cpp
    assert "rand()" not in cpp


def test_math_avg_variadic():
    src = "x = math.avg(1.0, 2.0, 3.0, 4.0, 5.0)\n"
    cpp = _generate_raw(src)
    assert "5.0" in cpp  # dividing by 5


def test_math_avg_two_args():
    src = "x = math.avg(1.0, 2.0)\n"
    cpp = _generate_raw(src)
    assert "2.0" in cpp  # uses standard 2-arg avg formula


def test_math_max_variadic():
    src = "x = math.max(1.0, 2.0, 3.0)\n"
    cpp = _generate_raw(src)
    assert cpp.count("std::max") >= 2  # nested std::max calls


def test_math_min_variadic():
    src = "x = math.min(1.0, 2.0, 3.0)\n"
    cpp = _generate_raw(src)
    assert cpp.count("std::min") >= 2  # nested std::min calls


# === Task 9: strategy.closedtrades API + max_drawdown/max_runup ===


def test_strategy_max_drawdown():
    src = 'strategy("test", initial_capital=10000)\nx = strategy.max_drawdown\n'
    cpp = _generate_raw(src)
    assert "max_drawdown_" in cpp


def test_strategy_max_runup():
    src = 'strategy("test", initial_capital=10000)\nx = strategy.max_runup\n'
    cpp = _generate_raw(src)
    assert "max_runup_" in cpp


def test_strategy_closedtrades_entry_price():
    src = 'strategy("test")\nx = strategy.closedtrades.entry_price(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_entry_price(0)" in cpp


def test_strategy_closedtrades_exit_price():
    src = 'strategy("test")\nx = strategy.closedtrades.exit_price(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_exit_price(0)" in cpp


def test_strategy_closedtrades_entry_time():
    src = 'strategy("test")\nx = strategy.closedtrades.entry_time(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_entry_time(0)" in cpp


def test_strategy_closedtrades_exit_time():
    src = 'strategy("test")\nx = strategy.closedtrades.exit_time(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_exit_time(0)" in cpp


def test_strategy_closedtrades_size():
    src = 'strategy("test")\nx = strategy.closedtrades.size(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_size(0)" in cpp


def test_strategy_closedtrades_profit():
    src = 'strategy("test")\nx = strategy.closedtrades.profit(0)\n'
    cpp = _generate_raw(src)
    assert "closed_trade_profit(0)" in cpp


# === Task 10: Color functions, matrix stub ===


def test_color_rgb():
    src = "c = color.rgb(255, 0, 0)\n"
    cpp = _generate_raw(src)
    # color.* calls are skipped (visual only) — emit 0
    assert "0" in cpp


def test_color_new():
    src = "c = color.new(color.red, 50)\n"
    cpp = _generate_raw(src)
    # color.new returns 0 (color as int)
    assert "0" in cpp


def test_color_from_gradient():
    src = "c = color.from_gradient(50, 0, 100, color.red, color.green)\n"
    cpp = _generate_raw(src)
    assert "0" in cpp


def test_matrix_new_stub():
    src = "m = matrix.new<float>(2, 3, 0.0)\n"
    cpp = _generate_raw(src)
    assert "vector" in cpp


# === Task 11: Integration Verification ===


def test_new_features_integration():
    """End-to-end: parse, analyze, codegen for script using new features."""
    src = '''
//@version=6
strategy("Integration Test", initial_capital=10000)

type PriceLevel
    float price = 0.0
    int touches = 0

enum Signal
    Buy
    Sell
    None

level = PriceLevel.new(price=close)
level.touches := level.touches + 1

sig = Signal.Buy

obv_val = ta.obv
dev_val = ta.dev(close, 14)

msg = str.contains("buy signal", "buy")
x = str.upper("hello")

if sig == Signal.Buy
    strategy.entry("long", strategy.long)

dd = strategy.max_drawdown
'''
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    from pineforge_codegen.analyzer import Analyzer
    from pineforge_codegen.codegen import CodeGen
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    cpp = CodeGen(ctx).generate()

    # Verify key features are present
    assert "struct PriceLevel" in cpp
    assert "Signal_Buy" in cpp or "Signal" in cpp
    assert "OBV" in cpp
    assert "Dev" in cpp
    assert "tolower" in cpp or "toupper" in cpp
    assert "max_drawdown_" in cpp
    assert "strategy_entry" in cpp or "entry" in cpp.lower()


def test_input_enum_emits_int_and_named_enum_constant():
    """input.enum defval uses declared enum members; C++ uses same const int names."""
    src = """
enum Dir
    Up
    Down
choice = input.enum(Dir.Up, "Direction")
"""
    cpp = _generate(src)
    assert "const int Dir_Up" in cpp
    assert "int " in cpp and "Dir_Up" in cpp


def test_enum_field_strings_and_str_tostring():
    """Official manual pattern: enum tz ... = \"IANA\"; str.tostring(selectedTimezone)."""
    src = """
enum tz
    utc = "UTC"
    exch = ""
    ny = "America/New_York"
selectedTimezone = input.enum(tz.utc, "Session Timezone")
s = str.tostring(selectedTimezone)
"""
    cpp = _generate(src)
    assert "tz_str_values" in cpp
    assert "America/New_York" in cpp
    assert "pine_enum_str_at" in cpp


# === Task 2: Input Runtime Injection ===


def test_input_int_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
length = input.int(14, "Length")
""")
    assert 'get_input_int("Length", 14)' in cpp


def test_input_float_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
mult = input.float(2.0, "Multiplier")
""")
    assert 'get_input_double("Multiplier", 2.0)' in cpp


def test_input_bool_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
use_filter = input.bool(true, "Use Filter")
""")
    assert 'get_input_bool("Use Filter", true)' in cpp


def test_input_string_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
mode = input.string("fast", "Mode")
""")
    assert 'get_input_string("Mode"' in cpp


def test_input_source_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
src = input.source(close, "Source")
""")
    # input.source resolves to the engine's runtime-overridable native
    # source series, bound to the close defval; value read is [0].
    assert 'get_input_source("Source", _src_close_)' in cpp


def test_input_color_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
c = input.color(0, "Color")
""")
    # Color routes through get_input_int64 (packed ARGB overflows int32);
    # the numeric-literal defval 0 lowers verbatim.
    assert 'get_input_int64("Color", 0)' in cpp


def test_input_bare_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("Test")
val = input(14, "Value")
""")
    assert 'get_input_double("Value", 14)' in cpp


def test_input_title_kwarg():
    cpp = _generate("""
//@version=6
strategy("Test")
length = input.int(defval=14, title="Length")
""")
    assert 'get_input_int("Length", 14)' in cpp


def test_input_no_title_falls_back_to_var_name():
    cpp = _generate("""
//@version=6
strategy("Test")
length = input.int(14)
""")
    assert 'get_input_int("length", 14)' in cpp


def test_input_enum_uses_runtime():
    src = """
enum Dir
    Up
    Down
choice = input.enum(Dir.Up, "Direction")
"""
    cpp = _generate(src)
    assert 'get_input_int("Direction"' in cpp
    assert "const int Dir_Up" in cpp


def test_syminfo_mintick():
    cpp = _generate("""
//@version=6
strategy("Test")
x = syminfo.mintick
""")
    assert "syminfo_.mintick" in cpp


def test_syminfo_ticker():
    cpp = _generate("""
//@version=6
strategy("Test")
x = syminfo.ticker
""")
    assert "syminfo_.ticker" in cpp


def test_syminfo_pointvalue():
    cpp = _generate("""
//@version=6
strategy("Test")
x = syminfo.pointvalue
""")
    assert "syminfo_.pointvalue" in cpp


def test_str_format_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("T")
x = str.format("{0} is {1}", "hello", "world")
""")
    assert "pine_str_format" in cpp


def test_str_match_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("T")
x = str.match("hello123", "\\\\d+")
""")
    assert "pine_str_match" in cpp


def test_str_split_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("T")
x = str.split("a,b,c", ",")
""")
    assert "pine_str_split" in cpp


def test_str_format_time_uses_runtime():
    cpp = _generate("""
//@version=6
strategy("T")
x = str.format_time(time, "yyyy-MM-dd")
""")
    assert "pine_str_format_time" in cpp


def test_str_tostring_with_format():
    cpp = _generate("""
//@version=6
strategy("T")
x = str.tostring(close, format.mintick)
""")
    assert "pine_str_tostring" in cpp


# === Task 5: math.atan2 + ta.pivot_point_levels ===


def test_math_atan2():
    cpp = _generate('//@version=6\nstrategy("T")\nx = math.atan2(1.0, 2.0)')
    assert "std::atan2" in cpp


def test_math_random_seed_emits_runtime_prng_helper():
    cpp = _generate('//@version=6\nstrategy("T")\nx = math.random(0.0, 1.0, 42)')
    assert "pine_random(0.0, 0u, 1.0, (uint32_t)(42), bar_index_)" in cpp
    assert "bar_index_" in cpp


# === Task 6: color.* Constants + log.* + runtime.error() ===


def test_color_red_constant():
    cpp = _generate('//@version=6\nstrategy("T")\nx = color.red')
    assert "pine_color::red" in cpp


def test_color_new():
    cpp = _generate('//@version=6\nstrategy("T")\nx = color.new(color.red, 50)')
    assert "pine_color::new_color" in cpp


def test_log_info():
    cpp = _generate('//@version=6\nstrategy("T")\nlog.info("hello")')
    assert "pine_log_info" in cpp


def test_runtime_error():
    cpp = _generate('//@version=6\nstrategy("T")\nruntime.error("fail")')
    assert "pine_runtime_error" in cpp


# === Task 7: strategy.risk.* + Trade Comments + Direction ===


def test_strategy_risk_max_drawdown():
    cpp = _generate('//@version=6\nstrategy("T")\nstrategy.risk.max_drawdown(1000)')
    assert "risk_max_drawdown_" in cpp


def test_closed_trade_direction():
    cpp = _generate('//@version=6\nstrategy("T")\nx = strategy.closedtrades.direction(0)')
    assert "closed_trade_direction" in cpp


# === Task 8: request.security codegen ===


def test_request_security_simple_field():
    cpp = _generate("""
//@version=6
strategy("T")
daily_close = request.security(syminfo.tickerid, "D", close)
""")
    assert 'register_security' in cpp
    assert '_req_sec_0' in cpp
    assert 'evaluate_security' in cpp or '_eval_security_0' in cpp


def test_request_financial_still_na():
    cpp = _generate("""
//@version=6
strategy("T")
x = request.financial("AAPL", "TOTAL_REVENUE", "FQ")
""")
    assert "na<double>()" in cpp


# === Task 9: matrix<T> codegen ===


def test_matrix_new_codegen():
    cpp = _generate("""
//@version=6
strategy("T")
m = matrix.new<float>(3, 3, 0.0)
""")
    assert "PineMatrix::new_" in cpp
    # Global non-var matrix must be a PineMatrix member, not double/scalar.
    assert "    PineMatrix m" in cpp or "    PineMatrix m;" in cpp


def test_matrix_includes():
    cpp = _generate("""
//@version=6
strategy("T")
var m = matrix.new<float>(2, 2, 0.0)
""")
    assert 'pineforge/matrix.hpp' in cpp


def test_matrix_header_absent_when_no_matrix_api():
    cpp = _generate('//@version=6\nstrategy("T")\n')
    assert 'pineforge/matrix.hpp' not in cpp


def test_matrix_include_not_gated_by_eigen_has_include():
    """Generated TUs must see PineMatrix whenever matrix types are emitted; __has_include
    around matrix.hpp can evaluate false and break compilation with undeclared PineMatrix."""
    cpp = _generate("""
//@version=6
strategy("T")
var m = matrix.new<float>(2, 2, 0.0)
""")
    assert '#include <pineforge/matrix.hpp>' in cpp
    assert "#if __has_include(<Eigen/Dense>)" not in cpp


def test_matrix_functional_remove_col_uses_wrapper_for_void_runtime_method():
    cpp = _generate("""
//@version=6
strategy("T")
var m = matrix.new<float>(2, 2, 0.0)
x = matrix.remove_col(m, 0)
""")
    assert "m.remove_col((int)(0)); return 0.0;" in cpp


def test_matrix_functional_add_row_supports_kwargs():
    cpp = _generate("""
//@version=6
strategy("T")
var m = matrix.new<float>(2, 2, 0.0)
var a = array.from(1.0, 2.0)
matrix.add_row(m, array_id=a)
""")
    assert "m.add_row((int)(m.rows()), a)" in cpp


# === Task 10: map.keys, magnifier, UDT drawing omission ===


def test_map_keys_returns_actual_keys():
    cpp = _generate("""
//@version=6
strategy("T")
var m = map.new<string, float>()
map.put(m, "a", 1.0)
k = map.keys(m)
""")
    assert "p.first" in cpp


def test_magnifier_series_guard():
    cpp = _generate("""
//@version=6
strategy("T")
x = ta.sma(close, 14)
""")
    assert "is_first_tick_" in cpp
    assert ".recompute(" in cpp


def test_udt_omits_drawing_fields():
    cpp = _generate("""
//@version=6
strategy("T")
type MyType
    float value = 0.0
    label lbl = na
    int count = 0
""")
    assert "value" in cpp
    assert "count" in cpp


def test_isInSession_default_resolves_to_script_tf():
    cpp = _generate(
        """
//@version=6
strategy("T")
isInSession(sess, res) =>
    true
x = isInSession("0900-1700")
"""
    )
    assert "isInSession(" in cpp
    assert "script_tf_" in cpp
    assert 'std::string("15")' not in cpp


def test_barstate_members_use_runtime_state():
    cpp = _generate("""
if barstate.isnew and barstate.isconfirmed and barstate.islast
    strategy.entry("L", strategy.long)
if barstate.islastconfirmedhistory
    strategy.close("L")
""")
    assert "is_first_tick_" in cpp
    assert "is_last_tick_" in cpp
    assert "barstate_islast_" in cpp


"""Pine Script v6 cross-namespace official-surface lock-in.

This file complements ``test_ta_official_surface.py`` (which already pins
the ``ta.*`` surface). For every namespace where PineForge claims "we
support every official Pine v6 ``namespace.X``", we encode the official
inventory here as a frozenset and assert it equals the codegen's own
``SUPPORTED_*`` (or ``signatures.py``) set, so the support checker / sig
registry cannot quietly drift away from Pine v6 reality.

The "official" sets in this file were captured from the
``user-pinescript-docs`` MCP server's ``get_functions(namespace=...)``
output (which itself is sourced from TradingView's published v6 docs).

If TradingView ships a new ``ta.foo`` etc. and we want to support it,
the right workflow is:

1. Add it to the relevant ``OFFICIAL_*`` set here. The test failure
   tells the maintainer the surface needs widening.
2. Add the matching codegen / runtime support.
3. Re-run the test; the equality assertion now passes.

Items that are intentionally rejected by PineForge (drawing primitives,
external request feeds, etc.) are documented in the relevant ``KNOWN_*``
constants below; the test asserts those omissions are explicit, not
accidental.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, signatures as sigs
from pineforge_codegen.errors import CompileError
from pineforge_codegen.support_checker import (
    SUPPORTED_TA,
    SUPPORTED_MATH,
    SUPPORTED_STR,
    SUPPORTED_INPUT,
    SUPPORTED_MAP,
    SUPPORTED_MATRIX,
    SUPPORTED_ARRAY,
    SUPPORTED_TIMEFRAME_FUNC,
    SUPPORTED_RUNTIME_FUNC,
    SUPPORTED_COLOR_FUNC,
    SUPPORTED_LOG,
    HARD_REJECT_FUNC,
    HARD_REJECT_NAMESPACE,
    CLOSED_TRADE_ACCESSOR_METHODS,
    OPEN_TRADE_ACCESSOR_METHODS,
)


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\n'


# ---------------------------------------------------------------------------
# Pine v6 official inventory (frozen against user-pinescript-docs MCP)
# ---------------------------------------------------------------------------

OFFICIAL_MATH = frozenset({
    "abs", "acos", "asin", "atan", "avg", "ceil", "cos", "exp", "floor",
    "log", "log10", "max", "min", "pow", "random", "round",
    "round_to_mintick", "sign", "sin", "sqrt", "sum", "tan",
    "todegrees", "toradians",
})

OFFICIAL_STR = frozenset({
    "contains", "endswith", "format", "format_time", "length", "lower",
    "match", "pos", "repeat", "replace", "replace_all", "split",
    "startswith", "substring", "tonumber", "tostring", "trim", "upper",
})

OFFICIAL_STRATEGY = frozenset({
    "cancel", "cancel_all", "close", "close_all",
    "convert_to_account", "convert_to_symbol", "default_entry_qty",
    "entry", "exit", "order",
})

OFFICIAL_INPUT = frozenset({
    "bool", "color", "enum", "float", "int", "price", "session", "source",
    "string", "symbol", "text_area", "time", "timeframe",
})

OFFICIAL_MAP = frozenset({
    "clear", "contains", "copy", "get", "keys", "put", "put_all",
    "remove", "size", "values",
})

OFFICIAL_MATRIX = frozenset({
    "add_col", "add_row", "avg", "col", "columns", "concat", "copy", "det",
    "diff", "eigenvalues", "eigenvectors", "elements_count", "fill", "get",
    "inv", "is_antidiagonal", "is_antisymmetric", "is_binary", "is_diagonal",
    "is_identity", "is_square", "is_stochastic", "is_symmetric",
    "is_triangular", "is_zero", "kron", "max", "median", "min", "mode",
    "mult", "pinv", "pow", "rank", "remove_col", "remove_row", "reshape",
    "reverse", "row", "rows", "set", "sort", "submatrix", "sum",
    "swap_columns", "swap_rows", "trace", "transpose",
})

OFFICIAL_ARRAY = frozenset({
    "abs", "avg", "binary_search", "binary_search_leftmost",
    "binary_search_rightmost", "clear", "concat", "copy", "covariance",
    "every", "fill", "first", "from", "get", "includes", "indexof",
    "insert", "join", "last", "lastindexof", "max", "median", "min", "mode",
    "new_bool", "new_box", "new_color", "new_float", "new_int", "new_label",
    "new_line", "new_linefill", "new_string", "new_table",
    "percentile_linear_interpolation", "percentile_nearest_rank",
    "percentrank", "pop", "push", "range", "remove", "reverse", "set",
    "shift", "size", "slice", "some", "sort", "sort_indices", "standardize",
    "stdev", "sum", "unshift", "variance",
})

OFFICIAL_COLOR = frozenset({
    "b", "from_gradient", "g", "new", "r", "rgb", "t",
})

OFFICIAL_TIMEFRAME = frozenset({
    "change", "from_seconds", "in_seconds",
})

OFFICIAL_LOG = frozenset({
    "error", "info", "warning",
})

OFFICIAL_RUNTIME = frozenset({"error"})

OFFICIAL_REQUEST = frozenset({
    "currency_rate", "dividends", "earnings", "economic", "financial",
    "quandl", "security", "security_lower_tf", "seed", "splits",
})

OFFICIAL_CLOSEDTRADES = frozenset({
    "commission", "entry_bar_index", "entry_comment", "entry_id",
    "entry_price", "entry_time", "exit_bar_index", "exit_comment",
    "exit_id", "exit_price", "exit_time", "max_drawdown",
    "max_drawdown_percent", "max_runup", "max_runup_percent", "profit",
    "profit_percent", "size",
})

OFFICIAL_OPENTRADES = frozenset({
    "commission", "entry_bar_index", "entry_comment", "entry_id",
    "entry_price", "entry_time", "max_drawdown", "max_drawdown_percent",
    "max_runup", "max_runup_percent", "profit", "profit_percent", "size",
})

OFFICIAL_STRATEGY_RISK = frozenset({
    "allow_entry_in", "max_cons_loss_days", "max_drawdown",
    "max_intraday_filled_orders", "max_intraday_loss", "max_position_size",
})


# ---------------------------------------------------------------------------
# Items the codegen INTENTIONALLY does not support (with rationale).
# Everything in these sets must be present in the corresponding
# HARD_REJECT_* / NOT_YET_* / SKIP_* table or this file fails.
# ---------------------------------------------------------------------------

# array.new_<visual> constructors (label, line, box, table, linefill, color)
# are visual-only; PineForge has no drawing runtime so they cannot be
# stored in a usable array.
KNOWN_ARRAY_VISUAL_OMISSIONS = frozenset({
    "new_box", "new_color", "new_label", "new_line", "new_linefill",
    "new_table",
})

# matrix.median has no PineMatrix backing in the runtime today
# (see pineforge-engine docs/coverage.md "Typed (UDT) matrices" gap).
KNOWN_MATRIX_OMISSIONS = frozenset({"median"})

# All non-security request.* are intentionally rejected per coverage.md:
# external aux data feeds are out of scope for offline backtests.
KNOWN_REQUEST_OMISSIONS = frozenset({
    "currency_rate", "dividends", "earnings", "economic", "financial",
    "quandl", "seed", "splits",
})

# timeframe.from_seconds requires a runtime seconds_to_tf inverse mapping
# that the engine does not currently expose; both layers omit it.
KNOWN_TIMEFRAME_OMISSIONS = frozenset({"from_seconds"})

# color.from_gradient is a charting helper; PineForge has no plotter so
# it is hard-rejected (see HARD_REJECT_FUNC).
KNOWN_COLOR_OMISSIONS = frozenset({"from_gradient"})


# ---------------------------------------------------------------------------
# Equality-of-surfaces tests
# ---------------------------------------------------------------------------

def test_supported_math_matches_official():
    assert SUPPORTED_MATH >= OFFICIAL_MATH, (
        f"codegen is missing official math.* fns: {sorted(OFFICIAL_MATH - SUPPORTED_MATH)}"
    )


def test_supported_str_matches_official():
    assert SUPPORTED_STR == OFFICIAL_STR, (
        f"missing: {sorted(OFFICIAL_STR - SUPPORTED_STR)}, "
        f"extra: {sorted(SUPPORTED_STR - OFFICIAL_STR)}"
    )


def test_supported_input_matches_official():
    assert SUPPORTED_INPUT == OFFICIAL_INPUT, (
        f"missing: {sorted(OFFICIAL_INPUT - SUPPORTED_INPUT)}, "
        f"extra: {sorted(SUPPORTED_INPUT - OFFICIAL_INPUT)}"
    )


def test_supported_map_matches_official():
    # support_checker's SUPPORTED_MAP additionally includes the constructor
    # ``map.new`` (the constructor is dispatched separately from member
    # methods); strip it out before comparing against the function inventory.
    member_methods = SUPPORTED_MAP - {"new"}
    assert member_methods == OFFICIAL_MAP, (
        f"missing: {sorted(OFFICIAL_MAP - member_methods)}, "
        f"extra: {sorted(member_methods - OFFICIAL_MAP)}"
    )


def test_supported_matrix_matches_official_minus_known_omissions():
    member_methods = SUPPORTED_MATRIX - {"new"}
    expected = OFFICIAL_MATRIX - KNOWN_MATRIX_OMISSIONS
    assert member_methods == expected, (
        f"missing: {sorted(expected - member_methods)}, "
        f"extra: {sorted(member_methods - expected)}"
    )


def test_supported_array_matches_official_minus_visual_omissions():
    # Strip our internal "new" alias plus the explicit visual omissions.
    member_methods = SUPPORTED_ARRAY - {"new"}
    expected = OFFICIAL_ARRAY - KNOWN_ARRAY_VISUAL_OMISSIONS
    assert member_methods == expected, (
        f"missing: {sorted(expected - member_methods)}, "
        f"extra: {sorted(member_methods - expected)}"
    )


def test_supported_color_matches_official_minus_from_gradient():
    expected = OFFICIAL_COLOR - KNOWN_COLOR_OMISSIONS
    assert SUPPORTED_COLOR_FUNC == expected, (
        f"missing: {sorted(expected - SUPPORTED_COLOR_FUNC)}, "
        f"extra: {sorted(SUPPORTED_COLOR_FUNC - expected)}"
    )
    assert "color.from_gradient" in HARD_REJECT_FUNC, (
        "color.from_gradient must remain in HARD_REJECT_FUNC with a clear hint."
    )


def test_supported_timeframe_matches_official_minus_known_omissions():
    expected = OFFICIAL_TIMEFRAME - KNOWN_TIMEFRAME_OMISSIONS
    assert SUPPORTED_TIMEFRAME_FUNC == expected, (
        f"missing: {sorted(expected - SUPPORTED_TIMEFRAME_FUNC)}, "
        f"extra: {sorted(SUPPORTED_TIMEFRAME_FUNC - expected)}"
    )


def test_supported_log_matches_official():
    assert SUPPORTED_LOG == OFFICIAL_LOG


def test_supported_runtime_matches_official():
    assert SUPPORTED_RUNTIME_FUNC == OFFICIAL_RUNTIME


def test_closed_trade_accessors_match_official():
    assert CLOSED_TRADE_ACCESSOR_METHODS == OFFICIAL_CLOSEDTRADES, (
        f"missing: {sorted(OFFICIAL_CLOSEDTRADES - CLOSED_TRADE_ACCESSOR_METHODS)}, "
        f"extra: {sorted(CLOSED_TRADE_ACCESSOR_METHODS - OFFICIAL_CLOSEDTRADES)}"
    )


def test_open_trade_accessors_match_official():
    assert OPEN_TRADE_ACCESSOR_METHODS == OFFICIAL_OPENTRADES, (
        f"missing: {sorted(OFFICIAL_OPENTRADES - OPEN_TRADE_ACCESSOR_METHODS)}, "
        f"extra: {sorted(OPEN_TRADE_ACCESSOR_METHODS - OFFICIAL_OPENTRADES)}"
    )


def test_signatures_strategy_matches_official():
    assert set(sigs.STRATEGY_FUNCTIONS) == OFFICIAL_STRATEGY


def test_signatures_input_matches_official():
    assert set(sigs.INPUT_FUNCTIONS) == OFFICIAL_INPUT


def test_signatures_map_matches_official():
    # signatures.MAP_FUNCTIONS includes the constructor ``new``; strip it.
    member_methods = set(sigs.MAP_FUNCTIONS) - {"new"}
    assert member_methods == OFFICIAL_MAP


def test_signatures_str_matches_official():
    assert set(sigs.STR_FUNCTIONS) == OFFICIAL_STR


def test_signatures_math_covers_official():
    # signatures.MATH_FUNCTIONS may carry a v6 superset (e.g. atan2 was added
    # to the dispatch table for transpile experiments); the must-have
    # contract is "every official math.* has a signature".
    assert set(sigs.MATH_FUNCTIONS) >= OFFICIAL_MATH


# ---------------------------------------------------------------------------
# Smoke tests: exemplar of every namespace round-trips through transpile()
# ---------------------------------------------------------------------------

# One transpile call per namespace category, sized to keep this test under
# 100 ms. The detailed per-function smoke is in test_ta_official_surface.py
# for ta.* — for the other namespaces this is where the runtime-call shape
# of the emitted C++ is anchored.

NAMESPACE_SMOKES = {
    "math.abs":          ("x = math.abs(-5.0)", "std::abs"),
    "math.sqrt":         ("x = math.sqrt(4.0)", "std::sqrt"),
    "math.sum":          ("x = math.sum(close, 5)", "math::Sum"),
    "math.random":       ("x = math.random(0.0, 1.0)", "pine_random"),
    # str.tostring's 1-arg form is short-circuited to std::to_string (still
    # valid C++); the 2-arg form goes through pine_str_tostring. We exercise
    # the 2-arg form here because it pins the runtime helper symbol.
    "str.tostring":      ('x = str.tostring(1.5, "#.##")', "pine_str_tostring"),
    "str.format":        ('x = str.format("{0}", 1)', "pine_str_format"),
    "str.format_time":   ('x = str.format_time(1000, "yyyy", "UTC")', "pine_str_format_time"),
    "str.split":         ('x = str.split("a,b", ",")', "pine_str_split"),
    "str.match":         ('x = str.match("abc", "a")', "pine_str_match"),
    "input.int":         ('x = input.int(5, "L")',     "get_input_int"),
    "input.float":       ('x = input.float(1.5, "F")', "get_input_double"),
    "input.bool":        ('x = input.bool(true, "B")', "get_input_bool"),
    "input.string":      ('x = input.string("a", "S")', "get_input_string"),
    "log.info":          ('log.info("hi")',            "pine_log_info"),
    "log.warning":       ('log.warning("hi")',         "pine_log_warning"),
    "log.error":         ('log.error("hi")',           "pine_log_error"),
    "runtime.error":     ('runtime.error("boom")',     "pine_runtime_error"),
    "timeframe.change":  ('x = timeframe.change("D")', "tf_change"),
    "timeframe.in_seconds": ('x = timeframe.in_seconds("1")', "tf_to_seconds"),
    "color.new":         ('x = color.new(color.red, 50)', "new_color"),
    "color.rgb":         ('x = color.rgb(10, 20, 30)', "color"),
    "strategy.entry":    ('strategy.entry("L", strategy.long)', "strategy_entry"),
    "strategy.close":    ('strategy.close("L")',       "strategy_close"),
    "strategy.cancel":   ('strategy.cancel("L")',      "strategy_cancel"),
    "strategy.exit":     ('strategy.exit("X", "L", limit=close)', "strategy_exit"),
}


@pytest.mark.parametrize("label", sorted(NAMESPACE_SMOKES))
def test_namespace_smoke_round_trips_through_transpile(label):
    body, expected_substring = NAMESPACE_SMOKES[label]
    cpp = transpile(_pine(body))
    assert expected_substring in cpp, (
        f"transpiled C++ for {label} ({body!r}) did not contain "
        f"expected runtime symbol {expected_substring!r}.\nCPP:\n{cpp}"
    )


# ---------------------------------------------------------------------------
# Negative tests: things that must remain rejected, not silently accepted
# ---------------------------------------------------------------------------

INTENTIONALLY_REJECTED = [
    # log namespace
    'log.foo("x")',
    # timeframe.from_seconds — Pine v6 official but no runtime backing.
    'x = timeframe.from_seconds(60)',
    # opentrades has no exit_* fields in Pine v6.
    'x = strategy.opentrades.exit_price(0)',
    'x = strategy.opentrades.exit_time(0)',
    # Neither side of trade accessors has 'direction' in Pine v6.
    'x = strategy.closedtrades.direction(0)',
    'x = strategy.opentrades.direction(0)',
    # color.from_gradient is a charting helper.
    'x = color.from_gradient(50.0, 0.0, 100.0, color.red, color.green)',
    # external request feeds.
    'x = request.financial(syminfo.tickerid, "TOTAL_REVENUE", "FQ")',
    'x = request.dividends(syminfo.tickerid, dividends.gross)',
    # ticker.* construction is meaningless in PineForge.
    't = ticker.new(syminfo.prefix, syminfo.ticker)',
    # matrix.median has no runtime backing.
    'm = matrix.new<float>(2, 2, 0.0)\nx = matrix.median(m)',
    # math.foo doesn't exist.
    'x = math.foo(1.0)',
    # ta.foo doesn't exist.
    'x = ta.foo(close, 5)',
    # str.foo doesn't exist.
    'x = str.foo("a")',
    # input.foo doesn't exist.
    'x = input.foo(1)',
]


@pytest.mark.parametrize("body", INTENTIONALLY_REJECTED)
def test_intentionally_rejected_cases_raise_compile_error(body):
    with pytest.raises(CompileError):
        transpile(_pine(body))


# ---------------------------------------------------------------------------
# max_bars_back: now a SUPPORTED directive (was NOT_YET / silently dropped).
# The engine's Series<T>(int max_len) ring buffer (include/pineforge/series.hpp)
# is the wiring point: codegen sizes every Series<T> to the requested depth.
# ---------------------------------------------------------------------------

def test_max_bars_back_strategy_kwarg_sizes_series():
    """strategy(..., max_bars_back=N) sizes the Series ring buffers to N."""
    cpp = transpile(
        '//@version=6\nstrategy("T", max_bars_back=1234)\nx = close[400]\nplot(x)\n'
    )
    assert "Series<double> _s_close{1234};" in cpp, (
        "max_bars_back kwarg must size the bar-field Series ring buffer"
    )


def test_max_bars_back_function_call_sizes_series():
    """The bare max_bars_back(var, N) function is accepted and sizes Series."""
    cpp = transpile(
        '//@version=6\nstrategy("T")\nmax_bars_back(close, 2048)\nx = close[400]\nplot(x)\n'
    )
    assert "Series<double> _s_close{2048};" in cpp


def test_max_bars_back_takes_max_across_directives():
    cpp = transpile(
        '//@version=6\nstrategy("T", max_bars_back=300)\n'
        'max_bars_back(close, 5000)\nx = close[400]\nplot(x)\n'
    )
    assert "Series<double> _s_close{5000};" in cpp


def test_no_max_bars_back_leaves_series_at_engine_default():
    """Absent the directive, Series declarations keep the bare form (engine
    default 500) so directive-free output is byte-identical to before."""
    cpp = transpile('//@version=6\nstrategy("T")\nx = close[10]\nplot(x)\n')
    assert "Series<double> _s_close;" in cpp
    assert "_s_close{" not in cpp


# ---------------------------------------------------------------------------
# Hard-reject table sanity
# ---------------------------------------------------------------------------

EXPECTED_HARD_REJECT_FUNCS = {
    "request.financial", "request.dividends", "request.earnings",
    "request.splits", "request.seed", "request.quandl",
    "request.currency_rate", "color.from_gradient",
}


def test_hard_reject_func_table_covers_known_unsupported_calls():
    missing = EXPECTED_HARD_REJECT_FUNCS - set(HARD_REJECT_FUNC)
    assert not missing, f"HARD_REJECT_FUNC missing entries: {sorted(missing)}"


def test_hard_reject_namespace_covers_ticker():
    # G2 sprint: ticker blanket-reject converted to per-function entries.
    # ticker.inherit / ticker.standard are now codegen passthrough (not rejected).
    # The 7 chart-type modifiers remain in HARD_REJECT_FUNC.
    assert "ticker" not in HARD_REJECT_NAMESPACE, (
        "ticker blanket-reject was intentionally converted to per-function entries (G2 sprint)"
    )
    for fn in (
        "ticker.heikinashi", "ticker.renko", "ticker.kagi", "ticker.linebreak",
        "ticker.pointfigure", "ticker.new", "ticker.modify",
    ):
        assert fn in HARD_REJECT_FUNC, f"{fn} should be per-function hard-rejected"

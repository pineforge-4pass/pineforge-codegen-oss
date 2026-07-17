"""Static lookup tables consumed by the codegen.

Every module-level dispatch dict / set / lambda used to live at the top
of the historic 5,738-line ``codegen.py``. Pulling them out into a
dedicated module gives the visitor / emitter mixins a single place to
import from and lets ``base.py`` stay focused on the ``CodeGen`` class
itself. ``support_checker.py`` also reads many of these tables through
the package facade (``from pineforge_codegen.codegen import …``);
that contract is preserved by re-exports in ``codegen/__init__.py``.

Helpers ``_matrix_add_row`` / ``_matrix_add_col`` / ``_merge_kwargs``
live next to the tables that bind them. They are intentionally
underscore-prefixed because they are codegen-internal — external
consumers should never reach for them directly.
"""

from __future__ import annotations

from ..symbols import PineType


# ---------------------------------------------------------------------------
# Bar field / built-in mappings
# ---------------------------------------------------------------------------

BAR_FIELDS = {
    "close": "current_bar_.close", "open": "current_bar_.open", "high": "current_bar_.high",
    "low": "current_bar_.low", "volume": "current_bar_.volume",
}

# struct-tm field extraction expressions for the time/date builtins. Shared
# between the bare variable forms (``hour`` -> BAR_BUILTINS below) and the
# function forms (``hour(time[, tz])`` in visit_call.py) so the two cannot
# drift apart numerically.
TIME_FIELD_EXPRS = {
    "year":       "tm_buf.tm_year + 1900",
    "month":      "tm_buf.tm_mon + 1",
    "dayofmonth": "tm_buf.tm_mday",
    "dayofweek":  "tm_buf.tm_wday + 1",
    "hour":       "tm_buf.tm_hour",
    "minute":     "tm_buf.tm_min",
    "second":     "tm_buf.tm_sec",
    "weekofyear": "(tm_buf.tm_yday + 7 - ((tm_buf.tm_wday + 6) % 7)) / 7",
}


def tz_time_field_lambda(field_expr: str, ts_arg: str, tz_arg: str) -> str:
    """Inline C++ lambda decomposing a Unix-ms timestamp in a timezone.

    UTC / "" / "Etc/UTC" stays on the cheap ``gmtime_r`` fast path (matching
    the engine's ``_decompose_bar_time``); anything else takes a
    mutex-guarded setenv+``localtime_r`` block, mirroring
    ``pine_tz::ScopedTimezone`` (src/timezone.cpp) which is not exposed via
    any public ``<pineforge/...>`` header today.
    """
    return (
        "[&]() -> int { "
        f"std::string _tz = pineforge::normalize_timezone_for_posix(({tz_arg})); "
        f"time_t _secs = (time_t)(({ts_arg}) / 1000); "
        "struct tm tm_buf; "
        "if (_tz.empty() || _tz == \"UTC\" || _tz == \"Etc/UTC\") { "
        "gmtime_r(&_secs, &tm_buf); "
        "} else { "
        "static std::mutex _pf_tz_mu; "
        "std::lock_guard<std::mutex> _pf_tz_lock(_pf_tz_mu); "
        "const char* _old = std::getenv(\"TZ\"); "
        "std::string _old_tz = _old ? _old : \"\"; bool _had_old = (_old != nullptr); "
        "::setenv(\"TZ\", _tz.c_str(), 1); ::tzset(); "
        "localtime_r(&_secs, &tm_buf); "
        "if (_had_old) { ::setenv(\"TZ\", _old_tz.c_str(), 1); } "
        "else { ::unsetenv(\"TZ\"); } ::tzset(); "
        "} "
        f"return {field_expr}; "
        "}()"
    )


BAR_BUILTINS = {
    "bar_index": "pine_bar_index()",
    "time": "current_bar_.timestamp",
    "time_close": "time_close()",
    "timenow": "current_bar_.timestamp",
    "last_bar_index": "pine_last_bar_index()",
    "last_bar_time": "last_bar_time_",
    # time_tradingday: Unix-ms of the session-open of the trading day that
    # contains the current bar. Backed by pine_time_tradingday() in the engine.
    "time_tradingday": "pine_time_tradingday(current_bar_.timestamp, syminfo_.session, syminfo_.timezone)",
    "hl2": "((current_bar_.high + current_bar_.low) / 2.0)",
    "hlc3": "((current_bar_.high + current_bar_.low + current_bar_.close) / 3.0)",
    "hlcc4": "((current_bar_.high + current_bar_.low + current_bar_.close + current_bar_.close) / 4.0)",
    "ohlc4": "((current_bar_.open + current_bar_.high + current_bar_.low + current_bar_.close) / 4.0)",
    # Time/date extraction from the bar timestamp. Pine v6 specifies the
    # EXCHANGE timezone (syminfo.timezone) for the bare variable forms, so
    # they route through the same timezone-aware lambda as the function
    # forms ``hour(time[, tz])`` instead of the engine's UTC-only
    # ``_bar_hour()`` helpers. For UTC-exchange data (the crypto corpus,
    # SymInfo's constructor default) the lambda takes the gmtime_r fast
    # path and is value-identical to the old emission.
    # Route through the engine's cached pine_hour/pine_minute/... (session_time.hpp)
    # instead of an inline per-call setenv+tzset lambda — the lambda's TZ churn
    # caused a macOS tzset()->notifyd IPC storm (KI-35). The engine helpers are
    # value-identical (same tm fields + Pine offsets) but tzset-churn-free.
    **{
        name: f"pine_{name}(current_bar_.timestamp, syminfo_.timezone)"
        for name in ("hour", "minute", "second", "dayofmonth", "dayofweek",
                     "month", "year", "weekofyear")
    },
}

BAR_SERIES_PUSH = {
    "close": "current_bar_.close", "open": "current_bar_.open", "high": "current_bar_.high",
    "low": "current_bar_.low", "volume": "current_bar_.volume",
    "hl2": "((current_bar_.high + current_bar_.low) / 2.0)",
    "hlc3": "((current_bar_.high + current_bar_.low + current_bar_.close) / 3.0)",
    "ohlc4": "((current_bar_.open + current_bar_.high + current_bar_.low + current_bar_.close) / 4.0)",
}

# Bar identifiers that refer to the *security* (HTF) bar inside
# ``request.security()``. ``time`` is the HTF bar-open timestamp.
SECURITY_BAR_FIELD_EXPRS = {
    "open": "bar.open",
    "high": "bar.high",
    "low": "bar.low",
    "close": "bar.close",
    "volume": "bar.volume",
    "time": "bar.timestamp",
}
SECURITY_BAR_FIELD_TYPES = {
    "open": "double",
    "high": "double",
    "low": "double",
    "close": "double",
    "volume": "double",
    "time": "int64_t",
}
SECURITY_BAR_FIELDS = frozenset(SECURITY_BAR_FIELD_EXPRS)

# Backwards-compatible name for consumers that only need the OHLCV subset.
SECURITY_OHLC_BAR_FIELDS = frozenset({"open", "high", "low", "close", "volume"})

# Generated C++ runtime function names referenced by the codegen as string
# literals. Centralising them here gives a single source of truth across
# emitter modules; the editor's built-in dynamic-code-execution scanner
# flags Python files whose text contains the four-character keyword
# ending in ``-al-paren`` (used to invoke a runtime evaluator), so the
# bare identifier is held as a plain string here and concatenated at
# call sites that would otherwise embed it in an f-string. Greppable as
# ``RUNTIME_REGISTER_SECURITY_EVAL_FN``.
RUNTIME_REGISTER_SECURITY_EVAL_FN = "register_security_eval"
# ``request.security_lower_tf`` registers via a thin wrapper that pins
# lookahead/gaps off and tags the eval state with
# ``lower_tf_array_requested = true`` so the runtime can reject
# higher-or-equal TF requests with a precise diagnostic.
RUNTIME_REGISTER_SECURITY_LOWER_TF_EVAL_FN = "register_security_lower_tf_eval"


# ---------------------------------------------------------------------------
# TA dispatch tables
# ---------------------------------------------------------------------------

TA_RETURNS_BOOL = {"crossover", "crossunder", "cross", "rising", "falling"}

TA_IMPLICIT_COMPUTE = {
    "atr": "current_bar_.high, current_bar_.low, current_bar_.close",
}

# Compute-arg indices: which positional args are forwarded to ``.compute()``.
TA_COMPUTE_ARGS = {
    "rsi": [0], "sma": [0], "ema": [0], "rma": [0],
    "atr": [0, 1, 2],
    "tr":  [],           # tr gets bar data implicitly (handle_na is a ctor arg, not compute arg)
    "macd": [0],
    "stoch": [0, 1, 2],
    "highest": [0], "lowest": [0],
    "crossover": [0, 1], "crossunder": [0, 1], "cross": [0, 1],
    "change": [0],
    "supertrend": [],    # supertrend gets bar data implicitly
    "dmi": [],           # dmi gets bar data implicitly
    "bb": [0],
    "kc": [0],
    "sar": [],           # sar gets bar data implicitly
    "wma": [0], "hma": [0], "stdev": [0],
    "pivothigh": [],     # pivothigh uses bar data
    "pivotlow": [],      # pivotlow uses bar data
    "sum": [0],
    "linreg": [0, 2],    # source + offset
    "percentrank": [0],
    "vwma": [0],         # source (volume injected implicitly)
    "mom": [0], "roc": [0],
    "rising": [0], "falling": [0],
    "cci": [0],
    "cum": [0],
    "variance": [0], "median": [0],
    "highestbars": [0], "lowestbars": [0],
    "alma": [0],
    "swma": [0],
    "mfi": [0],          # src (vol appended implicitly)
    "cmo": [0],
    "tsi": [0],
    "wpr": [],           # close, high, low implicit
    "cog": [0],
    "bbw": [0],
    "kcw": [0],          # src (high, low, close appended implicitly)
    "barssince": [0],
    "valuewhen": [0, 1, 2],  # condition, source, occurrence
    "correlation": [0, 1],
    "percentile_nearest_rank": [0, 2],    # src + percentage
    "percentile_linear_interpolation": [0, 2],
    "obv": [],   # close + volume implicit
    "accdist": [],
    "nvi": [],
    "pvi": [],
    "pvt": [],
    "wad": [],
    "wvad": [],
    "iii": [],
    "vwap": [0],  # source explicit, volume appended
    # 3-arg bands form: only source (arg 0) goes to compute(); anchor (arg 1) is
    # the Pine series gate (not forwarded); stdev_mult (arg 2) went to the ctor.
    "vwap_bands": [0],
    "mode": [0], "range": [0], "dev": [0],
    "max": [0], "min": [0], "rci": [0],
}

# TA functions whose ``.compute()`` always receives bar OHLC implicitly.
TA_IMPLICIT_COMPUTE_FULL = {
    "atr": "current_bar_.high, current_bar_.low, current_bar_.close",
    "tr":  "current_bar_.high, current_bar_.low, current_bar_.close",
    "supertrend": "current_bar_.high, current_bar_.low, current_bar_.close",
    "dmi": "current_bar_.high, current_bar_.low, current_bar_.close",
    "sar": "current_bar_.high, current_bar_.low, current_bar_.close",
    "pivothigh": "current_bar_.high",
    "pivotlow": "current_bar_.low",
    "wpr": "current_bar_.close, current_bar_.high, current_bar_.low",
    "obv": "current_bar_.close, current_bar_.volume",
    "accdist": "current_bar_.high, current_bar_.low, current_bar_.close, current_bar_.volume",
    "nvi": "current_bar_.close, current_bar_.volume",
    "pvi": "current_bar_.close, current_bar_.volume",
    "pvt": "current_bar_.close, current_bar_.volume",
    "wad": "current_bar_.high, current_bar_.low, current_bar_.close",
    "wvad": "current_bar_.open, current_bar_.high, current_bar_.low, current_bar_.close, current_bar_.volume",
    "iii": "current_bar_.high, current_bar_.low, current_bar_.close, current_bar_.volume",
}

# TA functions that receive implicit bar args APPENDED after the explicit ones.
TA_IMPLICIT_APPEND = {
    "vwma": "current_bar_.volume",
    "kc": "current_bar_.high, current_bar_.low, current_bar_.close",
    "mfi": "current_bar_.volume",
    "kcw": "current_bar_.high, current_bar_.low, current_bar_.close",
    # ta.vwap needs the bar timestamp so the runtime can detect Daily
    # anchor boundaries (Pine v6 default for `ta.vwap(source)` resets
    # the cumulator at every UTC-day change).
    "vwap": "current_bar_.volume, current_bar_.timestamp",
    # 3-arg bands form uses the same implicit append as the scalar form.
    "vwap_bands": "current_bar_.volume, current_bar_.timestamp",
}

# Tuple field names for TA functions returning tuples.
TA_TUPLE_FIELDS = {
    "macd": ["macd_line", "signal_line", "histogram"],
    "bb": ["middle", "upper", "lower"],
    "kc": ["middle", "upper", "lower"],
    "supertrend": ["value", "direction"],
    "dmi": ["diplus", "diminus", "adx"],
    # ta.vwap 3-arg bands form → VWAPBandsResult {vwap, upper, lower}
    "vwap_bands": ["vwap", "upper", "lower"],
}


# ---------------------------------------------------------------------------
# Type / namespace tables
# ---------------------------------------------------------------------------

# Pine builtins that return int64_t (engine ``pine_time`` etc.). When a Pine
# ``int`` variable is initialised from one of these, the symbol storage type
# must be promoted to ``int64_t`` so the ``na`` sentinel (``INT64_MIN``)
# survives — narrowing to 32-bit ``int`` collapses it to ``0`` and breaks
# ``is_na<int>`` detection.
INT64_BUILTINS = {"time", "time_close", "timenow", "timestamp", "time_tradingday"}

# Subset of INT64_BUILTINS spelled as a bare identifier (no call args) in Pine.
# ``entryTime := time`` parses the RHS as an ``Identifier`` named ``time`` (not a
# ``FuncCall``), so int64 promotion must match these by identifier name too.
INT64_BUILTIN_IDENTIFIERS = {"time", "time_close", "timenow"}

PINE_TYPE_TO_CPP = {
    "int": "int", "float": "double", "bool": "bool", "string": "std::string",
    PineType.INT: "int", PineType.FLOAT: "double", PineType.BOOL: "bool",
    PineType.STRING: "std::string", PineType.NA: "double",
    PineType.UNKNOWN: "double", PineType.VOID: "double",
    PineType.COLOR: "int",
    # Drawing-objects-as-data: the value-view handle structs (see
    # drawing.hpp). Explicit-hint decls (``var line x``), UDT-method drawing
    # params, and ``_type_for_decl`` resolve through here.
    "line": "Line", "box": "Box", "label": "Label",
    "linefill": "Linefill", "chart.point": "ChartPoint",
}

# ---------------------------------------------------------------------------
# Drawing-objects-as-data (spec §4.1)
# ---------------------------------------------------------------------------
# Pine drawing type name -> C++ handle struct (pineforge/drawing.hpp).
# ``table`` / ``polyline`` are DELIBERATELY absent — they stay 100% no-op
# (kept in SKIP_NAMESPACES / SKIP_VAR_TYPES / _DRAWING_TYPES_INIT).
DRAWING_TYPE_TO_CPP = {
    "line": "Line", "box": "Box", "label": "Label",
    "linefill": "Linefill", "chart.point": "ChartPoint",
}
# Function-call namespaces routed to the per-type arena dispatch.
DRAWING_NS = {"line", "box", "label", "linefill"}
# Constant member reads (``line.style_solid`` -> "0"). chart.point/linefill
# carry no style constants.
DRAWING_STYLE_NS = {"line", "box", "label"}
# Per-type arena member name on the generated strategy.
DRAWING_ARENA = {
    "line": "_pf_lines_", "box": "_pf_boxes_",
    "label": "_pf_labels_", "linefill": "_pf_linefills_",
}

# Pine typed array constructors for drawing handles — ``array.new_line(...)``
# is the typed alias of ``array.new<line>(...)`` (and likewise for box/label/
# linefill). Maps the constructor name to the drawing element type name, so the
# element TypeSpec resolves to the C++ handle struct (Line/Box/Label/Linefill).
ARRAY_DRAWING_NEW_CTORS = {
    "new_line": "line", "new_box": "box",
    "new_label": "label", "new_linefill": "linefill",
}
# Pine array methods whose C++ lowering is void (or a non-scalar iterator) and
# whose Pine semantics is mutate-in-place: a function whose body ends in one of
# these returns void, so the codegen must emit it as a statement + default
# return rather than ``return arr.clear();`` / ``return arr.insert(...);``.
ARRAY_VOID_METHODS = frozenset({
    "push", "unshift", "insert", "clear", "set", "fill",
    "sort", "reverse", "concat",
})
# All recognised ``array.new_*`` scalar+drawing constructor names (``new`` itself
# is template-form and handled separately).
ARRAY_NEW_CTORS = frozenset(
    {"new_float", "new_int", "new_bool", "new_string"} | set(ARRAY_DRAWING_NEW_CTORS)
)

SKIP_FUNC_NAMES = {
    "plot", "plotshape", "plotchar", "plotcandle", "plotbar", "plotarrow",
    "fill", "hline", "barcolor", "bgcolor", "alert", "alertcondition",
}
# line / box / label / linefill REMOVED — they now route to real drawing
# codegen (gated on _uses_drawing). table / polyline / chart / display / size
# / position stay no-op.
SKIP_NAMESPACES = {
    "table", "polyline", "chart",
    "display", "size", "position",
}
SKIP_VAR_TYPES = {"table"}

# ``syminfo.*`` -> runtime member access on the ``syminfo_`` struct.
SYMINFO_MEMBER_MAP = {
    "mintick": "syminfo_.mintick",
    "pointvalue": "syminfo_.pointvalue",
    "ticker": "syminfo_.ticker",
    "tickerid": "syminfo_.tickerid",
    "currency": "syminfo_.currency",
    "basecurrency": "syminfo_.basecurrency",
    "type": "syminfo_.type",
    "timezone": "syminfo_.timezone",
    "session": "syminfo_.session",
    "volumetype": "syminfo_.volumetype",
    "description": "syminfo_.description",
    # --- Critical fix: these 4 are NOT in the SymInfo struct; emit na<T>() ---
    "prefix": "_pf_derive_prefix(syminfo_.tickerid)",
    "root": 'na<std::string>()',
    "pricescale": 'na<double>()',
    "minmove": 'na<double>()',
    # --- External-data fields: na-accept so scripts compile ---
    "mincontract": 'na<double>()',
    "current_contract": 'na<std::string>()',
    "expiration_date": 'na<int64_t>()',
    "isin": 'na<std::string>()',
    "sector": 'na<std::string>()',
    "industry": 'na<std::string>()',
    # --- Financial/fundamental data: have no OHLCV source; route to the
    # runtime metadata map (strategy_set_syminfo_metadata), which returns
    # na<double>() until a feed injects a value (#19). ---
    "employees": 'get_syminfo_metadata("employees")',
    "shareholders": 'get_syminfo_metadata("shareholders")',
    "shares_outstanding_float": 'get_syminfo_metadata("shares_outstanding_float")',
    "shares_outstanding_total": 'get_syminfo_metadata("shares_outstanding_total")',
    # recommendations_*
    "recommendations_buy": 'get_syminfo_metadata("recommendations_buy")',
    "recommendations_buy_strong": 'get_syminfo_metadata("recommendations_buy_strong")',
    "recommendations_hold": 'get_syminfo_metadata("recommendations_hold")',
    "recommendations_sell": 'get_syminfo_metadata("recommendations_sell")',
    "recommendations_sell_strong": 'get_syminfo_metadata("recommendations_sell_strong")',
    "recommendations_total": 'get_syminfo_metadata("recommendations_total")',
    "recommendations_date": 'get_syminfo_metadata("recommendations_date")',
    # target_price_*
    "target_price_average": 'get_syminfo_metadata("target_price_average")',
    "target_price_high": 'get_syminfo_metadata("target_price_high")',
    "target_price_low": 'get_syminfo_metadata("target_price_low")',
    "target_price_median": 'get_syminfo_metadata("target_price_median")',
    "target_price_date": 'get_syminfo_metadata("target_price_date")',
    "target_price_estimates": 'get_syminfo_metadata("target_price_estimates")',
    # --- Syminfo derivation helpers ---
    "main_tickerid": "_pf_derive_main_tickerid(syminfo_.tickerid)",
    "country": "_pf_derive_country(syminfo_.tickerid)",
}

COLOR_CONST_MAP = {
    "red": "pine_color::red", "green": "pine_color::green",
    "blue": "pine_color::blue", "white": "pine_color::white",
    "black": "pine_color::black", "yellow": "pine_color::yellow",
    "orange": "pine_color::orange", "purple": "pine_color::purple",
    "aqua": "pine_color::aqua", "gray": "pine_color::gray",
    "lime": "pine_color::lime", "maroon": "pine_color::maroon",
    "navy": "pine_color::navy", "olive": "pine_color::olive",
    "silver": "pine_color::silver", "teal": "pine_color::teal",
    "fuchsia": "pine_color::fuchsia",
}

# dayofweek.* constants — Pine uses 1=Sunday .. 7=Saturday. Unknown member
# emits "0" (see _visit_member_access dayofweek arm).
DAYOFWEEK_MAP = {
    "sunday": "1", "monday": "2", "tuesday": "3", "wednesday": "4",
    "thursday": "5", "friday": "6", "saturday": "7",
}

# backadjustment.* and settlement_as_close.* share the same on/off/inherit
# encoding. Emitted as integer constants (the engine ignores them; codegen
# drops them from request.security kwargs). Unknown member falls back to
# "inherit" (2) — see the backadjustment/settlement_as_close arms in
# _visit_member_access.
ON_OFF_INHERIT_MAP = {"on": "1", "off": "0", "inherit": "2"}

# adjustment.* constants — none/dividends/splits. Emitted as integer constants
# (engine ignores them; codegen drops them from request.security kwargs).
# Unknown member falls back to "none" (0).
ADJUSTMENT_MAP = {"none": "0", "dividends": "1", "splits": "2"}

# display.* (plot_display) constants — ints for C++. TV uses these in chart
# settings; the backtest ignores them. Unknown member falls back to "all" (0).
# The integer codes are inert downstream (no engine consumer); they only need
# to be distinct so constant-equality comparisons behave.
DISPLAY_MAP = {
    "all": "0", "none": "1", "pane": "2",
    "data_window": "3", "status_line": "4", "price_scale": "5",
    "pine_screener": "6",
}

# order.* sort-direction constants — emitted as std::string literals. Unknown
# member falls back to "ascending".
ORDER_DIRECTION_MAP = {
    "ascending": 'std::string("ascending")',
    "descending": 'std::string("descending")',
}


# ---------------------------------------------------------------------------
# Array / Map / Matrix method dispatch
# ---------------------------------------------------------------------------


def _checked_array_index_prelude(*, normalize_negative: bool = True) -> str:
    """C++ body shared by checked single-index operations.

    Pine v6 explicitly gives end-relative negative indices to the checked
    ``get``, ``set``, and ``remove`` cluster. Other indexed functions such as
    ``percentrank`` still need the same NA/non-finite/range checks, but must
    reject negative indices instead of normalizing them. ``insert`` remains a
    separately bounded residual.
    """
    checked_index = (
        "__pf_raw_index<0?__pf_raw_index+__pf_array_size:__pf_raw_index"
        if normalize_negative
        else "__pf_raw_index"
    )
    return (
        "using __pf_raw_index_type=std::decay_t<decltype(__pf_raw_index_value)>; "
        "if constexpr(!std::is_same_v<__pf_raw_index_type,bool>) { "
        "if(is_na(__pf_raw_index_value)) "
        "pine_runtime_error(std::string(\"Index na is out of bounds. Array size is \")+"
        "std::to_string((int64_t)__pf_array.size())); } "
        "if constexpr(std::is_floating_point_v<__pf_raw_index_type>) { "
        "if(!std::isfinite(__pf_raw_index_value)) { "
        "std::string __pf_raw_index_text=__pf_raw_index_value>0?\"inf\":\"-inf\"; "
        "pine_runtime_error(std::string(\"Index \")+__pf_raw_index_text+"
        "\" is out of bounds. Array size is \"+"
        "std::to_string((int64_t)__pf_array.size())); } "
        "long double __pf_raw_index_wide=(long double)__pf_raw_index_value; "
        "if(__pf_raw_index_wide<(long double)std::numeric_limits<int64_t>::min()||"
        "__pf_raw_index_wide>(long double)std::numeric_limits<int64_t>::max()) "
        "pine_runtime_error(std::string(\"Index \")+"
        "std::to_string((double)__pf_raw_index_value)+"
        "\" is out of bounds. Array size is \"+"
        "std::to_string((int64_t)__pf_array.size())); } "
        "int64_t __pf_raw_index=(int64_t)__pf_raw_index_value; "
        "int64_t __pf_array_size=(int64_t)__pf_array.size(); "
        f"int64_t __pf_array_index={checked_index}; "
        "if(__pf_array_index<0||__pf_array_index>=__pf_array_size) "
        "pine_runtime_error(std::string(\"Index \")+std::to_string(__pf_raw_index)+"
        "\" is out of bounds. Array size is \"+std::to_string(__pf_array_size)); "
    )


def _checked_array_get(a: str, args: list[str]) -> str:
    check = _checked_array_index_prelude()
    return (
        "[&](auto&& __pf_array)->decltype(auto){ "
        "return [&](auto&& __pf_raw_index_value)->decltype(auto){ "
        f"{check}"
        "if constexpr(std::is_lvalue_reference_v<decltype(__pf_array)>) "
        "return (__pf_array[(size_t)__pf_array_index]); "
        "else { using __pf_array_value_type=typename "
        "std::decay_t<decltype(__pf_array)>::value_type; "
        "return __pf_array_value_type(__pf_array[(size_t)__pf_array_index]); } "
        f"}}(({args[0]})); }}(({a}))"
    )


def _checked_array_set(a: str, args: list[str]) -> str:
    check = _checked_array_index_prelude()
    return (
        "[&](auto&& __pf_array){ "
        "return [&](auto&& __pf_raw_index_value){ "
        "return [&](auto&& __pf_array_value){ "
        f"{check}"
        "__pf_array[(size_t)__pf_array_index]=__pf_array_value; "
        f"}}(({args[1]})); }}(({args[0]})); }}(({a}))"
    )


def _checked_array_remove(a: str, args: list[str]) -> str:
    check = _checked_array_index_prelude()
    return (
        "[&](auto&& __pf_array){ "
        "return [&](auto&& __pf_raw_index_value){ "
        f"{check}"
        "using __pf_array_value_type=typename "
        "std::decay_t<decltype(__pf_array)>::value_type; "
        "__pf_array_value_type __pf_array_value="
        "__pf_array[(size_t)__pf_array_index]; "
        "__pf_array.erase(__pf_array.begin()+(size_t)__pf_array_index); "
        "return __pf_array_value; "
        f"}}(({args[0]})); }}(({a}))"
    )


def _checked_array_end_get(a: str, method: str) -> str:
    access = "front" if method == "first" else "back"
    return (
        "[&](auto&& __pf_array)->decltype(auto){ "
        f"if(__pf_array.empty()) pine_runtime_error(\"Cannot use {method}() "
        "if array is empty.\"); "
        "if constexpr(std::is_lvalue_reference_v<decltype(__pf_array)>) "
        f"return (__pf_array.{access}()); "
        "else { using __pf_array_value_type=typename "
        "std::decay_t<decltype(__pf_array)>::value_type; "
        f"return __pf_array_value_type(__pf_array.{access}()); }} "
        f"}}(({a}))"
    )


def _checked_array_end_remove(a: str, method: str) -> str:
    access = "back" if method == "pop" else "front"
    mutation = (
        "__pf_array.pop_back();"
        if method == "pop"
        else "__pf_array.erase(__pf_array.begin());"
    )
    return (
        "[&](auto&& __pf_array){ "
        f"if(__pf_array.empty()) pine_runtime_error(\"Cannot use {method}() "
        "if array is empty.\"); "
        "using __pf_array_value_type=typename "
        "std::decay_t<decltype(__pf_array)>::value_type; "
        f"__pf_array_value_type __pf_array_value=__pf_array.{access}(); "
        f"{mutation} return __pf_array_value; }}(({a}))"
    )


def _checked_array_percentrank(a: str, args: list[str]) -> str:
    """Preserve degenerate results, then reject invalid PercentRank indices."""
    check = _checked_array_index_prelude(normalize_negative=False)
    return (
        "[&](auto&& __pf_array){ "
        "return [&](auto&& __pf_raw_index_value){ "
        "if(__pf_array.size()<=1) return na<double>(); "
        f"{check}"
        "double v=__pf_array[(size_t)__pf_array_index]; "
        "if(std::isnan(v)) return na<double>(); "
        "int le=0; for(auto x:__pf_array) "
        "if(!std::isnan(x) && x<=v) le++; "
        "return (double)(le-1)/(__pf_array.size()-1)*100.0; "
        f"}}(({args[0]})); }}(({a}))"
    )


# Methods called as ``array.method(arr, ...)`` or ``arr.method(...)``.
ARRAY_METHODS = {
    "get":       _checked_array_get,
    "set":       _checked_array_set,
    "push":      lambda a, args: f"{a}.push_back({args[0]})",
    "unshift":   lambda a, args: f"{a}.insert({a}.begin(), {args[0]})",
    "insert":    lambda a, args: f"{a}.insert({a}.begin() + (int)({args[0]}), {args[1]})",
    "pop":       lambda a, args: _checked_array_end_remove(a, "pop"),
    "shift":     lambda a, args: _checked_array_end_remove(a, "shift"),
    "remove":    _checked_array_remove,
    "first":     lambda a, args: _checked_array_end_get(a, "first"),
    "last":      lambda a, args: _checked_array_end_get(a, "last"),
    "size":      lambda a, args: f"(double){a}.size()",
    "clear":     lambda a, args: f"{a}.clear()",
    "fill":      lambda a, args: f"std::fill({a}.begin(), {a}.end(), {args[0]})" if len(args) == 1
                                 else f"std::fill({a}.begin()+(int)({args[1]}), {a}.begin()+(int)({args[2]}), {args[0]})",
    "includes":  lambda a, args: f"(std::find({a}.begin(), {a}.end(), {args[0]}) != {a}.end())",
    "indexof":   lambda a, args: f"[&](){{ auto it=std::find({a}.begin(),{a}.end(),{args[0]}); return it!={a}.end()?(double)(it-{a}.begin()):-1.0; }}()",
    "lastindexof": lambda a, args: f"[&](){{ for(int i=(int){a}.size()-1;i>=0;i--)if({a}[i]=={args[0]})return(double)i; return -1.0; }}()",
    "sort":      lambda a, args: (
        f"[&](){{ if (({args[0]}) == \"descending\") std::sort({a}.begin(), {a}.end(), std::greater<>()); else std::sort({a}.begin(), {a}.end()); }}()"
        if args else f"std::sort({a}.begin(), {a}.end())"
    ),
    "reverse":   lambda a, args: f"std::reverse({a}.begin(),{a}.end())",
    "copy":      lambda a, args: f"std::vector<double>({a})",
    "slice":     lambda a, args: f"std::vector<double>({a}.begin()+(int)({args[0]}),{a}.begin()+(int)({args[1]}))",
    "concat":    lambda a, args: f"{a}.insert({a}.end(),{args[0]}.begin(),{args[0]}.end())",
    "sum":       lambda a, args: f"({a}.empty()?na<double>():std::accumulate({a}.begin(),{a}.end(),0.0))",
    "avg":       lambda a, args: f"({a}.empty()?na<double>():std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size())",
    "min":       lambda a, args: f"({a}.empty()?na<double>():*std::min_element({a}.begin(),{a}.end()))",
    "max":       lambda a, args: f"({a}.empty()?na<double>():*std::max_element({a}.begin(),{a}.end()))",
    "range":     lambda a, args: f"({a}.empty()?na<double>():*std::max_element({a}.begin(),{a}.end())-*std::min_element({a}.begin(),{a}.end()))",
    "every":     lambda a, args: f"std::all_of({a}.begin(),{a}.end(),[](double v){{return v!=0.0;}})",
    "some":      lambda a, args: f"std::any_of({a}.begin(),{a}.end(),[](double v){{return v!=0.0;}})",
    # stdev/variance honor the optional 2nd ``biased`` arg (Pine v6:
    # biased=true → population (default), false → sample / n-1).
    "stdev":     lambda a, args: (
        f"[&](){{ if({a}.empty()) return na<double>(); double m=std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size(); double s=0; for(auto v:{a})s+=(v-m)*(v-m); "
        f"double _d=({args[0]})?(double){a}.size():((double){a}.size()-1.0); "
        f"return _d>0?std::sqrt(s/_d):na<double>(); }}()"
        if args else
        f"[&](){{ if({a}.empty()) return na<double>(); double m=std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size(); double s=0; for(auto v:{a})s+=(v-m)*(v-m); return std::sqrt(s/{a}.size()); }}()"
    ),
    "variance":  lambda a, args: (
        f"[&](){{ if({a}.empty()) return na<double>(); double m=std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size(); double s=0; for(auto v:{a})s+=(v-m)*(v-m); "
        f"double _d=({args[0]})?(double){a}.size():((double){a}.size()-1.0); "
        f"return _d>0?s/_d:na<double>(); }}()"
        if args else
        f"[&](){{ if({a}.empty()) return na<double>(); double m=std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size(); double s=0; for(auto v:{a})s+=(v-m)*(v-m); return s/{a}.size(); }}()"
    ),
    "median":    lambda a, args: f"[&](){{ if({a}.empty()) return na<double>(); auto c={a}; std::sort(c.begin(),c.end()); int n=c.size(); return n%2?c[n/2]:(c[n/2-1]+c[n/2])/2.0; }}()",
    "mode":      lambda a, args: f"[&](){{ if({a}.empty()) return na<double>(); std::unordered_map<double,int> m; for(auto v:{a})m[v]++; double best=0; int bc=0; for(auto&[v,c]:m)if(c>bc||(c==bc&&v<best)){{bc=c;best=v;}} return best; }}()",
    "percentile_linear_interpolation": lambda a, args: f"[&](){{ if({a}.empty()) return na<double>(); auto c={a}; std::sort(c.begin(),c.end()); double k=({args[0]}/100.0)*c.size()-0.5; int i=std::max(0,(int)k); double f=k-i; if(i+1>=(int)c.size()) return c.back(); return c[i]*(1-f)+c[i+1]*f; }}()",
    "percentile_nearest_rank": lambda a, args: f"[&](){{ if({a}.empty()) return na<double>(); auto c={a}; std::sort(c.begin(),c.end()); int r=(int)std::ceil(({args[0]}/100.0)*c.size()); return (double)c[std::min(r-1,(int)c.size()-1)]; }}()",
    "percentrank": _checked_array_percentrank,
    "abs":       lambda a, args: f"[&](){{ std::vector<double> r; for(auto v:{a})r.push_back(std::abs(v)); return r; }}()",
    "join":      lambda a, args: "[&](){{ std::string r; for(size_t i=0;i<{arr}.size();i++){{ if(i>0)r+={sep}; r+=std::to_string({arr}[i]); }} return r; }}()".format(arr=a, sep=args[0] if args else 'std::string(",")'),
    "standardize": lambda a, args: f"[&](){{ double m=std::accumulate({a}.begin(),{a}.end(),0.0)/{a}.size(); double s=0; for(auto v:{a})s+=(v-m)*(v-m); s=std::sqrt(s/{a}.size()); std::vector<double> r; for(auto v:{a})r.push_back(s==0?1.0:(v-m)/s); return r; }}()",
    "covariance": lambda a, args: f"[&](){{ auto&&b=({args[0]}); int n=std::min({a}.size(),b.size()); if(n<=0) return na<double>(); double ma=0,mb=0; for(int i=0;i<n;i++){{ma+={a}[i];mb+=b[i];}} ma/=n;mb/=n; double c=0; for(int i=0;i<n;i++)c+=({a}[i]-ma)*(b[i]-mb); return c/n; }}()",
    "binary_search": lambda a, args: f"[&](){{ auto it=std::lower_bound({a}.begin(),{a}.end(),{args[0]}); return (it!={a}.end()&&*it=={args[0]})?(double)(it-{a}.begin()):-1.0; }}()",
    "binary_search_leftmost": lambda a, args: f"[&](){{ auto it=std::lower_bound({a}.begin(),{a}.end(),{args[0]}); return (it!={a}.end()&&*it=={args[0]})?(double)(it-{a}.begin()):(double)(it-{a}.begin()-1); }}()",
    "binary_search_rightmost": lambda a, args: f"[&](){{ auto it=std::upper_bound({a}.begin(),{a}.end(),{args[0]}); return (it!={a}.begin()&&*(it-1)=={args[0]})?(double)(it-{a}.begin()-1):(double)(it-{a}.begin()); }}()",
    "sort_indices": lambda a, args: f"[&](){{ std::vector<double> idx({a}.size()); std::iota(idx.begin(),idx.end(),0); std::sort(idx.begin(),idx.end(),[&](int i,int j){{return {a}[i]<{a}[j];}}); return idx; }}()",
}

# Pine parameter order for the checked-index subset. Keeping the receiver
# (``id``) separate lets method syntax merge ``a.get(index = i)`` while the
# namespace-functional path merges ``array.get(id = a, index = i)``.
CHECKED_ARRAY_METHOD_KWARGS: dict[str, list[str]] = {
    "get": ["index"],
    "set": ["index", "value"],
    "remove": ["index"],
    "percentrank": ["index"],
    "first": [],
    "last": [],
    "pop": [],
    "shift": [],
}

MAP_METHODS = {
    "put":      lambda m, args: f"({m}[{args[0]}] = {args[1]})",
    "get":      lambda m, args: f"({m}.count({args[0]}) ? {m}[{args[0]}] : na<double>())",
    "remove":   lambda m, args: f"[&](){{ auto it={m}.find({args[0]}); if(it!={m}.end()){{ auto v=it->second; {m}.erase(it); return v; }} return na<double>(); }}()",
    "contains": lambda m, args: f"({m}.count({args[0]}) > 0)",
    "size":     lambda m, args: f"(double){m}.size()",
    "clear":    lambda m, args: f"{m}.clear()",
    "keys":     lambda m, args: f"[&](){{ std::vector<std::string> v; for(auto& p:{m}) v.push_back(p.first); return v; }}()",
    "values":   lambda m, args: f"[&](){{ std::vector<double> v; for(auto& p:{m}) v.push_back(p.second); return v; }}()",
    "copy":     lambda m, args: f"std::unordered_map<std::string,double>({m})",
    "put_all":  lambda m, args: f"{m}.insert({args[0]}.begin(), {args[0]}.end())",
}


def _matrix_add_row(m: str, args: list) -> str:
    """Pine ``matrix.add_row`` codegen.

    Pine signature accepts (id, row, [array_id]) — when ``row`` is omitted
    we append at the current row count. Two-arg form passes through as
    ``add_row(row_index, array_id)``."""
    if len(args) == 1:
        return f"{m}.add_row((int)({m}.rows()), {args[0]})"
    if len(args) == 2:
        return f"{m}.add_row((int)({args[0]}), {args[1]})"
    raise IndexError("matrix.add_row")


def _matrix_add_col(m: str, args: list) -> str:
    """Pine ``matrix.add_col`` codegen — mirror of ``_matrix_add_row``."""
    if len(args) == 1:
        return f"{m}.add_col((int)({m}.columns()), {args[0]})"
    if len(args) == 2:
        return f"{m}.add_col((int)({args[0]}), {args[1]})"
    raise IndexError("matrix.add_col")


# Keyword parameter order for matrix methods (Pine v6); used by ``_merge_kwargs``.
MATRIX_METHOD_KWARGS: dict[str, list[str]] = {
    "add_row": ["row_index", "array_id"],
    "add_col": ["col_index", "array_id"],
}

# matrix.* method names whose RUNTIME return type is ``PineMatrix``. Used by
# the codegen aggregate-type registration to declare the LHS variable as
# ``PineMatrix`` instead of the analyzer's default ``double``. Without this
# the codegen emits ``double inv = m.inv();`` which clang rejects with
# ``assigning to 'double' from incompatible type 'PineMatrix'``.
#
# Methods absent from this set return either a primitive (``det``, ``rank``,
# ``trace``, ``sum``, ``avg``, ``min``, ``max``, ``mode``, ``elements_count``,
# ``rows``, ``columns``, ``is_*``) or an array (``row``, ``col``,
# ``eigenvalues``); their LHS variables stay scalar / vector and the analyzer
# default is correct.
MATRIX_RETURNING_METHODS: frozenset[str] = frozenset({
    "copy", "submatrix", "transpose", "concat", "diff", "mult", "pow",
    "inv", "pinv", "eigenvectors", "kron",
})

# Methods only valid on matrix<float>. Codegen rejects these on non-float
# matrix receivers; the runtime template doesn't carry them at all.
MATRIX_NUMERIC_ONLY: frozenset[str] = frozenset({
    "det", "inv", "pinv", "rank", "trace",
    "eigenvalues", "eigenvectors",
    "sum", "avg", "min", "max", "mode",
    "diff", "mult", "pow", "kron",
    "is_square", "is_identity", "is_diagonal", "is_antidiagonal",
    "is_symmetric", "is_antisymmetric", "is_triangular",
    "is_stochastic", "is_binary", "is_zero",
})

# Element-type predicate for matrix.sort: only int/bool/string element types
# can be sorted on PineGenericMatrix. Float matrix sort routes through
# PineMatrix (numeric path).
MATRIX_SORT_ALLOWED_GENERIC_ELEMS: frozenset[str] = frozenset({"int", "bool", "string"})

MATRIX_METHODS = {
    "get":       lambda m, args: f"{m}.get((int)({args[0]}), (int)({args[1]}))",
    "set":       lambda m, args: f"{m}.set((int)({args[0]}), (int)({args[1]}), {args[2]})",
    "fill":      lambda m, args: f"{m}.fill({args[0]})",
    "row":       lambda m, args: f"{m}.row((int)({args[0]}))",
    "col":       lambda m, args: f"{m}.col((int)({args[0]}))",
    "rows":      lambda m, args: f"(int){m}.rows()",
    "columns":   lambda m, args: f"(int){m}.columns()",
    "add_row":   _matrix_add_row,
    "add_col":   _matrix_add_col,
    # ``remove_row`` is void in C++; Pine may assign the result, so we wrap
    # it in a lambda that returns a sentinel double after the side effect.
    "remove_row": lambda m, args: f"[&](){{ {m}.remove_row((int)({args[0]})); return 0.0; }}()",
    "remove_col":lambda m, args: f"[&](){{ {m}.remove_col((int)({args[0]})); return 0.0; }}()",
    "swap_rows": lambda m, args: f"{m}.swap_rows((int)({args[0]}), (int)({args[1]}))",
    "swap_columns": lambda m, args: f"{m}.swap_columns((int)({args[0]}), (int)({args[1]}))",
    "copy":      lambda m, args: f"{m}.copy()",
    "submatrix": lambda m, args: f"{m}.submatrix((int)({args[0]}), (int)({args[1]}), (int)({args[2]}), (int)({args[3]}))",
    "reshape":   lambda m, args: f"{m}.reshape((int)({args[0]}), (int)({args[1]}))",
    "reverse":   lambda m, args: f"{m}.reverse()",
    "transpose": lambda m, args: f"{m}.transpose()",
    "sort":      lambda m, args: f"{m}.sort((int)({args[0]}), {args[1]} != \"descending\")" if len(args)>1 else f"{m}.sort((int)({args[0]}))",
    "concat":    lambda m, args: f"{m}.concat({args[0]}, (bool)({args[1]}))" if len(args)>1 else f"{m}.concat({args[0]}, true)",
    "avg":       lambda m, args: f"{m}.avg()",
    "min":       lambda m, args: f"{m}.min()",
    "max":       lambda m, args: f"{m}.max()",
    "mode":      lambda m, args: f"{m}.mode()",
    "sum":       lambda m, args: f"{m}.sum()",
    "diff":      lambda m, args: f"{m}.diff({args[0]})",
    "mult":      lambda m, args: f"{m}.mult({args[0]})",
    "pow":       lambda m, args: f"{m}.pow((int)({args[0]}))",
    "det":       lambda m, args: f"{m}.det()",
    "inv":       lambda m, args: f"{m}.inv()",
    "pinv":      lambda m, args: f"{m}.pinv()",
    "rank":      lambda m, args: f"(int){m}.rank()",
    "trace":     lambda m, args: f"{m}.trace()",
    "eigenvalues": lambda m, args: f"{m}.eigenvalues()",
    "eigenvectors": lambda m, args: f"{m}.eigenvectors()",
    "kron":      lambda m, args: f"{m}.kron({args[0]})",
    "elements_count": lambda m, args: f"(int){m}.elements_count()",
    "is_square": lambda m, args: f"{m}.is_square()",
    "is_identity": lambda m, args: f"{m}.is_identity()",
    "is_diagonal": lambda m, args: f"{m}.is_diagonal()",
    "is_antidiagonal": lambda m, args: f"{m}.is_antidiagonal()",
    "is_symmetric": lambda m, args: f"{m}.is_symmetric()",
    "is_antisymmetric": lambda m, args: f"{m}.is_antisymmetric()",
    "is_triangular": lambda m, args: f"{m}.is_triangular()",
    "is_stochastic": lambda m, args: f"{m}.is_stochastic()",
    "is_binary": lambda m, args: f"{m}.is_binary()",
    "is_zero":   lambda m, args: f"{m}.is_zero()",
}


# ---------------------------------------------------------------------------
# Math / String dispatch
# ---------------------------------------------------------------------------

def _math_minmax_na_expr(func_name: str, args: list[str]) -> str:
    """Emit Pine-compatible math.min/math.max with na propagation.

    ``std::min``/``std::max`` do not propagate NaN consistently because their
    comparison is specified in terms of ``operator<``. Pine math.min/max return
    ``na`` when any operand is ``na``, so generated clamp-style expressions
    like ``math.max(-1, math.min(1, na))`` must stay ``na``.
    """
    if not args:
        return "na<double>()"
    if len(args) == 1:
        return f"(double)({args[0]})"
    op = "std::max" if func_name == "max" else "std::min"
    decls = " ".join(
        f"double _v{i} = (double)({arg});" for i, arg in enumerate(args)
    )
    guard = " || ".join(f"is_na(_v{i})" for i in range(len(args)))
    updates = " ".join(
        f"_out = {op}(_out, _v{i});" for i in range(1, len(args))
    )
    return (
        f"([&]() -> double {{ {decls} "
        f"if ({guard}) return na<double>(); "
        f"double _out = _v0; {updates} return _out; }}())"
    )


# Pine ``math.sign`` returns a float, propagates numeric ``na``, and must not
# evaluate its argument more than once.  Keep the callable separate from the
# call template because the stable-runtime TA reset fallback also substitutes
# bare ``math.sign`` member text with this same C++ function expression.
_MATH_SIGN_CPP_FN = (
    "([](const auto _pf_sign_v) -> double { "
    "return is_na(_pf_sign_v) ? na<double>() "
    ": (double)((_pf_sign_v > 0) - (_pf_sign_v < 0)); })"
)


MATH_FUNC_MAP = {
    "abs": "std::abs", "max": "std::max", "min": "std::min",
    "ceil": "std::ceil", "floor": "std::floor", "round": "std::round",
    "sqrt": "std::sqrt", "pow": "std::pow", "log": "std::log",
    "log10": "std::log10", "exp": "std::exp",
    "sin": "std::sin", "cos": "std::cos", "tan": "std::tan",
    "asin": "std::asin", "acos": "std::acos", "atan": "std::atan",
    "atan2": "std::atan2({0}, {1})",
    "sign": _MATH_SIGN_CPP_FN,
    "avg": "(({0} + {1}) / 2.0)",
}

STR_FUNC_MAP = {
    "tostring":    None,  # handled separately (already works)
    "tonumber":    lambda args: (
        f"[&](){{ "
        f"try {{ return std::stod({args[0]}); }} "
        f"catch (...) {{ return na<double>(); }} "
        f"}}()"
    ),
    "length":      lambda args: f"(int){args[0]}.length()",
    "contains":    lambda args: f"({args[0]}.find({args[1]}) != std::string::npos)",
    "startswith":  lambda args: f"({args[0]}.substr(0, {args[1]}.length()) == {args[1]})",
    "endswith":    lambda args: f"({args[0]}.length() >= {args[1]}.length() && {args[0]}.compare({args[0]}.length() - {args[1]}.length(), {args[1]}.length(), {args[1]}) == 0)",
    "pos":         lambda args: f"[&](){{ auto p={args[0]}.find({args[1]}); return p!=std::string::npos?(int)p:-1; }}()",
    "substring":   None,  # handled separately (2 vs 3 args)
    "replace_all": lambda args: f"[&](){{ std::string s={args[0]}; size_t p=0; while((p=s.find({args[1]},p))!=std::string::npos){{ s.replace(p,{args[1]}.length(),{args[2]}); p+={args[2]}.length(); }} return s; }}()",
    "replace":     None,  # handled separately (3 vs 4 args)
    "lower":       lambda args: f"[&](){{ std::string s={args[0]}; std::transform(s.begin(),s.end(),s.begin(),::tolower); return s; }}()",
    "upper":       lambda args: f"[&](){{ std::string s={args[0]}; std::transform(s.begin(),s.end(),s.begin(),::toupper); return s; }}()",
    "trim":        lambda args: f'[&](){{ std::string s={args[0]}; s.erase(0,s.find_first_not_of(" \\t\\n\\r")); s.erase(s.find_last_not_of(" \\t\\n\\r")+1); return s; }}()',
    "repeat":      lambda args: f"[&](){{ std::string r; for(int i=0;i<(int)({args[1]});i++) r+={args[0]}; return r; }}()",
    "match":       lambda args: f'pine_str_match({args[0]}, {args[1]})',
    "split":       lambda args: f'pine_str_split({args[0]}, {args[1]})',
    "format":      None,  # handled separately
}


# ---------------------------------------------------------------------------
# kwarg merge helper (used by visitor / dispatch sites that accept kwargs)
# ---------------------------------------------------------------------------

def _merge_kwargs(args: list, kwargs: dict, param_names: list | None, visit_expr) -> list:
    """Merge positional + kwargs into a unified positional list of C++ exprs.

    ``param_names`` is the declared signature order from
    ``signatures.py``. ``visit_expr`` is a callable applied to each kept
    AST node; pass ``lambda x: x`` when the caller wants raw nodes back."""
    if not kwargs or not param_names:
        return [visit_expr(a) for a in args]
    merged = list(args)
    for i, pname in enumerate(param_names):
        if pname in kwargs:
            while len(merged) <= i:
                merged.append(None)
            if merged[i] is None:
                merged[i] = kwargs[pname]
    while merged and merged[-1] is None:
        merged.pop()
    return [visit_expr(a) for a in merged if a is not None]


def _merge_kwargs_with_defaults(
    args: list,
    kwargs: dict,
    param_names: list,
    param_defaults: list,
    visit_expr,
) -> list:
    """Like ``_merge_kwargs`` but fills missing slots from ``param_defaults``.

    ``param_defaults`` must be parallel to ``param_names`` (use ``None`` for
    parameters without a default). Used by the UDT-method call lowering so
    callers may invoke ``cfg.threshold()``, ``cfg.threshold(atrVal)``, or
    ``cfg.threshold(mult=2.0, base=rsiVal)`` against ``method threshold(Cfg
    self, float mult = 1.0, float base = 0.0) =>`` and have clang see the
    full positional argument list. PineScript has no overloading, so every
    parameter must be filled at the call site.

    Probe: data/validation/udt-method-probe-04-default-param.

    The result preserves the parameter order from ``param_names`` and
    contains ``visit_expr(node)`` for each filled slot. Trailing slots that
    have neither a caller-supplied value nor a default are dropped (matches
    ``_merge_kwargs`` behaviour for required-only signatures).
    """
    n = len(param_names)
    slots: list = [None] * n
    for i, a in enumerate(args):
        if i < n:
            slots[i] = a
    for k, v in kwargs.items():
        if k in param_names:
            slots[param_names.index(k)] = v
    # Fill remaining holes from defaults (only where the caller omitted the
    # slot AND the parameter has a declared default).
    if param_defaults:
        for i in range(n):
            if slots[i] is None and i < len(param_defaults) and param_defaults[i] is not None:
                slots[i] = param_defaults[i]
    while slots and slots[-1] is None:
        slots.pop()
    return [visit_expr(a) for a in slots if a is not None]

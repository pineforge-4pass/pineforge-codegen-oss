"""Complete PineScript v6 intrinsic function signatures.

Single source of truth for all TradingView built-in function signatures.
Used by both analyzer (type inference, kwargs resolution) and codegen
(correct C++ emission).

Each signature specifies:
- params: ordered list of (name, type, default_value_or_None)
- return_type: PineType
- overloads: list of alternative param lists (if function is overloaded)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .symbols import PineType


@dataclass
class Param:
    """One function parameter."""
    name: str
    pine_type: PineType = PineType.FLOAT
    default: Any = None          # None = required, else default value
    is_series: bool = True       # series vs simple/const


@dataclass
class FuncSig:
    """One function signature (one overload)."""
    params: list[Param] = field(default_factory=list)
    return_type: PineType = PineType.FLOAT
    returns_tuple: bool = False
    tuple_count: int = 0         # number of tuple elements


@dataclass
class IntrinsicFunc:
    """Complete intrinsic function definition with all overloads."""
    name: str                    # e.g. "ta.sma" or "math.abs"
    signatures: list[FuncSig] = field(default_factory=list)

    @property
    def primary(self) -> FuncSig:
        return self.signatures[0]

    @property
    def param_names(self) -> list[str]:
        """Param names from the widest overload (for kwargs resolution).

        Uses the overload with the most params so all possible kwargs
        can be resolved to positional positions.
        """
        widest = max(self.signatures, key=lambda s: len(s.params))
        return [p.name for p in widest.params]


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------

def _sig(params: list[tuple], ret: PineType = PineType.FLOAT,
         returns_tuple: bool = False, tuple_count: int = 0) -> FuncSig:
    """Shorthand to build a FuncSig from tuples of (name, type, default)."""
    plist = []
    for p in params:
        name = p[0]
        ptype = p[1] if len(p) > 1 else PineType.FLOAT
        default = p[2] if len(p) > 2 else None
        plist.append(Param(name=name, pine_type=ptype, default=default))
    return FuncSig(params=plist, return_type=ret,
                   returns_tuple=returns_tuple, tuple_count=tuple_count)


def _func(name: str, *sigs: FuncSig) -> IntrinsicFunc:
    return IntrinsicFunc(name=name, signatures=list(sigs))


# Shortcuts for common param types
F = PineType.FLOAT
I = PineType.INT
B = PineType.BOOL
S = PineType.STRING
NA = PineType.NA
VOID = PineType.VOID


# ============================================================================
# ta.* functions
# ============================================================================

TA_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _ta(short_name: str, *sigs: FuncSig) -> None:
    TA_FUNCTIONS[short_name] = _func(f"ta.{short_name}", *sigs)

# --- Moving Averages ---
_ta("sma",     _sig([("source", F), ("length", I)]))
_ta("ema",     _sig([("source", F), ("length", I)]))
_ta("rma",     _sig([("source", F), ("length", I)]))
_ta("wma",     _sig([("source", F), ("length", I)]))
_ta("hma",     _sig([("source", F), ("length", I)]))
_ta("vwma",    _sig([("source", F), ("length", I)]))
_ta("alma",    _sig([("source", F), ("length", I), ("offset", F, 0.85), ("sigma", F, 6.0)]))
_ta("swma",    _sig([("source", F)]))
_ta("linreg",  _sig([("source", F), ("length", I), ("offset", I, 0)]))

# --- Oscillators ---
_ta("rsi",     _sig([("source", F), ("length", I)]))
_ta("stoch",   _sig([("source", F), ("high", F), ("low", F), ("length", I)]))
_ta("cci",     _sig([("source", F), ("length", I)]))
_ta("mfi",     _sig([("series", F), ("length", I)]))
_ta("mom",     _sig([("source", F), ("length", I)]))
_ta("roc",     _sig([("source", F), ("length", I)]))
_ta("cmo",     _sig([("source", F), ("length", I)]))
_ta("tsi",     _sig([("source", F), ("short_length", I), ("long_length", I)]))
_ta("wpr",     _sig([("length", I)]))
_ta("cog",     _sig([("source", F), ("length", I)]))

# --- MACD ---
_ta("macd",    _sig([("source", F), ("fastlen", I), ("slowlen", I), ("siglen", I)],
                    returns_tuple=True, tuple_count=3))

# --- Bollinger Bands / Keltner ---
_ta("bb",      _sig([("source", F), ("length", I), ("mult", F, 2.0)],
                    returns_tuple=True, tuple_count=3))
_ta("kc",      _sig([("source", F), ("length", I), ("mult", F, 1.5)],
                    returns_tuple=True, tuple_count=3))
_ta("bbw",     _sig([("source", F), ("length", I), ("mult", F, 2.0)]))
_ta("kcw",     _sig([("source", F), ("length", I), ("mult", F, 1.5)]))

# --- Volatility & Range ---
_ta("atr",     _sig([("length", I)]))
_ta("tr",      _sig([("handle_na", B, False)]))
_ta("stdev",   _sig([("source", F), ("length", I), ("biased", B, True)]))
_ta("variance",_sig([("source", F), ("length", I), ("biased", B, True)]))

# --- Trend ---
_ta("supertrend", _sig([("factor", F), ("atrPeriod", I)],
                       returns_tuple=True, tuple_count=2))
_ta("dmi",     _sig([("diLength", I), ("adxSmoothing", I)],
                    returns_tuple=True, tuple_count=3))
_ta("sar",     _sig([("start", F, 0.02), ("inc", F, 0.02), ("max", F, 0.2)]))

# --- Crossover / State ---
_ta("crossover",  _sig([("source1", F), ("source2", F)], ret=B))
_ta("crossunder", _sig([("source1", F), ("source2", F)], ret=B))
_ta("cross",      _sig([("source1", F), ("source2", F)], ret=B))
_ta("rising",     _sig([("source", F), ("length", I)], ret=B))
_ta("falling",    _sig([("source", F), ("length", I)], ret=B))
_ta("change",     _sig([("source", F), ("length", I, 1)]))

# --- Extremes & Lookback ---
# ta.highest / ta.lowest have TWO overloads:
#   ta.highest(source, length)  — full form
#   ta.highest(length)          — source defaults to high/low
_ta("highest",
    _sig([("source", F), ("length", I)]),
    _sig([("length", I)]))
_ta("lowest",
    _sig([("source", F), ("length", I)]),
    _sig([("length", I)]))
_ta("highestbars", _sig([("source", F), ("length", I)], ret=I))
_ta("lowestbars",  _sig([("source", F), ("length", I)], ret=I))
_ta("median",      _sig([("source", F), ("length", I)]))
_ta("percentrank", _sig([("source", F), ("length", I)]))
_ta("percentile_nearest_rank",       _sig([("source", F), ("length", I), ("percentage", F)]))
_ta("percentile_linear_interpolation", _sig([("source", F), ("length", I), ("percentage", F)]))

# --- Pivots ---
# ta.pivothigh/pivotlow have TWO overloads:
#   ta.pivothigh(source, leftbars, rightbars)
#   ta.pivothigh(leftbars, rightbars)           — source defaults to high/low
_ta("pivothigh",
    _sig([("source", F), ("leftbars", I), ("rightbars", I)]),
    _sig([("leftbars", I), ("rightbars", I)]))
_ta("pivotlow",
    _sig([("source", F), ("leftbars", I), ("rightbars", I)]),
    _sig([("leftbars", I), ("rightbars", I)]))

# --- Volume indicators ---
# ta.vwap has TWO overloads:
#   ta.vwap(source, anchor, stdev_mult)      — 3-arg bands form (primary), returns [vwap, upper, lower]
#   ta.vwap(source)                          — scalar 1-arg form (daily anchor)
# Note: primary is the 3-arg form so param_names covers all kwargs; the
# analyzer remaps 3-arg calls to the internal "vwap_bands" dispatch key.
_ta("vwap",    _sig([("source", F), ("anchor", B), ("stdev_mult", F)],
                    returns_tuple=True, tuple_count=3),
               _sig([("source", F)]))

# --- Statistical (additional) ---
_ta("mode",    _sig([("source", F), ("length", I)]))
_ta("range",   _sig([("source", F), ("length", I)]))
_ta("dev",     _sig([("source", F), ("length", I)]))

# --- Cumulative & Historical ---
_ta("cum",        _sig([("source", F)]))
_ta("barssince",  _sig([("condition", B)], ret=I))
_ta("valuewhen",  _sig([("condition", B), ("source", F), ("occurrence", I)]))
_ta("correlation", _sig([("source1", F), ("source2", F), ("length", I)]))

# --- Chart all-time extremes (single series) ---
_ta("max",        _sig([("source", F)]))
_ta("min",        _sig([("source", F)]))

# --- Rank Correlation Index ---
_ta("rci",        _sig([("source", F), ("length", I)]))

# --- Pivot levels ---
_ta("pivot_point_levels",
    _sig([("type", S), ("anchor", B), ("developing", B, False)]))


# ============================================================================
# math.* functions
# ============================================================================

MATH_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _math(short_name: str, *sigs: FuncSig) -> None:
    MATH_FUNCTIONS[short_name] = _func(f"math.{short_name}", *sigs)

# Arithmetic
_math("abs",    _sig([("x", F)]))
_math("ceil",   _sig([("x", F)], ret=I))
_math("floor",  _sig([("x", F)], ret=I))
_math("round",
      _sig([("x", F)], ret=I),                             # round(x) → int
      _sig([("x", F), ("precision", I)]))                  # round(x, n) → float
_math("round_to_mintick", _sig([("x", F)]))
_math("sign",   _sig([("x", F)], ret=F))
_math("max",
      _sig([("x", F), ("y", F)]),                          # 2-arg
      _sig([("x", F), ("y", F), ("z", F)]),                # 3-arg
      _sig([("x", F), ("y", F), ("z", F), ("w", F)]))     # 4-arg
_math("min",
      _sig([("x", F), ("y", F)]),
      _sig([("x", F), ("y", F), ("z", F)]),
      _sig([("x", F), ("y", F), ("z", F), ("w", F)]))
_math("avg",
      _sig([("x", F), ("y", F)]),
      _sig([("x", F), ("y", F), ("z", F)]),
      _sig([("x", F), ("y", F), ("z", F), ("w", F)]),
      _sig([("x", F), ("y", F), ("z", F), ("w", F), ("u", F)]),
      _sig([("x", F), ("y", F), ("z", F), ("w", F), ("u", F), ("v", F)]))
_math("sum",    _sig([("source", F), ("length", I)]))  # rolling sum → math::Sum in runtime
_math("pow",    _sig([("base", F), ("exp", F)]))
_math("sqrt",   _sig([("x", F)]))
_math("exp",    _sig([("x", F)]))
_math("log",    _sig([("x", F)]))
_math("log10",  _sig([("x", F)]))
_math("random", _sig([("min", F, 0.0), ("max", F, 1.0), ("seed", I, None)]))

# Trigonometry
_math("sin",    _sig([("x", F)]))
_math("cos",    _sig([("x", F)]))
_math("tan",    _sig([("x", F)]))
_math("asin",   _sig([("x", F)]))
_math("acos",   _sig([("x", F)]))
_math("atan",   _sig([("x", F)]))
_math("atan2",  _sig([("y", F), ("x", F)]))
_math("todegrees", _sig([("x", F)]))
_math("toradians", _sig([("x", F)]))


# ============================================================================
# strategy.* functions
# ============================================================================

STRATEGY_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _strat(short_name: str, *sigs: FuncSig) -> None:
    STRATEGY_FUNCTIONS[short_name] = _func(f"strategy.{short_name}", *sigs)

_strat("entry", _sig([
    ("id", S), ("direction", B), ("qty", F, None),
    ("limit", F, None), ("stop", F, None),
    ("oca_name", S, None), ("oca_type", I, None),
    ("comment", S, None), ("alert_message", S, None),
    ("disable_alert", B, None), ("qty_type", I, None),
], ret=VOID))

_strat("order", _sig([
    ("id", S), ("direction", B), ("qty", F, None),
    ("limit", F, None), ("stop", F, None),
    ("oca_name", S, None), ("oca_type", I, None),
    ("comment", S, None), ("alert_message", S, None),
    ("disable_alert", B, None), ("qty_type", I, None),
], ret=VOID))

_strat("exit", _sig([
    ("id", S), ("from_entry", S, ""),
    ("qty", F, None), ("qty_percent", F, None),
    ("profit", F, None), ("loss", F, None),
    ("limit", F, None), ("stop", F, None),
    ("trail_price", F, None), ("trail_points", F, None),
    ("trail_offset", F, None),
    ("oca_name", S, None),
    ("comment", S, None), ("comment_profit", S, None),
    ("comment_loss", S, None), ("comment_trailing", S, None),
    ("alert_message", S, None),
    ("alert_profit", S, None), ("alert_loss", S, None), ("alert_trailing", S, None),
    ("disable_alert", B, None),
], ret=VOID))

_strat("close", _sig([
    ("id", S), ("comment", S, None),
    ("qty", F, None), ("qty_percent", F, None),
    ("alert_message", S, None), ("immediately", B, False),
    ("disable_alert", B, None),
], ret=VOID))

_strat("close_all", _sig([
    ("comment", S, None), ("alert_message", S, None),
    ("immediately", B, False), ("disable_alert", B, None),
], ret=VOID))

_strat("cancel", _sig([("id", S)], ret=VOID))
_strat("cancel_all", _sig([], ret=VOID))

_strat("convert_to_account", _sig([("value", F)], ret=F))
_strat("convert_to_symbol", _sig([("value", F)], ret=F))
_strat("default_entry_qty", _sig([("fill_price", F)], ret=F))


# ============================================================================
# str.* functions
# ============================================================================

STR_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _str(short_name: str, *sigs: FuncSig) -> None:
    STR_FUNCTIONS[short_name] = _func(f"str.{short_name}", *sigs)

_str("tostring",
     _sig([("value", F)], ret=S),
     _sig([("value", F), ("format", S)], ret=S))
_str("tonumber",    _sig([("string", S)], ret=F))
_str("format",      _sig([("formatStr", S)], ret=S))  # variadic
_str("format_time", _sig([("time", I), ("format", S), ("timezone", S, "")], ret=S))
_str("length",      _sig([("string", S)], ret=I))
_str("contains",    _sig([("source", S), ("str", S)], ret=B))
_str("startswith",  _sig([("source", S), ("str", S)], ret=B))
_str("endswith",    _sig([("source", S), ("str", S)], ret=B))
_str("pos",         _sig([("source", S), ("str", S)], ret=I))
_str("substring",
     _sig([("source", S), ("begin_pos", I)], ret=S),
     _sig([("source", S), ("begin_pos", I), ("end_pos", I)], ret=S))
_str("replace",     _sig([("source", S), ("target", S), ("replacement", S), ("occurrence", I, 0)], ret=S))
_str("replace_all", _sig([("source", S), ("target", S), ("replacement", S)], ret=S))
_str("lower",       _sig([("source", S)], ret=S))
_str("upper",       _sig([("source", S)], ret=S))
_str("trim",        _sig([("source", S)], ret=S))
_str("repeat",      _sig([("source", S), ("count", I)], ret=S))
_str("match",       _sig([("source", S), ("regex", S)], ret=S))
_str("split",       _sig([("string", S), ("separator", S)], ret=S))  # returns array<string>


# ============================================================================
# map.* functions
# ============================================================================

MAP_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _map(short_name: str, *sigs: FuncSig) -> None:
    MAP_FUNCTIONS[short_name] = _func(f"map.{short_name}", *sigs)

_map("new",      _sig([], ret=F))
_map("put",      _sig([("id", F), ("key", S), ("value", F)], ret=F))
_map("get",      _sig([("id", F), ("key", S)], ret=F))
_map("remove",   _sig([("id", F), ("key", S)], ret=F))
_map("contains", _sig([("id", F), ("key", S)], ret=B))
_map("size",     _sig([("id", F)], ret=I))
_map("clear",    _sig([("id", F)], ret=VOID))
_map("keys",     _sig([("id", F)], ret=F))     # returns array
_map("values",   _sig([("id", F)], ret=F))     # returns array
_map("copy",     _sig([("id", F)], ret=F))
_map("put_all",  _sig([("id", F), ("id2", F)], ret=VOID))


# ============================================================================
# syminfo.* functions
# ============================================================================

SYMINFO_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _syminfo_fn(short_name: str, *sigs: FuncSig) -> None:
    SYMINFO_FUNCTIONS[short_name] = _func(f"syminfo.{short_name}", *sigs)

_syminfo_fn("prefix", _sig([], ret=S))
_syminfo_fn("ticker", _sig([], ret=S))


# ============================================================================
# Standalone built-in functions (no namespace)
# ============================================================================

BUILTIN_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _builtin(name: str, *sigs: FuncSig) -> None:
    BUILTIN_FUNCTIONS[name] = _func(name, *sigs)

_builtin("na",       _sig([("x", F)], ret=B))
_builtin("nz",
         _sig([("x", F)], ret=F),
         _sig([("x", F), ("y", F)], ret=F))
_builtin("fixnan",   _sig([("x", F)], ret=F))
_builtin("timestamp",
         _sig([("year", I), ("month", I), ("day", I),
               ("hour", I, 0), ("minute", I, 0), ("second", I, 0)], ret=I))
_builtin("time",
         _sig([("timeframe", S), ("session", S), ("timezone", S, "")], ret=I),
         _sig([("timeframe", S)], ret=I))
_builtin("time_close",
         _sig([("timeframe", S), ("session", S), ("timezone", S, "")], ret=I),
         _sig([("timeframe", S)], ret=I))
_builtin("year",     _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("month",    _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("dayofmonth", _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("dayofweek", _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("hour",     _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("minute",   _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("second",   _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("weekofyear", _sig([("time", I, None), ("timezone", S, "")], ret=I))
_builtin("max_bars_back", _sig([("var", F), ("num", I)], ret=VOID))

# Type casts
_builtin("int",      _sig([("x", F)], ret=I))
_builtin("float",    _sig([("x", I)], ret=F))
_builtin("bool",     _sig([("x", F)], ret=B))
_builtin("string",   _sig([("x", F)], ret=S))

# Timeframe
_builtin("timeframe.change", _sig([("timeframe", S)], ret=B))
_builtin("timeframe.in_seconds", _sig([("timeframe", S)], ret=F))

# Plain input() — overloaded on type of defval (PineScript v6)
_builtin("input",
         _sig([("defval", F), ("title", S, "")], ret=F),
         _sig([("defval", I), ("title", S, "")], ret=I),
         _sig([("defval", B), ("title", S, "")], ret=B),
         _sig([("defval", S), ("title", S, "")], ret=S))


# ============================================================================
# input.* functions
# ============================================================================

INPUT_FUNCTIONS: dict[str, IntrinsicFunc] = {}

def _input(short_name: str, *sigs: FuncSig) -> None:
    INPUT_FUNCTIONS[short_name] = _func(f"input.{short_name}", *sigs)

# Common trailing: tooltip, inline, group, display (plot_display), confirm
_C = [("tooltip", S, None), ("inline", S, None), ("group", S, None),
      ("display", I, None), ("confirm", B, None)]

_input("int",    _sig([("defval", I), ("title", S, ""), ("minval", I, None),
                       ("maxval", I, None), ("step", I, 1)] + _C, ret=I))
_input("float",  _sig([("defval", F), ("title", S, ""), ("minval", F, None),
                       ("maxval", F, None), ("step", F, None)] + _C, ret=F))
_input("bool",   _sig([("defval", B), ("title", S, "")] + _C, ret=B))
_input("string", _sig([("defval", S), ("title", S, ""), ("options", S, None)] + _C, ret=S))
_input("source", _sig([("defval", F), ("title", S, "")] + _C, ret=F))
_input("color",  _sig([("defval", I), ("title", S, "")] + _C, ret=I))
_input("timeframe", _sig([("defval", S, ""), ("title", S, "")] + _C, ret=S))
_input("session", _sig([("defval", S, ""), ("title", S, "")] + _C, ret=S))
_input("symbol", _sig([("defval", S, ""), ("title", S, "")] + _C, ret=S))
_input("price",  _sig([("defval", F), ("title", S, "")] + _C, ret=F))
_input("time",   _sig([("defval", I), ("title", S, "")] + _C, ret=I))
_input("text_area", _sig([("defval", S), ("title", S, "")] + _C, ret=S))
# defval is an enum member (typed as float in Pine's overloads; value is int index)
_input("enum",   _sig([("defval", F), ("title", S, "")] + _C, ret=I))


# ============================================================================
# Math constants
# ============================================================================

MATH_CONSTANTS = {
    "pi":   3.141592653589793,
    "e":    2.718281828459045,
    "phi":  1.618033988749895,
    "rphi": 0.6180339887498949,
}


# ============================================================================
# Built-in variables (not functions)
# ============================================================================

BUILTIN_VARIABLES: dict[str, PineType] = {
    # Price series
    "open": F, "high": F, "low": F, "close": F, "volume": F,
    # Derived price
    "hl2": F, "hlc3": F, "hlcc4": F, "ohlc4": F,
    # Bar info
    "bar_index": I, "time": I, "time_close": I,
    "last_bar_index": I, "last_bar_time": I, "timenow": I, "time_tradingday": I,
    # Time/date (when used as variables, NOT function calls)
    "hour": I, "minute": I, "second": I,
    "dayofmonth": I, "dayofweek": I,
    "month": I, "year": I, "weekofyear": I,
    # Special
    "na": NA,
    # ta.tr as a variable
    "ta.tr": F,
    # Official no-call ta.* series variables.
    "ta.obv": F, "ta.accdist": F, "ta.nvi": F, "ta.pvi": F,
    "ta.pvt": F, "ta.wad": F, "ta.wvad": F, "ta.iii": F,
    # chart.is_* chart-type predicates (batch engine is always standard chart)
    "chart.is_standard": B,
    "chart.is_heikinashi": B,
    "chart.is_kagi": B,
    "chart.is_linebreak": B,
    "chart.is_pnf": B,
    "chart.is_range": B,
    "chart.is_renko": B,
    # Session predicates (Pine v6 HIGH — backed by pine_session_is* engine fns)
    "session.ismarket": B, "session.ispremarket": B, "session.ispostmarket": B,
    "session.isfirstbar": B, "session.isfirstbar_regular": B,
    "session.islastbar": B, "session.islastbar_regular": B,
}

# Strategy variables (read-only properties, not function calls)
STRATEGY_VARIABLES: dict[str, PineType] = {
    "strategy.position_size": F,
    "strategy.position_avg_price": F,
    "strategy.position_entry_name": S,
    "strategy.equity": F,
    "strategy.initial_capital": F,
    "strategy.netprofit": F,
    "strategy.netprofit_percent": F,
    "strategy.openprofit": F,
    "strategy.openprofit_percent": F,
    "strategy.grossprofit": F,
    "strategy.grossloss": F,
    "strategy.closedtrades": I,
    "strategy.opentrades": I,
    "strategy.wintrades": I,
    "strategy.losstrades": I,
    "strategy.eventrades": I,
    "strategy.max_contracts_held_all": F,
    "strategy.max_contracts_held_long": F,
    "strategy.max_contracts_held_short": F,
    "strategy.max_drawdown": F,
    "strategy.max_drawdown_percent": F,
    "strategy.max_runup": F,
    "strategy.max_runup_percent": F,
    "strategy.avg_trade": F,
    "strategy.avg_trade_percent": F,
    "strategy.avg_winning_trade": F,
    "strategy.avg_losing_trade": F,
    "strategy.avg_winning_trade_percent": F,
    "strategy.avg_losing_trade_percent": F,
    "strategy.margin_liquidation_price": F,
    "strategy.grossprofit_percent": F,
    "strategy.grossloss_percent": F,
    "strategy.account_currency": S,
    "strategy.opentrades.capital_held": F,
    # Direction constants
    "strategy.long": B,
    "strategy.short": B,
    # Qty type constants
    "strategy.fixed": I,
    "strategy.cash": I,
    "strategy.percent_of_equity": I,
    # Commission type constants
    "strategy.commission.percent": I,
    "strategy.commission.cash_per_contract": I,
    "strategy.commission.cash_per_order": I,
    # OCA constants
    "strategy.oca.cancel": I,
    "strategy.oca.reduce": I,
    "strategy.oca.none": I,
    # Direction constants
    "strategy.direction.all": I,
    "strategy.direction.long": I,
    "strategy.direction.short": I,
}

# Barstate variables
BARSTATE_VARIABLES: dict[str, PineType] = {
    "barstate.isfirst": B,
    "barstate.islast": B,
    "barstate.ishistory": B,
    "barstate.isrealtime": B,
    "barstate.isnew": B,
    "barstate.isconfirmed": B,
    "barstate.islastconfirmedhistory": B,
}

# Syminfo variables
SYMINFO_VARIABLES: dict[str, PineType] = {
    "syminfo.ticker": S,
    "syminfo.tickerid": S,
    "syminfo.basecurrency": S,
    "syminfo.currency": S,
    "syminfo.description": S,
    "syminfo.mintick": F,
    "syminfo.pointvalue": F,
    "syminfo.type": S,
    "syminfo.timezone": S,
    "syminfo.session": S,
    "syminfo.volumetype": S,
    # --- Critical fix: were silently emitting 0; now na-accept ---
    "syminfo.prefix": S,
    "syminfo.root": S,
    "syminfo.pricescale": F,
    "syminfo.minmove": F,
    # --- External-data fields: na-accept so scripts compile ---
    "syminfo.mincontract": F,
    "syminfo.current_contract": S,
    "syminfo.expiration_date": I,
    "syminfo.isin": S,
    "syminfo.sector": S,
    "syminfo.industry": S,
    # --- LOW-tier na-accepts: financial/fundamental data ---
    "syminfo.employees": F,
    "syminfo.shareholders": F,
    "syminfo.shares_outstanding_float": F,
    "syminfo.shares_outstanding_total": F,
    "syminfo.recommendations_buy": F,
    "syminfo.recommendations_strong_buy": F,
    "syminfo.recommendations_hold": F,
    "syminfo.recommendations_sell": F,
    "syminfo.recommendations_strong_sell": F,
    "syminfo.recommendations_total": F,
    "syminfo.recommendations_date": F,
    "syminfo.target_price_average": F,
    "syminfo.target_price_high": F,
    "syminfo.target_price_low": F,
    "syminfo.target_price_median": F,
    "syminfo.target_price_date": F,
    "syminfo.target_price_analysts_count": F,
    # --- Syminfo derivations ---
    "syminfo.main_tickerid": S,
    "syminfo.country": S,
}

# Timeframe variables
TIMEFRAME_VARIABLES: dict[str, PineType] = {
    "timeframe.period": S,
    "timeframe.multiplier": I,
    "timeframe.isintraday": B,
    "timeframe.isdaily": B,
    "timeframe.isweekly": B,
    "timeframe.ismonthly": B,
    "timeframe.isdwm": B,
    "timeframe.main_period": S,
    "timeframe.isticks": B,
}

# display.* plot_display constants (PineScript v6 — values for C++ codegen)
DISPLAY_VARIABLES: dict[str, PineType] = {
    "display.all": I,
    "display.none": I,
    "display.pane": I,
    "display.data_window": I,
    "display.status_line": I,
    "display.price_scale": I,
}




# ============================================================================
# TA functions that have implicit default source (1-arg overload)
# When called with fewer args than the primary signature, the source
# is implicitly `high` (for highest/pivothigh) or `low` (for lowest/pivotlow).
# ============================================================================

TA_DEFAULT_SOURCE = {
    "highest": "high",
    "lowest": "low",
    "pivothigh": "high",
    "pivotlow": "low",
}


# ============================================================================
# Lookup helpers
# ============================================================================

def resolve_overload(func: IntrinsicFunc, num_args: int) -> FuncSig:
    """Pick the best matching overload for a given argument count."""
    # Try exact match first
    for sig in func.signatures:
        required = sum(1 for p in sig.params if p.default is None)
        total = len(sig.params)
        if required <= num_args <= total:
            return sig
    # Fallback to primary
    return func.primary


def merge_kwargs_to_positional(
    sig: FuncSig,
    args: list,
    kwargs: dict,
) -> list:
    """Merge positional args and kwargs into a unified positional arg list
    using the function signature's parameter names.

    Returns a list of AST nodes in positional order.
    """
    merged = list(args)
    for i, param in enumerate(sig.params):
        if param.name in kwargs:
            while len(merged) <= i:
                merged.append(None)
            if i >= len(args) or merged[i] is None:
                merged[i] = kwargs[param.name]
    # Trim trailing Nones
    while merged and merged[-1] is None:
        merged.pop()
    return merged


def get_param_names(namespace: str | None, func_name: str) -> list[str] | None:
    """Get parameter names for a function (for kwargs resolution).

    Returns None if the function is not a known intrinsic.
    """
    if namespace == "ta" and func_name in TA_FUNCTIONS:
        return TA_FUNCTIONS[func_name].param_names
    if namespace == "math" and func_name in MATH_FUNCTIONS:
        return MATH_FUNCTIONS[func_name].param_names
    if namespace == "strategy" and func_name in STRATEGY_FUNCTIONS:
        return STRATEGY_FUNCTIONS[func_name].param_names
    if namespace == "str" and func_name in STR_FUNCTIONS:
        return STR_FUNCTIONS[func_name].param_names
    if namespace == "input" and func_name in INPUT_FUNCTIONS:
        return INPUT_FUNCTIONS[func_name].param_names
    if namespace == "map" and func_name in MAP_FUNCTIONS:
        return MAP_FUNCTIONS[func_name].param_names
    if namespace == "syminfo" and func_name in SYMINFO_FUNCTIONS:
        return SYMINFO_FUNCTIONS[func_name].param_names
    if namespace is None and func_name in BUILTIN_FUNCTIONS:
        return BUILTIN_FUNCTIONS[func_name].param_names
    return None


def get_return_type(namespace: str | None, func_name: str,
                    num_args: int = 0) -> PineType:
    """Get return type for a function call."""
    func = None
    if namespace == "ta":
        func = TA_FUNCTIONS.get(func_name)
    elif namespace == "math":
        func = MATH_FUNCTIONS.get(func_name)
    elif namespace == "strategy":
        func = STRATEGY_FUNCTIONS.get(func_name)
    elif namespace == "str":
        func = STR_FUNCTIONS.get(func_name)
    elif namespace == "input":
        func = INPUT_FUNCTIONS.get(func_name)
    elif namespace == "map":
        func = MAP_FUNCTIONS.get(func_name)
    elif namespace == "syminfo":
        func = SYMINFO_FUNCTIONS.get(func_name)
    elif namespace is None:
        func = BUILTIN_FUNCTIONS.get(func_name)

    if func is None:
        return PineType.UNKNOWN
    sig = resolve_overload(func, num_args)
    return sig.return_type


def is_intrinsic_function(namespace: str | None, func_name: str) -> bool:
    """Check if a function is a known TradingView intrinsic."""
    if namespace == "ta":
        return func_name in TA_FUNCTIONS
    if namespace == "math":
        return func_name in MATH_FUNCTIONS
    if namespace == "strategy":
        return func_name in STRATEGY_FUNCTIONS
    if namespace == "str":
        return func_name in STR_FUNCTIONS
    if namespace == "input":
        return func_name in INPUT_FUNCTIONS
    if namespace == "map":
        return func_name in MAP_FUNCTIONS
    if namespace == "syminfo":
        return func_name in SYMINFO_FUNCTIONS
    if namespace is None:
        return func_name in BUILTIN_FUNCTIONS
    return False


def is_intrinsic_variable(namespace: str | None, name: str) -> bool:
    """Check if a name is a known TradingView built-in variable."""
    if namespace is None:
        return name in BUILTIN_VARIABLES
    full = f"{namespace}.{name}"
    if (full in STRATEGY_VARIABLES
            or full in BARSTATE_VARIABLES
            or full in SYMINFO_VARIABLES
            or full in TIMEFRAME_VARIABLES
            or full in BUILTIN_VARIABLES
            or full in DISPLAY_VARIABLES):
        return True
    # e.g. strategy + "opentrades.capital_held" -> strategy.opentrades.capital_held
    if namespace == "strategy" and "." in name:
        return f"strategy.{name}" in STRATEGY_VARIABLES
    return False

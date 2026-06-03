"""Static lookup tables consumed by the analyzer.

The historic ``analyzer.py`` carried these module-level tables at the
top of the file. Pulling them out into a dedicated module mirrors the
codegen package layout (``codegen/tables.py``) and gives the future
analyzer mixins a single place to import from while letting
``base.py`` stay focused on the ``Analyzer`` class itself.

External consumers (``codegen/base.py``, ``support_checker.py``, and
external tests) import these names through the package facade
(``from pineforge_codegen.analyzer import …``); that contract is
preserved by re-exports in ``analyzer/__init__.py``.

Naming overlap note: ``BAR_FIELDS`` here is a ``set[str]`` of Pine
identifiers treated as series-of-bar fields by the *analyzer*. The
codegen package owns a same-named ``BAR_FIELDS`` ``dict[str, str]``
that maps those identifiers to C++ accessors. The two never share an
import path (each lives in its own ``tables.py``), so the name reuse
is a deliberate, parallel-package convention rather than a collision.
"""

from __future__ import annotations

from ..symbols import PineType


# ---------------------------------------------------------------------------
# TA mapping tables
# ---------------------------------------------------------------------------

TA_CLASS_MAP = {
    "sma": "ta::SMA",
    "ema": "ta::EMA",
    "rma": "ta::RMA",
    "rsi": "ta::RSI",
    "atr": "ta::ATR",
    "tr":  "ta::TR",
    "macd": "ta::MACD",
    "stoch": "ta::Stoch",
    "crossover": "ta::Crossover",
    "crossunder": "ta::Crossunder",
    "cross": "ta::Cross",
    "highest": "ta::Highest",
    "lowest": "ta::Lowest",
    "change": "ta::Change",
    "supertrend": "ta::Supertrend",
    "dmi": "ta::DMI",
    "sar": "ta::SAR",
    "bb": "ta::BB",
    "kc": "ta::KC",
    "wma": "ta::WMA",
    "hma": "ta::HMA",
    "stdev": "ta::StdDev",
    "pivothigh": "ta::PivotHigh",
    "pivotlow": "ta::PivotLow",
    # Task 6
    "sum": "math::Sum",
    # Task 7 Batch 1
    "linreg": "ta::Linreg",
    "percentrank": "ta::PercentRank",
    "vwma": "ta::VWMA",
    # Task 7 Batch 2
    "mom": "ta::Mom",
    "roc": "ta::ROC",
    "rising": "ta::Rising",
    "falling": "ta::Falling",
    "cci": "ta::CCI",
    "cum": "ta::Cum",
    # Task 7 Batch 3
    "variance": "ta::Variance",
    "median": "ta::Median",
    "highestbars": "ta::HighestBars",
    "lowestbars": "ta::LowestBars",
    # Batch 4 — remaining TA functions
    "alma": "ta::ALMA",
    "swma": "ta::SWMA",
    "mfi": "ta::MFI",
    "cmo": "ta::CMO",
    "tsi": "ta::TSI",
    "wpr": "ta::WPR",
    "cog": "ta::COG",
    "bbw": "ta::BBW",
    "kcw": "ta::KCW",
    "barssince": "ta::BarsSince",
    "valuewhen": "ta::ValueWhen",
    "correlation": "ta::Correlation",
    "percentile_nearest_rank": "ta::PercentileNearestRank",
    "percentile_linear_interpolation": "ta::PercentileLinearInterpolation",
    # Task 5 — Volume indicators + statistical
    "vwap": "ta::VWAP",
    # 3-arg form: ta.vwap(source, anchor, stdev_mult) → tuple [vwap, upper, lower]
    "vwap_bands": "ta::VWAPBands",
    "obv": "ta::OBV",
    "accdist": "ta::AccDist",
    "nvi": "ta::NVI",
    "pvi": "ta::PVI",
    "pvt": "ta::PVT",
    "wad": "ta::WAD",
    "wvad": "ta::WVAD",
    "iii": "ta::III",
    "mode": "ta::Mode",
    "range": "ta::Range",
    "dev": "ta::Dev",
    "max": "ta::AllTimeMax",
    "min": "ta::AllTimeMin",
    "rci": "ta::RCI",
}

# Which arg index is the period/length (goes to constructor)
TA_PERIOD_ARG = {
    "sma": 1, "ema": 1, "rma": 1, "rsi": 1, "atr": 0,
    "highest": 1, "lowest": 1, "change": 1,
    "wma": 1, "hma": 1, "stdev": 1,
    # Task 6
    "sum": 1,
    # Task 7 Batch 1
    "linreg": 1, "percentrank": 1, "vwma": 1,
    # Task 7 Batch 2
    "mom": 1, "roc": 1, "rising": 1, "falling": 1, "cci": 1,
    # cum has no period arg — handled in TA_NO_CTOR
    # Task 7 Batch 3
    "variance": 1, "median": 1, "highestbars": 1, "lowestbars": 1,
    # Batch 4
    "cmo": 1, "cog": 1, "correlation": 2,
    "percentile_nearest_rank": 1, "percentile_linear_interpolation": 1,
    # Task 5
    "mode": 1, "range": 1, "dev": 1,
    "rci": 1,
}

# Functions that return tuples
TA_TUPLE_RETURNS = {"macd", "supertrend", "dmi", "bb", "kc", "vwap_bands"}

# Functions with multiple constructor args
TA_MULTI_CTOR = {
    "macd": [1, 2, 3],      # fast, slow, signal
    "stoch": [3],            # length (high, low are compute args)
    "supertrend": [0, 1],    # factor, atr_period
    "dmi": [0, 1],           # di_length, adx_smoothing
    "bb": [1, 2],            # length, mult
    "kc": [1, 2],            # length, mult
    "sar": [0, 1, 2],        # start, increment, maximum
    "pivothigh": [0, 1],     # left_bars, right_bars
    "pivotlow": [0, 1],      # left_bars, right_bars
    # Batch 4
    "alma": [1, 2, 3],      # length, offset, sigma
    "mfi": [1],              # length (src is compute arg, vol implicit)
    # Pine signature: ta.tsi(source, short_length, long_length) — positions
    # 1 and 2 carry the lengths that initialize the four nested EMAs;
    # source flows to compute() at position 0. The previous `[0, 1]` was
    # copy-paste off-by-one (caught by a TV-anchored sweep that found
    # engine TSI returning 0.0 for every bar because compute() was being
    # called with the long_length integer literal as the "source").
    "tsi": [1, 2],           # short_length, long_length
    "wpr": [0],              # length
    "bbw": [1, 2],           # length, mult
    "kcw": [1, 2],           # length, mult
    "tr":  [0],              # handle_na (compile-time bool)
}

# No-state functions (no constructor args, stateless or self-contained)
TA_NO_CTOR = {"crossover", "crossunder", "cross", "cum", "swma", "barssince", "valuewhen",
              "max", "min",
              "obv", "accdist", "nvi", "pvi", "pvt", "wad", "wvad", "iii", "vwap"}

# TA parameter names are now in signatures.py (sigs.get_param_names)


# ---------------------------------------------------------------------------
# Built-in variables
# ---------------------------------------------------------------------------

BUILTIN_VARS = {
    # Price
    "open": PineType.FLOAT, "high": PineType.FLOAT, "low": PineType.FLOAT,
    "close": PineType.FLOAT, "volume": PineType.FLOAT,
    # Derived price
    "hl2": PineType.FLOAT, "hlc3": PineType.FLOAT, "hlcc4": PineType.FLOAT,
    "ohlc4": PineType.FLOAT,
    # Bar info
    "bar_index": PineType.INT, "time": PineType.INT, "time_close": PineType.INT,
    "last_bar_index": PineType.INT, "last_bar_time": PineType.INT, "timenow": PineType.INT,
    "time_tradingday": PineType.INT,
    # Time & date
    "hour": PineType.INT, "minute": PineType.INT, "second": PineType.INT,
    "dayofmonth": PineType.INT, "dayofweek": PineType.INT,
    "month": PineType.INT, "year": PineType.INT, "weekofyear": PineType.INT,
    # Special
    "na": PineType.NA,
}

# Bar-related price fields (these are series that map to bar.* in C++)
BAR_FIELDS = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"}


# ---------------------------------------------------------------------------
# Skip functions (visual -- parse but don't generate code)
# ---------------------------------------------------------------------------

SKIP_FUNCS = {
    "plot", "plotshape", "plotchar", "plotcandle", "fill", "hline",
    "bgcolor", "barcolor", "alert", "alertcondition",
    "max_bars_back",
}

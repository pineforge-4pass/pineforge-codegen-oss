"""Pine Script v6 TA surface checks against the user-pinescript-docs MCP.

The MCP-reported surface has 59 `ta.*` functions. Several legacy volume
indicators are official `ta.*` variables instead, so they must compile only
without parentheses.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen import signatures as sigs
from pineforge_codegen.support_checker import SUPPORTED_TA, TA_PROPERTY_VARIABLES


OFFICIAL_TA_FUNCTIONS = {
    "alma", "atr", "barssince", "bb", "bbw", "cci", "change", "cmo",
    "cog", "correlation", "cross", "crossover", "crossunder", "cum",
    "dev", "dmi", "ema", "falling", "highest", "highestbars", "hma",
    "kc", "kcw", "linreg", "lowest", "lowestbars", "macd", "max",
    "median", "mfi", "min", "mode", "mom", "percentile_linear_interpolation",
    "percentile_nearest_rank", "percentrank", "pivot_point_levels",
    "pivothigh", "pivotlow", "range", "rci", "rising", "rma", "roc",
    "rsi", "sar", "sma", "stdev", "stoch", "supertrend", "swma",
    "tr", "tsi", "valuewhen", "variance", "vwap", "vwma", "wma", "wpr",
}

OFFICIAL_TA_VARIABLES = {
    "obv", "accdist", "nvi", "pvi", "pvt", "wad", "wvad", "iii",
}


TA_SMOKE_CASES = {
    "alma": ("x = ta.alma(close, 5, 0.85, 6.0)", "ta::ALMA"),
    "atr": ("x = ta.atr(14)", "ta::ATR"),
    "barssince": ("x = ta.barssince(close > open)", "ta::BarsSince"),
    "bb": ("[m, u, l] = ta.bb(close, 5, 2.0)", "ta::BB"),
    "bbw": ("x = ta.bbw(close, 5, 2.0)", "ta::BBW"),
    "cci": ("x = ta.cci(close, 5)", "ta::CCI"),
    "change": ("x = ta.change(close)", "ta::Change"),
    "cmo": ("x = ta.cmo(close, 5)", "ta::CMO"),
    "cog": ("x = ta.cog(close, 5)", "ta::COG"),
    "correlation": ("x = ta.correlation(close, open, 5)", "ta::Correlation"),
    "cross": ("x = ta.cross(close, open)", "ta::Cross"),
    "crossover": ("x = ta.crossover(close, open)", "ta::Crossover"),
    "crossunder": ("x = ta.crossunder(close, open)", "ta::Crossunder"),
    "cum": ("x = ta.cum(close)", "ta::Cum"),
    "dev": ("x = ta.dev(close, 5)", "ta::Dev"),
    "dmi": ("[p, n, a] = ta.dmi(14, 14)", "ta::DMI"),
    "ema": ("x = ta.ema(close, 5)", "ta::EMA"),
    "falling": ("x = ta.falling(close, 3)", "ta::Falling"),
    "highest": ("x = ta.highest(high, 3)", "ta::Highest"),
    "highestbars": ("x = ta.highestbars(high, 3)", "ta::HighestBars"),
    "hma": ("x = ta.hma(close, 5)", "ta::HMA"),
    "kc": ("[m, u, l] = ta.kc(close, 5, 1.5)", "ta::KC"),
    "kcw": ("x = ta.kcw(close, 5, 1.5)", "ta::KCW"),
    "linreg": ("x = ta.linreg(close, 5, 0)", "ta::Linreg"),
    "lowest": ("x = ta.lowest(low, 3)", "ta::Lowest"),
    "lowestbars": ("x = ta.lowestbars(low, 3)", "ta::LowestBars"),
    "macd": ("[m, s, h] = ta.macd(close, 12, 26, 9)", "ta::MACD"),
    "max": ("x = ta.max(close)", "ta::AllTimeMax"),
    "median": ("x = ta.median(close, 5)", "ta::Median"),
    "mfi": ("x = ta.mfi(close, 5)", "ta::MFI"),
    "min": ("x = ta.min(close)", "ta::AllTimeMin"),
    "mode": ("x = ta.mode(close, 5)", "ta::Mode"),
    "mom": ("x = ta.mom(close, 1)", "ta::Mom"),
    "percentile_linear_interpolation": (
        "x = ta.percentile_linear_interpolation(close, 5, 50)",
        "ta::PercentileLinearInterpolation",
    ),
    "percentile_nearest_rank": (
        "x = ta.percentile_nearest_rank(close, 5, 50)",
        "ta::PercentileNearestRank",
    ),
    "percentrank": ("x = ta.percentrank(close, 5)", "ta::PercentRank"),
    "pivot_point_levels": (
        'levels = ta.pivot_point_levels("Traditional", true)',
        "ta::pivot_point_levels",
    ),
    "pivothigh": ("x = ta.pivothigh(2, 2)", "ta::PivotHigh"),
    "pivotlow": ("x = ta.pivotlow(2, 2)", "ta::PivotLow"),
    "range": ("x = ta.range(close, 5)", "ta::Range"),
    "rci": ("x = ta.rci(close, 5)", "ta::RCI"),
    "rising": ("x = ta.rising(close, 3)", "ta::Rising"),
    "rma": ("x = ta.rma(close, 5)", "ta::RMA"),
    "roc": ("x = ta.roc(close, 1)", "ta::ROC"),
    "rsi": ("x = ta.rsi(close, 14)", "ta::RSI"),
    "sar": ("x = ta.sar(0.02, 0.02, 0.2)", "ta::SAR"),
    "sma": ("x = ta.sma(close, 5)", "ta::SMA"),
    "stdev": ("x = ta.stdev(close, 5)", "ta::StdDev"),
    "stoch": ("x = ta.stoch(close, high, low, 14)", "ta::Stoch"),
    "supertrend": ("[s, d] = ta.supertrend(3.0, 10)", "ta::Supertrend"),
    "swma": ("x = ta.swma(close)", "ta::SWMA"),
    "tr": ("x = ta.tr(true)", "ta::TR"),
    "tsi": ("x = ta.tsi(close, 13, 25)", "ta::TSI"),
    "valuewhen": ("x = ta.valuewhen(close > open, close, 0)", "ta::ValueWhen"),
    "variance": ("x = ta.variance(close, 5)", "ta::Variance"),
    "vwap": ("x = ta.vwap(close)", "ta::VWAP"),
    "vwma": ("x = ta.vwma(close, 5)", "ta::VWMA"),
    "wma": ("x = ta.wma(close, 5)", "ta::WMA"),
    "wpr": ("x = ta.wpr(14)", "ta::WPR"),
}


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\n'


def test_supported_ta_matches_official_function_surface():
    assert SUPPORTED_TA == OFFICIAL_TA_FUNCTIONS
    assert set(sigs.TA_FUNCTIONS) == OFFICIAL_TA_FUNCTIONS


def test_property_variables_match_official_non_function_surface():
    assert TA_PROPERTY_VARIABLES == OFFICIAL_TA_VARIABLES
    for name in OFFICIAL_TA_VARIABLES:
        assert f"ta.{name}" in sigs.BUILTIN_VARIABLES
        assert sigs.is_intrinsic_variable("ta", name)
        assert name not in sigs.TA_FUNCTIONS
    assert sigs.is_intrinsic_variable("ta", "tr")


@pytest.mark.parametrize("name", sorted(OFFICIAL_TA_FUNCTIONS))
def test_every_official_ta_function_has_codegen_smoke(name):
    body, expected = TA_SMOKE_CASES[name]
    cpp = transpile(_pine(body))
    assert expected in cpp


@pytest.mark.parametrize("name", sorted(OFFICIAL_TA_VARIABLES))
def test_official_ta_property_variables_compile_without_parentheses(name):
    cpp = transpile(_pine(f"x = ta.{name}"))
    assert f"ta::{ {'obv': 'OBV', 'accdist': 'AccDist', 'nvi': 'NVI', 'pvi': 'PVI', 'pvt': 'PVT', 'wad': 'WAD', 'wvad': 'WVAD', 'iii': 'III'}[name] }" in cpp


@pytest.mark.parametrize("name", sorted(OFFICIAL_TA_VARIABLES))
def test_official_ta_property_variables_reject_function_call_form(name):
    with pytest.raises(CompileError, match="variable, not a function"):
        transpile(_pine(f"x = ta.{name}()"))


def test_pivot_point_levels_global_initializes_as_vector():
    # Pine v6 semantics: with `developing=false` (default), the pivot is
    # computed from the LAST CLOSED period's HLC. Codegen lowers this to
    # `_s_high[1]/_s_low[1]/_s_close[1]` (previous bar) — passing the
    # current bar's HLC produced TV-shifted-by-one-bar values across all
    # 11 levels.
    cpp = transpile(_pine('levels = ta.pivot_point_levels("Traditional", true)\nx = levels[0]'))
    assert "std::vector<double> levels;" in cpp
    assert 'ta::pivot_point_levels(std::string("Traditional"), _s_high[1], _s_low[1], _s_close[1])' in cpp
    # Series members must be auto-declared and pushed at the top of on_bar
    assert "Series<double> _s_high;" in cpp
    assert "Series<double> _s_low;" in cpp
    assert "Series<double> _s_close;" in cpp
    assert "x = levels[0];" in cpp


def test_pivot_point_levels_developing_and_kwargs_use_runtime_ohlc_shape():
    positional = transpile(_pine('levels = ta.pivot_point_levels("Traditional", true, false)'))
    keyword = transpile(_pine('levels = ta.pivot_point_levels(type="Traditional", anchor=true, developing=false)'))
    expected = 'ta::pivot_point_levels(std::string("Traditional"), _s_high[1], _s_low[1], _s_close[1])'
    assert expected in positional
    assert expected in keyword

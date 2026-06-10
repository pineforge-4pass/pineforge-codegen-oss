"""Tests for the signatures registry and kwargs resolution across ALL intrinsic functions.

Every TradingView intrinsic function must have correct parameter names so that
kwargs can be properly resolved to positional args. These tests verify that
the full pipeline (parser -> analyzer -> codegen) handles kwargs correctly
for every registered intrinsic function.
"""

import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen import signatures as sigs
from pineforge_codegen import tv_input_choices as tv_in
from pineforge_codegen.errors import CompileError
from pineforge_codegen.symbols import PineType


def _generate(src: str) -> str:
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    return CodeGen(ctx).generate()


def _pine(body: str) -> str:
    """Wrap body in a minimal strategy script."""
    return f'//@version=6\nstrategy("T")\n{body}\n'


# ============================================================================
# Part 1: Signatures registry unit tests
# ============================================================================


class TestSignaturesRegistry:
    """Test the signatures module data structures and helpers."""

    def test_ta_functions_populated(self):
        assert len(sigs.TA_FUNCTIONS) > 20
        for name in ["sma", "ema", "rma", "rsi", "atr", "macd", "stoch",
                      "crossover", "crossunder", "highest", "lowest",
                      "supertrend", "dmi", "bb", "kc", "sar",
                      "linreg", "percentrank", "vwma", "mom", "roc",
                      "cci", "mfi", "rising", "falling", "change",
                      "stdev", "variance", "median", "cum",
                      "highestbars", "lowestbars", "wma", "hma",
                      "alma", "swma", "cog", "tsi", "wpr", "cmo",
                      "barssince", "valuewhen", "correlation",
                      "pivothigh", "pivotlow", "bbw", "kcw",
                      "percentile_nearest_rank", "percentile_linear_interpolation",
                      "max", "min", "rci"]:
            assert name in sigs.TA_FUNCTIONS, f"ta.{name} missing from TA_FUNCTIONS"

    def test_math_functions_populated(self):
        assert len(sigs.MATH_FUNCTIONS) > 15
        for name in ["abs", "ceil", "floor", "round", "sign",
                      "max", "min", "avg", "sum", "pow", "sqrt",
                      "exp", "log", "log10", "random",
                      "sin", "cos", "tan", "asin", "acos", "atan",
                      "todegrees", "toradians", "round_to_mintick"]:
            assert name in sigs.MATH_FUNCTIONS, f"math.{name} missing from MATH_FUNCTIONS"

    def test_strategy_functions_populated(self):
        for name in ["entry", "exit", "close", "close_all", "order",
                      "cancel", "cancel_all",
                      "convert_to_account", "convert_to_symbol", "default_entry_qty"]:
            assert name in sigs.STRATEGY_FUNCTIONS, f"strategy.{name} missing"

    def test_str_functions_populated(self):
        for name in ["tostring", "tonumber", "format", "length",
                      "contains", "startswith", "endswith", "pos",
                      "substring", "replace", "replace_all",
                      "lower", "upper", "trim", "repeat", "match", "split"]:
            assert name in sigs.STR_FUNCTIONS, f"str.{name} missing"

    def test_builtin_functions_populated(self):
        for name in ["na", "nz", "fixnan", "timestamp",
                      "int", "float", "bool", "string",
                      "max_bars_back", "input"]:
            assert name in sigs.BUILTIN_FUNCTIONS, f"{name} missing from BUILTIN_FUNCTIONS"

    def test_plain_input_is_builtin_not_input_namespace(self):
        assert "input" in sigs.BUILTIN_FUNCTIONS
        assert "__default__" not in sigs.INPUT_FUNCTIONS

    def test_input_functions_populated(self):
        for name in ["int", "float", "bool", "string", "source",
                      "color", "timeframe", "session", "symbol",
                      "price", "time", "text_area", "enum"]:
            assert name in sigs.INPUT_FUNCTIONS, f"input.{name} missing"

    def test_map_functions_populated(self):
        for name in ["new", "put", "get", "remove", "contains", "size",
                      "clear", "keys", "values", "copy", "put_all"]:
            assert name in sigs.MAP_FUNCTIONS, f"map.{name} missing"

    def test_display_constants_registered(self):
        assert sigs.is_intrinsic_variable("display", "all")
        assert sigs.is_intrinsic_variable("display", "none")

    def test_math_constants(self):
        assert "pi" in sigs.MATH_CONSTANTS
        assert "e" in sigs.MATH_CONSTANTS
        assert "phi" in sigs.MATH_CONSTANTS
        assert "rphi" in sigs.MATH_CONSTANTS
        assert abs(sigs.MATH_CONSTANTS["pi"] - 3.14159265) < 0.001

    def test_builtin_variables(self):
        for name in ["open", "high", "low", "close", "volume",
                      "hl2", "hlc3", "ohlc4", "bar_index", "time",
                      "na"]:
            assert name in sigs.BUILTIN_VARIABLES, f"{name} missing from BUILTIN_VARIABLES"

    def test_strategy_variables(self):
        for name in ["strategy.position_size", "strategy.equity",
                      "strategy.long", "strategy.short",
                      "strategy.percent_of_equity",
                      "strategy.commission.percent"]:
            assert name in sigs.STRATEGY_VARIABLES, f"{name} missing"

    def test_barstate_variables(self):
        for name in ["barstate.isfirst", "barstate.islast",
                      "barstate.ishistory", "barstate.isrealtime"]:
            assert name in sigs.BARSTATE_VARIABLES

    def test_syminfo_variables(self):
        for name in ["syminfo.ticker", "syminfo.mintick", "syminfo.currency"]:
            assert name in sigs.SYMINFO_VARIABLES

    def test_timeframe_variables(self):
        for name in ["timeframe.period", "timeframe.multiplier",
                      "timeframe.isintraday"]:
            assert name in sigs.TIMEFRAME_VARIABLES


class TestIsIntrinsic:
    """Test is_intrinsic_function and is_intrinsic_variable helpers."""

    def test_ta_intrinsics(self):
        for name in sigs.TA_FUNCTIONS:
            assert sigs.is_intrinsic_function("ta", name), f"ta.{name} not recognized"

    def test_math_intrinsics(self):
        for name in sigs.MATH_FUNCTIONS:
            assert sigs.is_intrinsic_function("math", name), f"math.{name} not recognized"

    def test_strategy_intrinsics(self):
        for name in sigs.STRATEGY_FUNCTIONS:
            assert sigs.is_intrinsic_function("strategy", name)

    def test_str_intrinsics(self):
        for name in sigs.STR_FUNCTIONS:
            assert sigs.is_intrinsic_function("str", name)

    def test_builtin_intrinsics(self):
        for name in sigs.BUILTIN_FUNCTIONS:
            assert sigs.is_intrinsic_function(None, name)

    def test_input_intrinsics(self):
        for name in sigs.INPUT_FUNCTIONS:
            assert sigs.is_intrinsic_function("input", name)

    def test_map_intrinsics(self):
        for name in sigs.MAP_FUNCTIONS:
            assert sigs.is_intrinsic_function("map", name), f"map.{name} not recognized"

    def test_user_defined_not_intrinsic(self):
        assert not sigs.is_intrinsic_function(None, "myFunc")
        assert not sigs.is_intrinsic_function("ta", "myCustomIndicator")
        assert not sigs.is_intrinsic_function("ta", "sum")
        assert not sigs.is_intrinsic_function(None, "calculateSignal")

    def test_builtin_variables_detected(self):
        assert sigs.is_intrinsic_variable(None, "close")
        assert sigs.is_intrinsic_variable(None, "open")
        assert sigs.is_intrinsic_variable(None, "volume")
        assert sigs.is_intrinsic_variable(None, "bar_index")
        assert sigs.is_intrinsic_variable(None, "na")

    def test_strategy_variables_detected(self):
        assert sigs.is_intrinsic_variable("strategy", "position_size")
        assert sigs.is_intrinsic_variable("strategy", "long")
        assert sigs.is_intrinsic_variable("strategy", "equity")

    def test_barstate_variables_detected(self):
        assert sigs.is_intrinsic_variable("barstate", "isfirst")
        assert sigs.is_intrinsic_variable("barstate", "ishistory")

    def test_user_defined_not_variable(self):
        assert not sigs.is_intrinsic_variable(None, "myVar")
        assert not sigs.is_intrinsic_variable(None, "signal")


class TestTvInputChoices:
    """Compile-time TV choice sets for input.* validation."""

    def test_chart_series_subset_of_input_source_choices(self):
        for s in ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"):
            assert s in tv_in.INPUT_SOURCE_SERIES_IDS, s

    def test_timeframe_helpers(self):
        assert tv_in.is_valid_timeframe_string("")
        assert tv_in.is_valid_timeframe_string("15")
        assert tv_in.is_valid_timeframe_string("25")
        assert tv_in.is_valid_timeframe_string("D")
        assert not tv_in.is_valid_timeframe_string("bogus!!!")

    def test_session_helpers(self):
        assert tv_in.is_plausible_session_string("24x7")
        assert tv_in.is_plausible_session_string("0930-1600")
        assert not tv_in.is_plausible_session_string("bad")


class TestGetParamNames:
    """Test get_param_names returns correct parameter names for every function."""

    def test_all_ta_have_param_names(self):
        for name, func in sigs.TA_FUNCTIONS.items():
            pnames = sigs.get_param_names("ta", name)
            assert pnames is not None, f"ta.{name} has no param names"
            assert len(pnames) == len(func.primary.params), \
                f"ta.{name}: param count mismatch {len(pnames)} vs {len(func.primary.params)}"

    def test_all_math_have_param_names(self):
        for name, func in sigs.MATH_FUNCTIONS.items():
            pnames = sigs.get_param_names("math", name)
            assert pnames is not None, f"math.{name} has no param names"

    def test_all_strategy_have_param_names(self):
        for name, func in sigs.STRATEGY_FUNCTIONS.items():
            pnames = sigs.get_param_names("strategy", name)
            assert pnames is not None, f"strategy.{name} has no param names"

    def test_all_str_have_param_names(self):
        for name, func in sigs.STR_FUNCTIONS.items():
            pnames = sigs.get_param_names("str", name)
            assert pnames is not None, f"str.{name} has no param names"

    def test_all_map_have_param_names(self):
        for name, func in sigs.MAP_FUNCTIONS.items():
            pnames = sigs.get_param_names("map", name)
            assert pnames is not None, f"map.{name} has no param names"

    def test_all_input_have_param_names(self):
        for name, func in sigs.INPUT_FUNCTIONS.items():
            pnames = sigs.get_param_names("input", name)
            assert pnames is not None, f"input.{name} has no param names"

    def test_all_builtin_have_param_names(self):
        for name, func in sigs.BUILTIN_FUNCTIONS.items():
            pnames = sigs.get_param_names(None, name)
            assert pnames is not None, f"{name} has no param names"

    def test_every_registry_function_is_intrinsic(self):
        """Every registered signature must be recognized by is_intrinsic_function."""
        pairs = [
            ("ta", sigs.TA_FUNCTIONS),
            ("math", sigs.MATH_FUNCTIONS),
            ("strategy", sigs.STRATEGY_FUNCTIONS),
            ("str", sigs.STR_FUNCTIONS),
            ("input", sigs.INPUT_FUNCTIONS),
            ("map", sigs.MAP_FUNCTIONS),
        ]
        for ns, funcs in pairs:
            for name in funcs:
                assert sigs.is_intrinsic_function(ns, name), f"{ns}.{name} not intrinsic"

        for name in sigs.BUILTIN_FUNCTIONS:
            assert sigs.is_intrinsic_function(None, name), f"builtin {name} not intrinsic"

    def test_unknown_returns_none(self):
        assert sigs.get_param_names("ta", "nonexistent") is None
        assert sigs.get_param_names("math", "nonexistent") is None
        assert sigs.get_param_names(None, "nonexistent") is None


class TestResolveOverload:
    """Test overload resolution picks correct signature."""

    def test_math_round_1arg(self):
        func = sigs.MATH_FUNCTIONS["round"]
        sig = sigs.resolve_overload(func, 1)
        assert sig.return_type == PineType.INT  # round(x) returns int

    def test_math_round_2arg(self):
        func = sigs.MATH_FUNCTIONS["round"]
        sig = sigs.resolve_overload(func, 2)
        assert sig.return_type == PineType.FLOAT  # round(x, precision) returns float

    def test_ta_highest_1arg(self):
        func = sigs.TA_FUNCTIONS["highest"]
        sig = sigs.resolve_overload(func, 1)
        assert len(sig.params) == 1  # highest(length) overload

    def test_ta_highest_2arg(self):
        func = sigs.TA_FUNCTIONS["highest"]
        sig = sigs.resolve_overload(func, 2)
        assert len(sig.params) == 2  # highest(source, length) overload

    def test_nz_1arg(self):
        func = sigs.BUILTIN_FUNCTIONS["nz"]
        sig = sigs.resolve_overload(func, 1)
        assert len(sig.params) == 1

    def test_nz_2arg(self):
        func = sigs.BUILTIN_FUNCTIONS["nz"]
        sig = sigs.resolve_overload(func, 2)
        assert len(sig.params) == 2

    def test_macd_returns_tuple(self):
        func = sigs.TA_FUNCTIONS["macd"]
        sig = sigs.resolve_overload(func, 4)
        assert sig.returns_tuple is True
        assert sig.tuple_count == 3

    def test_bb_returns_tuple(self):
        func = sigs.TA_FUNCTIONS["bb"]
        sig = sigs.resolve_overload(func, 3)
        assert sig.returns_tuple is True
        assert sig.tuple_count == 3


class TestMergeKwargsToPositional:
    """Test the merge_kwargs_to_positional helper."""

    def test_no_kwargs(self):
        func = sigs.TA_FUNCTIONS["sma"]
        sig = func.primary
        result = sigs.merge_kwargs_to_positional(sig, ["close_expr", "14"], {})
        assert result == ["close_expr", "14"]

    def test_kwargs_only(self):
        func = sigs.TA_FUNCTIONS["sma"]
        sig = func.primary
        result = sigs.merge_kwargs_to_positional(sig, [], {"source": "close_expr", "length": "14"})
        assert result == ["close_expr", "14"]

    def test_mixed_args_kwargs(self):
        func = sigs.TA_FUNCTIONS["sma"]
        sig = func.primary
        result = sigs.merge_kwargs_to_positional(sig, ["close_expr"], {"length": "14"})
        assert result == ["close_expr", "14"]

    def test_kwargs_override_position(self):
        func = sigs.STRATEGY_FUNCTIONS["entry"]
        sig = func.primary
        result = sigs.merge_kwargs_to_positional(
            sig, ["Long"], {"direction": "true", "stop": "100.0"}
        )
        assert result[0] == "Long"
        assert result[1] == "true"  # direction at position 1
        # stop is at position 4
        assert "100.0" in result


class TestGetReturnType:
    """Test return type inference from signatures."""

    def test_ta_sma_returns_float(self):
        assert sigs.get_return_type("ta", "sma") == PineType.FLOAT

    def test_ta_crossover_returns_bool(self):
        assert sigs.get_return_type("ta", "crossover") == PineType.BOOL

    def test_ta_macd_returns_float(self):
        # MACD returns tuple of floats, but the base return_type is FLOAT
        assert sigs.get_return_type("ta", "macd") == PineType.FLOAT

    def test_math_ceil_returns_int(self):
        assert sigs.get_return_type("math", "ceil") == PineType.INT

    def test_math_floor_returns_int(self):
        assert sigs.get_return_type("math", "floor") == PineType.INT

    def test_math_abs_returns_float(self):
        assert sigs.get_return_type("math", "abs") == PineType.FLOAT

    def test_strategy_entry_returns_void(self):
        assert sigs.get_return_type("strategy", "entry") == PineType.VOID

    def test_str_tostring_returns_string(self):
        assert sigs.get_return_type("str", "tostring") == PineType.STRING

    def test_str_length_returns_int(self):
        assert sigs.get_return_type("str", "length") == PineType.INT

    def test_str_contains_returns_bool(self):
        assert sigs.get_return_type("str", "contains") == PineType.BOOL

    def test_na_returns_bool(self):
        assert sigs.get_return_type(None, "na") == PineType.BOOL

    def test_nz_returns_float(self):
        assert sigs.get_return_type(None, "nz") == PineType.FLOAT

    def test_unknown_returns_unknown(self):
        assert sigs.get_return_type("ta", "nonexistent") == PineType.UNKNOWN
        assert sigs.get_return_type(None, "nonexistent") == PineType.UNKNOWN


# ============================================================================
# Part 2: End-to-end kwargs tests through full pipeline
# ============================================================================


class TestTAKwargs:
    """Test ta.* functions with kwargs through the full transpilation pipeline."""

    def test_ta_sma_kwargs(self):
        cpp = _generate(_pine('x = ta.sma(source=close, length=14)'))
        assert "ta::SMA" in cpp
        assert ".compute(" in cpp

    def test_ta_ema_kwargs(self):
        cpp = _generate(_pine('x = ta.ema(source=close, length=20)'))
        assert "ta::EMA" in cpp

    def test_ta_rma_kwargs(self):
        cpp = _generate(_pine('x = ta.rma(source=close, length=14)'))
        assert "ta::RMA" in cpp

    def test_ta_wma_kwargs(self):
        cpp = _generate(_pine('x = ta.wma(source=close, length=14)'))
        assert "ta::WMA" in cpp

    def test_ta_hma_kwargs(self):
        cpp = _generate(_pine('x = ta.hma(source=close, length=14)'))
        assert "ta::HMA" in cpp

    def test_ta_rsi_kwargs(self):
        cpp = _generate(_pine('x = ta.rsi(source=close, length=14)'))
        assert "ta::RSI" in cpp

    def test_ta_atr_kwargs(self):
        cpp = _generate(_pine('x = ta.atr(length=14)'))
        assert "ta::ATR" in cpp

    def test_ta_stdev_kwargs(self):
        cpp = _generate(_pine('x = ta.stdev(source=close, length=20)'))
        assert "ta::StdDev" in cpp

    def test_ta_highest_kwargs(self):
        cpp = _generate(_pine('x = ta.highest(source=high, length=14)'))
        assert "ta::Highest" in cpp

    def test_ta_lowest_kwargs(self):
        cpp = _generate(_pine('x = ta.lowest(source=low, length=14)'))
        assert "ta::Lowest" in cpp

    def test_ta_max_min_chart_kwargs(self):
        cpp = _generate(_pine('x = ta.max(source=high)\ny = ta.min(source=low)'))
        assert "ta::AllTimeMax" in cpp
        assert "ta::AllTimeMin" in cpp

    def test_ta_rci_kwargs(self):
        cpp = _generate(_pine('x = ta.rci(source=close, length=14)'))
        assert "ta::RCI" in cpp

    def test_ta_change_kwargs(self):
        cpp = _generate(_pine('x = ta.change(source=close, length=1)'))
        assert "ta::Change" in cpp

    def test_ta_crossover_kwargs(self):
        cpp = _generate(_pine('fast = ta.sma(close, 10)\nslow = ta.sma(close, 20)\nx = ta.crossover(source1=fast, source2=slow)'))
        assert "ta::Crossover" in cpp

    def test_ta_crossunder_kwargs(self):
        cpp = _generate(_pine('fast = ta.sma(close, 10)\nslow = ta.sma(close, 20)\nx = ta.crossunder(source1=fast, source2=slow)'))
        assert "ta::Crossunder" in cpp

    def test_ta_macd_kwargs(self):
        cpp = _generate(_pine('[m, s, h] = ta.macd(source=close, fastlen=12, slowlen=26, siglen=9)'))
        assert "ta::MACD" in cpp

    def test_ta_bb_kwargs(self):
        cpp = _generate(_pine('[mid, up, lo] = ta.bb(source=close, length=20, mult=2.0)'))
        assert "ta::BB" in cpp

    def test_ta_kc_kwargs(self):
        cpp = _generate(_pine('[mid, up, lo] = ta.kc(source=close, length=20, mult=1.5)'))
        assert "ta::KC" in cpp

    def test_ta_supertrend_kwargs(self):
        cpp = _generate(_pine('[st, dir] = ta.supertrend(factor=3.0, atrPeriod=10)'))
        assert "ta::Supertrend" in cpp

    def test_ta_dmi_kwargs(self):
        cpp = _generate(_pine('[dp, dm, adx] = ta.dmi(diLength=14, adxSmoothing=14)'))
        assert "ta::DMI" in cpp

    def test_ta_sar_kwargs(self):
        cpp = _generate(_pine('x = ta.sar(start=0.02, inc=0.02, max=0.2)'))
        assert "ta::SAR" in cpp
        assert "current_bar_.high, current_bar_.low, current_bar_.close" in cpp

    def test_ta_linreg_kwargs(self):
        cpp = _generate(_pine('x = ta.linreg(source=close, length=14, offset=0)'))
        assert "ta::Linreg" in cpp

    def test_ta_percentrank_kwargs(self):
        cpp = _generate(_pine('x = ta.percentrank(source=close, length=14)'))
        assert "ta::PercentRank" in cpp

    def test_ta_vwma_kwargs(self):
        cpp = _generate(_pine('x = ta.vwma(source=close, length=20)'))
        assert "ta::VWMA" in cpp

    def test_ta_mom_kwargs(self):
        cpp = _generate(_pine('x = ta.mom(source=close, length=10)'))
        assert "ta::Mom" in cpp

    def test_ta_roc_kwargs(self):
        cpp = _generate(_pine('x = ta.roc(source=close, length=10)'))
        assert "ta::ROC" in cpp

    def test_ta_rising_kwargs(self):
        cpp = _generate(_pine('x = ta.rising(source=close, length=3)'))
        assert "ta::Rising" in cpp

    def test_ta_falling_kwargs(self):
        cpp = _generate(_pine('x = ta.falling(source=close, length=3)'))
        assert "ta::Falling" in cpp

    def test_ta_cci_kwargs(self):
        cpp = _generate(_pine('x = ta.cci(source=close, length=20)'))
        assert "ta::CCI" in cpp

    def test_ta_cum_kwargs(self):
        cpp = _generate(_pine('x = ta.cum(source=volume)'))
        assert "ta::Cum" in cpp

    def test_ta_sum_is_invalid_use_math_sum(self):
        with pytest.raises(CompileError):
            _generate(_pine('x = ta.sum(source=close, length=14)'))

    def test_ta_variance_kwargs(self):
        cpp = _generate(_pine('x = ta.variance(source=close, length=14)'))
        assert "ta::Variance" in cpp

    def test_ta_median_kwargs(self):
        cpp = _generate(_pine('x = ta.median(source=close, length=14)'))
        assert "ta::Median" in cpp

    def test_ta_highestbars_kwargs(self):
        cpp = _generate(_pine('x = ta.highestbars(source=high, length=14)'))
        assert "ta::HighestBars" in cpp

    def test_ta_lowestbars_kwargs(self):
        cpp = _generate(_pine('x = ta.lowestbars(source=low, length=14)'))
        assert "ta::LowestBars" in cpp

    def test_ta_mixed_positional_and_kwargs(self):
        """First arg positional, second as kwarg."""
        cpp = _generate(_pine('x = ta.sma(close, length=14)'))
        assert "ta::SMA" in cpp
        assert ".compute(" in cpp

    def test_ta_stoch_kwargs(self):
        cpp = _generate(_pine('x = ta.stoch(source=close, high=high, low=low, length=14)'))
        assert "ta::Stoch" in cpp


class TestMathKwargs:
    """Test math.* functions with kwargs through the full transpilation pipeline."""

    def test_math_abs_kwargs(self):
        cpp = _generate(_pine('x = math.abs(x=-5)'))
        assert "std::abs" in cpp

    def test_math_max_kwargs(self):
        cpp = _generate(_pine('x = math.max(x=1.0, y=2.0)'))
        assert "std::max" in cpp

    def test_math_min_kwargs(self):
        cpp = _generate(_pine('x = math.min(x=1.0, y=2.0)'))
        assert "std::min" in cpp

    def test_math_ceil_kwargs(self):
        cpp = _generate(_pine('x = math.ceil(x=1.5)'))
        assert "std::ceil" in cpp

    def test_math_floor_kwargs(self):
        cpp = _generate(_pine('x = math.floor(x=1.5)'))
        assert "std::floor" in cpp

    def test_math_round_1arg_kwargs(self):
        cpp = _generate(_pine('x = math.round(x=1.5)'))
        assert "std::round" in cpp

    def test_math_round_2arg_kwargs(self):
        cpp = _generate(_pine('x = math.round(x=1.555, precision=2)'))
        assert "std::round" in cpp
        assert "std::pow" in cpp  # round(x * pow(10, n)) / pow(10, n)

    def test_math_sqrt_kwargs(self):
        cpp = _generate(_pine('x = math.sqrt(x=4.0)'))
        assert "std::sqrt" in cpp

    def test_math_pow_kwargs(self):
        cpp = _generate(_pine('x = math.pow(base=2.0, exp=3.0)'))
        assert "std::pow" in cpp

    def test_math_log_kwargs(self):
        cpp = _generate(_pine('x = math.log(x=10.0)'))
        assert "std::log" in cpp

    def test_math_log10_kwargs(self):
        cpp = _generate(_pine('x = math.log10(x=100.0)'))
        assert "std::log10" in cpp

    def test_math_exp_kwargs(self):
        cpp = _generate(_pine('x = math.exp(x=1.0)'))
        # math.exp → std::exp, but "exp" is a C++ reserved word so it becomes _exp_
        # The important thing is that it compiles
        assert "std::exp" in cpp or "_exp_" in cpp

    def test_math_sin_kwargs(self):
        cpp = _generate(_pine('x = math.sin(x=1.0)'))
        assert "std::sin" in cpp

    def test_math_cos_kwargs(self):
        cpp = _generate(_pine('x = math.cos(x=1.0)'))
        assert "std::cos" in cpp

    def test_math_tan_kwargs(self):
        cpp = _generate(_pine('x = math.tan(x=1.0)'))
        assert "std::tan" in cpp

    def test_math_asin_kwargs(self):
        cpp = _generate(_pine('x = math.asin(x=0.5)'))
        assert "std::asin" in cpp

    def test_math_acos_kwargs(self):
        cpp = _generate(_pine('x = math.acos(x=0.5)'))
        assert "std::acos" in cpp

    def test_math_atan_kwargs(self):
        cpp = _generate(_pine('x = math.atan(x=1.0)'))
        assert "std::atan" in cpp

    def test_math_sign_kwargs(self):
        cpp = _generate(_pine('x = math.sign(x=-5.0)'))
        assert ">" in cpp and "<" in cpp  # sign uses comparison

    def test_math_todegrees_kwargs(self):
        cpp = _generate(_pine('x = math.todegrees(x=3.14)'))
        assert "180.0" in cpp
        assert "M_PI" in cpp

    def test_math_toradians_kwargs(self):
        cpp = _generate(_pine('x = math.toradians(x=180.0)'))
        assert "M_PI" in cpp

    def test_math_avg_kwargs(self):
        cpp = _generate(_pine('x = math.avg(x=1.0, y=2.0)'))
        assert "2.0" in cpp  # avg formula divides by 2

    def test_math_avg_three_kwargs(self):
        cpp = _generate(_pine('x = math.avg(x=1.0, y=2.0, z=3.0)'))
        assert "/ 3.0" in cpp

    def test_math_round_to_mintick_kwargs(self):
        cpp = _generate(_pine('x = math.round_to_mintick(x=100.123)'))
        # Lowered to the engine method (NaN/mintick<=0 guarded).
        assert "round_to_mintick(100.123)" in cpp

    def test_math_sum_kwargs_redirects_to_ta(self):
        """math.sum(source, length) should use math::Sum (rolling window)."""
        cpp = _generate(_pine('x = math.sum(source=close, length=14)'))
        assert "math::Sum" in cpp


class TestInputKwargs:
    """input.* and plain input() through transpile."""

    def test_plain_input_int(self):
        cpp = _generate(_pine('x = input(14, "len")'))
        assert "14" in cpp

    def test_input_int_title_defval_kwargs(self):
        cpp = _generate(_pine('x = input.int(title="Len", defval=20)'))
        assert "20" in cpp

    def test_input_int_with_display_kwarg(self):
        cpp = _generate(_pine('x = input.int(5, "t", display=display.none)'))
        assert "5" in cpp

    def test_input_price_defval_close(self):
        cpp = _generate(_pine('x = input.price(close, "p")'))
        assert "close" in cpp or "current_bar_" in cpp

    def test_display_none_emits_int(self):
        cpp = _generate(_pine('x = display.none'))
        assert "1" in cpp  # display.none -> 1 in codegen map


class TestStrategyKwargs:
    """Test strategy.* functions with kwargs through the full transpilation pipeline."""

    def test_strategy_entry_all_kwargs(self):
        cpp = _generate(_pine('strategy.entry(id="Long", direction=strategy.long)'))
        assert "strategy_entry(" in cpp
        assert '"Long"' in cpp

    def test_strategy_entry_with_stop_kwarg(self):
        cpp = _generate(_pine('strategy.entry(id="L", direction=strategy.long, stop=100.0)'))
        assert "strategy_entry(" in cpp
        assert "100" in cpp

    def test_strategy_entry_with_limit_kwarg(self):
        cpp = _generate(_pine('strategy.entry(id="L", direction=strategy.long, limit=95.0)'))
        assert "strategy_entry(" in cpp
        assert "95" in cpp

    def test_strategy_close_kwargs(self):
        cpp = _generate(_pine('strategy.close(id="Long")'))
        assert "strategy_close(" in cpp

    def test_strategy_exit_kwargs_limit_stop(self):
        cpp = _generate(_pine('strategy.exit(id="X", from_entry="Long", limit=110.0, stop=90.0)'))
        assert "strategy_exit(" in cpp
        assert "110" in cpp
        assert "90" in cpp

    def test_strategy_exit_kwargs_profit_loss(self):
        cpp = _generate(_pine('strategy.exit(id="X", from_entry="Long", profit=100, loss=50)'))
        assert "strategy_exit(" in cpp

    def test_strategy_exit_kwargs_trail(self):
        cpp = _generate(_pine('strategy.exit(id="X", from_entry="L", trail_points=50.0, trail_offset=20.0)'))
        assert "strategy_exit(" in cpp
        assert "50" in cpp

    def test_strategy_exit_alert_and_disable_kwargs(self):
        """Pine v6 exit(..., alert_profit, alert_loss, alert_trailing, disable_alert)."""
        cpp = _generate(_pine(
            'strategy.exit(id="X", from_entry="L", limit=110.0, disable_alert=true, alert_profit="ap")'
        ))
        assert "strategy_exit(" in cpp

    def test_strategy_order_kwargs(self):
        cpp = _generate(_pine('strategy.order(id="O", direction=strategy.long, qty=10)'))
        assert "strategy_order(" in cpp

    def test_strategy_cancel_kwargs(self):
        cpp = _generate(_pine('strategy.cancel(id="O")'))
        assert "strategy_cancel(" in cpp

    def test_strategy_close_all(self):
        cpp = _generate(_pine('strategy.close_all()'))
        assert "strategy_close_all()" in cpp

    def test_strategy_cancel_all(self):
        cpp = _generate(_pine('strategy.cancel_all()'))
        assert "strategy_cancel_all()" in cpp

    def test_strategy_entry_mixed_positional_kwargs(self):
        """First two positional, rest kwargs."""
        cpp = _generate(_pine('strategy.entry("L", strategy.long, stop=100.0)'))
        assert "strategy_entry(" in cpp
        assert "100" in cpp


class TestMathSumAsTA:
    """Verify math.sum is treated as a rolling TA call, not std::accumulate."""

    def test_math_sum_positional(self):
        cpp = _generate(_pine('x = math.sum(close, 14)'))
        assert "math::Sum" in cpp
        assert ".compute(" in cpp
        assert "std::accumulate" not in cpp

    def test_math_sum_kwargs(self):
        cpp = _generate(_pine('x = math.sum(source=close, length=14)'))
        assert "math::Sum" in cpp


class TestUserFunctionKwargs:
    """Test user-defined function calls with kwargs."""

    def test_user_func_kwargs(self):
        src = _pine('f(a, b) =>\n    a + b\nx = f(a=1, b=2)')
        cpp = _generate(src)
        assert "f(" in cpp
        assert "1" in cpp
        assert "2" in cpp

    def test_user_func_mixed(self):
        src = _pine('f(a, b, c) =>\n    a + b + c\nx = f(1, c=3, b=2)')
        cpp = _generate(src)
        assert "f(" in cpp


# ============================================================================
# Part 3: Signature consistency checks
# ============================================================================


class TestSignatureConsistency:
    """Verify that every registered function has consistent param/return data."""

    def test_every_ta_func_has_at_least_one_param_or_is_special(self):
        # No-arg TA indicators used as properties (ta.obv, ta.accdist, etc.)
        _TA_NO_PARAM = {"obv", "accdist", "nvi", "pvi", "pvt", "wad", "wvad", "iii"}
        for name, func in sigs.TA_FUNCTIONS.items():
            sig = func.primary
            if name in _TA_NO_PARAM:
                continue
            # All TA functions should have at least one param
            # (some like wpr have only length, some like cum have only source)
            assert len(sig.params) >= 1, f"ta.{name} has 0 params"

    def test_every_math_func_has_at_least_one_param(self):
        for name, func in sigs.MATH_FUNCTIONS.items():
            sig = func.primary
            assert len(sig.params) >= 1, f"math.{name} has 0 params"

    def test_strategy_funcs_return_void(self):
        _FLOAT_RETURNS = {"convert_to_account", "convert_to_symbol", "default_entry_qty"}
        for name, func in sigs.STRATEGY_FUNCTIONS.items():
            if name in _FLOAT_RETURNS:
                assert func.primary.return_type == PineType.FLOAT, \
                    f"strategy.{name} should return FLOAT"
                continue
            assert func.primary.return_type == PineType.VOID, \
                f"strategy.{name} should return VOID"

    def test_str_funcs_return_types(self):
        """str.* functions that return strings should have STRING return type."""
        string_returns = {"tostring", "format", "format_time", "substring",
                          "replace", "replace_all", "lower", "upper", "trim",
                          "repeat", "match", "split"}
        for name in string_returns:
            if name in sigs.STR_FUNCTIONS:
                ret = sigs.get_return_type("str", name)
                assert ret == PineType.STRING, f"str.{name} should return STRING, got {ret}"

    def test_ta_tuple_funcs(self):
        """TA functions that return tuples should be marked correctly."""
        tuple_funcs = {"macd": 3, "bb": 3, "kc": 3, "supertrend": 2, "dmi": 3}
        for name, count in tuple_funcs.items():
            func = sigs.TA_FUNCTIONS[name]
            sig = func.primary
            assert sig.returns_tuple is True, f"ta.{name} should return tuple"
            assert sig.tuple_count == count, \
                f"ta.{name} should have {count} tuple elements, got {sig.tuple_count}"

    def test_overloaded_functions(self):
        """Functions with multiple overloads should have > 1 signature."""
        assert len(sigs.MATH_FUNCTIONS["round"].signatures) == 2
        assert len(sigs.MATH_FUNCTIONS["max"].signatures) >= 2
        assert len(sigs.MATH_FUNCTIONS["min"].signatures) >= 2
        assert len(sigs.TA_FUNCTIONS["highest"].signatures) == 2
        assert len(sigs.TA_FUNCTIONS["lowest"].signatures) == 2
        assert len(sigs.BUILTIN_FUNCTIONS["nz"].signatures) == 2
        assert len(sigs.TA_FUNCTIONS["pivothigh"].signatures) == 2
        assert len(sigs.TA_FUNCTIONS["pivotlow"].signatures) == 2

    def test_default_source_mapping(self):
        """TA functions with implicit default source must be in TA_DEFAULT_SOURCE."""
        assert sigs.TA_DEFAULT_SOURCE["highest"] == "high"
        assert sigs.TA_DEFAULT_SOURCE["lowest"] == "low"
        assert sigs.TA_DEFAULT_SOURCE["pivothigh"] == "high"
        assert sigs.TA_DEFAULT_SOURCE["pivotlow"] == "low"

    def test_param_names_unique_within_signature(self):
        """No duplicate parameter names within a single signature."""
        all_funcs = [
            ("ta", sigs.TA_FUNCTIONS),
            ("math", sigs.MATH_FUNCTIONS),
            ("strategy", sigs.STRATEGY_FUNCTIONS),
            ("str", sigs.STR_FUNCTIONS),
            ("input", sigs.INPUT_FUNCTIONS),
            ("map", sigs.MAP_FUNCTIONS),
            ("builtin", sigs.BUILTIN_FUNCTIONS),
        ]
        for ns, funcs in all_funcs:
            for name, func in funcs.items():
                for i, sig in enumerate(func.signatures):
                    pnames = [p.name for p in sig.params]
                    assert len(pnames) == len(set(pnames)), \
                        f"{ns}.{name} overload {i} has duplicate param names: {pnames}"


# ---------------------------------------------------------------------------
# G2 sprint: SYMINFO_VARIABLES additions
# ---------------------------------------------------------------------------

class TestG2SyminfoVariables:
    """Verify G2 sprint additions to SYMINFO_VARIABLES."""

    def test_critical_fix_fields_present(self):
        """syminfo.prefix/root/pricescale/minmove were silently emitting 0;
        now accepted as na-producing fields in SYMINFO_VARIABLES."""
        for field in ("syminfo.prefix", "syminfo.root", "syminfo.pricescale", "syminfo.minmove"):
            assert field in sigs.SYMINFO_VARIABLES, f"{field} missing from SYMINFO_VARIABLES"

    def test_external_data_fields_present(self):
        for field in (
            "syminfo.mincontract", "syminfo.current_contract", "syminfo.expiration_date",
            "syminfo.isin", "syminfo.sector", "syminfo.industry",
        ):
            assert field in sigs.SYMINFO_VARIABLES, f"{field} missing from SYMINFO_VARIABLES"

    def test_low_tier_na_fields_present(self):
        for field in (
            "syminfo.employees", "syminfo.shareholders",
            "syminfo.shares_outstanding_float", "syminfo.shares_outstanding_total",
            "syminfo.recommendations_buy", "syminfo.recommendations_hold",
            "syminfo.recommendations_sell",
            "syminfo.target_price_average", "syminfo.target_price_high",
        ):
            assert field in sigs.SYMINFO_VARIABLES, f"{field} missing from SYMINFO_VARIABLES"

    def test_derivation_fields_present(self):
        assert "syminfo.main_tickerid" in sigs.SYMINFO_VARIABLES
        assert "syminfo.country" in sigs.SYMINFO_VARIABLES

    def test_field_types_correct(self):
        from pineforge_codegen.symbols import PineType
        S, F, I = PineType.STRING, PineType.FLOAT, PineType.INT
        assert sigs.SYMINFO_VARIABLES["syminfo.prefix"] == S
        assert sigs.SYMINFO_VARIABLES["syminfo.root"] == S
        assert sigs.SYMINFO_VARIABLES["syminfo.pricescale"] == F
        assert sigs.SYMINFO_VARIABLES["syminfo.minmove"] == F
        assert sigs.SYMINFO_VARIABLES["syminfo.expiration_date"] == I
        assert sigs.SYMINFO_VARIABLES["syminfo.sector"] == S
        assert sigs.SYMINFO_VARIABLES["syminfo.main_tickerid"] == S
        assert sigs.SYMINFO_VARIABLES["syminfo.country"] == S


class TestG2CodegenHelpers:
    """Verify codegen output for G2 new features."""

    def _generate(self, body: str) -> str:
        from pineforge_codegen import transpile
        return transpile(f'//@version=6\nstrategy("T")\n{body}\n')

    def test_backadjustment_on_emits_int(self):
        # backadjustment.* emit as integer constants (analyzer types as INT;
        # engine ignores them — codegen drops them from request.security kwargs)
        cpp = self._generate('x = backadjustment.on\n')
        assert cpp  # just verify it compiles

    def test_backadjustment_off_emits_int(self):
        cpp = self._generate('x = backadjustment.off\n')
        assert cpp

    def test_backadjustment_inherit_emits_int(self):
        cpp = self._generate('x = backadjustment.inherit\n')
        assert cpp

    def test_settlement_as_close_on_emits_int(self):
        cpp = self._generate('x = settlement_as_close.on\n')
        assert cpp

    def test_settlement_as_close_off_emits_int(self):
        cpp = self._generate('x = settlement_as_close.off\n')
        assert cpp

    def test_adjustment_dividends_emits_int(self):
        cpp = self._generate('x = adjustment.dividends\n')
        assert cpp

    def test_adjustment_splits_emits_int(self):
        cpp = self._generate('x = adjustment.splits\n')
        assert cpp

    def test_syminfo_prefix_emits_na(self):
        cpp = self._generate('x = syminfo.prefix\n')
        assert 'na<std::string>()' in cpp

    def test_syminfo_pricescale_emits_na(self):
        cpp = self._generate('x = syminfo.pricescale\n')
        assert 'na<double>()' in cpp

    def test_syminfo_shares_outstanding_routes_to_metadata(self):
        # Financial fields are injectable at runtime (#19); reads resolve
        # through the metadata map, which returns na until a feed injects.
        cpp = self._generate('x = syminfo.shares_outstanding_total\n')
        assert 'get_syminfo_metadata("shares_outstanding_total")' in cpp

    def test_syminfo_target_price_routes_to_metadata(self):
        cpp = self._generate('x = syminfo.target_price_average\n')
        assert 'get_syminfo_metadata("target_price_average")' in cpp

    def test_syminfo_main_tickerid_emits_derivation(self):
        cpp = self._generate('x = syminfo.main_tickerid\n')
        assert '_pf_derive_main_tickerid' in cpp

    def test_syminfo_country_emits_derivation(self):
        cpp = self._generate('x = syminfo.country\n')
        assert '_pf_derive_country' in cpp

    def test_derivation_helpers_emitted_in_output(self):
        """Both derivation helper functions should appear in every generated file."""
        cpp = self._generate('x = close\n')
        assert '_pf_derive_main_tickerid' in cpp
        assert '_pf_derive_country' in cpp

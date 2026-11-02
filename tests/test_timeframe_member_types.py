"""Timeframe member types follow Pine's int, bool and string contracts."""

import re

from pineforge_codegen import transpile


def test_timeframe_multiplier_arithmetic_and_float_comparison():
    pine = ('//@version=6\nstrategy("Timeframe integer")\n'
            'frameMultiple = timeframe.multiplier\n'
            'nextMultiple = frameMultiple + 1.0\n'
            'aboveFraction = frameMultiple > 1.5\n'
            'plot(nextMultiple)\nplot(aboveFraction ? 1 : 0)\n')
    cpp = transpile(pine)
    # Preservation pin: the INT signature and _type_for_decl give integral storage.
    assert re.search(r"\bint frameMultiple\s*=", cpp)
    assert "frameMultiple = tf_multiplier(script_tf_);" in cpp
    # _lower_binop keeps numeric +; an int/double relational uses the float band.
    assert "(frameMultiple + 1.0)" in cpp
    assert "auto _pna_l = (frameMultiple); auto _pna_r = (1.5);" in cpp
    assert "double _pfc_l = static_cast<double>(_pna_l);" in cpp
    assert "std::fabs(_pfc_l - _pfc_r) <= 1e-10" in cpp
    assert "((_pfc_l > _pfc_r) && !_pfc_eq)" in cpp


def test_timeframe_flags_are_bool_conditions():
    pine = ('//@version=6\nstrategy("Timeframe booleans")\n'
            'intradayValue() => timeframe.isintraday\n'
            'dailyValue() => timeframe.isdaily\n'
            'intradayFlag = timeframe.isintraday\n'
            'dailyFlag = timeframe.isdaily\n'
            'if intradayFlag\n    strategy.entry("Long", strategy.long)\n'
            'pickedDaily = dailyFlag ? 1 : 0\n'
            'pickedBoth = intradayValue() and dailyValue() ? 1 : 0\n')
    cpp = transpile(pine)
    # Preservation pin: BOOL signatures give bool slots and unchanged conditions.
    assert re.search(r"\bbool intradayFlag\s*=", cpp)
    assert re.search(r"\bbool dailyFlag\s*=", cpp)
    assert "intradayFlag = tf_is_intraday(script_tf_);" in cpp
    assert "dailyFlag = tf_is_daily(script_tf_);" in cpp
    assert "if (intradayFlag)" in cpp
    # The flag is the ternary's condition itself, whatever the parenthesisation
    # (a global assignment emits `name = ((cond) ? (1) : (0));`, as the sibling
    # ternary rows of the suite show).
    assert re.search(r"\bpickedDaily\s*=\s*\(*dailyFlag\)*\s*\?\s*\(*1\)*\s*:\s*\(*0", cpp)
    # Both helpers are called, so both are emitted.
    assert re.search(r"\bpickedBoth\s*=", cpp)
    # _emit_func maps each analyzer return type to bool, never std::string.
    assert re.search(r"\bbool\s+intradayValue\s*\(", cpp)
    assert re.search(r"\bbool\s+dailyValue\s*\(", cpp)


def test_timeframe_period_stays_string_in_string_functions():
    pine = ('//@version=6\nstrategy("Timeframe string")\n'
            'periodName = timeframe.period\n'
            'periodSize = str.length(periodName)\nplot(periodSize)\n')
    cpp = transpile(pine)
    # Preservation pin: STRING storage and str.length's table use .length().
    assert re.search(r"\bstd::string periodName\s*=", cpp)
    assert "periodName = script_tf_;" in cpp
    assert "(int)periodName.length()" in cpp

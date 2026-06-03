"""Tests for ta.vwap(source, anchor, stdev_mult) → 3-tuple codegen.

Verifies that the dual-overload vwap dispatch routes 3-arg calls to the
VWAPBands tuple-return path and emits correct C++ field accesses.
"""
import re
import pytest
from pineforge_codegen import transpile
from pineforge_codegen.support_checker import CompileError

PRELUDE = '//@version=6\nstrategy("T")\n'


def _transpile(src: str) -> str:
    """Transpile Pine source and return generated C++."""
    return transpile(src)


def _has_no_errors(src: str) -> bool:
    try:
        transpile(src)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Basic tuple unpack: [vw, ub, lb] = ta.vwap(close, ta.change(time, "1D"), 2)
# ---------------------------------------------------------------------------

def test_vwap_3arg_compiles():
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if close > ub
    strategy.entry("long", strategy.long)
"""
    assert _has_no_errors(src), "3-arg ta.vwap should compile without errors"


def test_vwap_3arg_uses_vwapbands_class():
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if close > ub
    strategy.entry("long", strategy.long)
"""
    cpp = _transpile(src)
    # Verify VWAPBands is instantiated as a class member
    assert "ta::VWAPBands" in cpp, "Expected ta::VWAPBands member in generated C++"


def test_vwap_3arg_emits_vwap_field():
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if vw > 0
    strategy.entry("long", strategy.long)
"""
    cpp = _transpile(src)
    # Should see .vwap field access in the generated code
    assert ".vwap" in cpp, "Expected .vwap field access in generated C++"


def test_vwap_3arg_emits_upper_lower_fields():
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if close > ub or close < lb
    strategy.entry("long", strategy.long)
"""
    cpp = _transpile(src)
    assert ".upper" in cpp, "Expected .upper field access in generated C++"
    assert ".lower" in cpp, "Expected .lower field access in generated C++"


def test_vwap_3arg_stdev_mult_in_ctor():
    """stdev_mult (2.0) should appear in the VWAPBands member constructor call.

    The generated initializer list looks like: _ta_vwap_bands_1(2)
    so we search for the member name pattern followed by the stdev_mult value.
    """
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if close > ub
    strategy.entry("long", strategy.long)
"""
    cpp = _transpile(src)
    # Initializer list: _ta_vwap_bands_N(stdev_mult)
    assert re.search(r"_ta_vwap_bands_\d+\(2", cpp), \
        "Expected _ta_vwap_bands_N(2...) in initializer list"


def test_vwap_1arg_still_scalar():
    """1-arg ta.vwap(close) must still produce a scalar (no tuple)."""
    src = PRELUDE + """\
v = ta.vwap(close)
if close > v
    strategy.entry("long", strategy.long)
"""
    cpp = _transpile(src)
    assert "ta::VWAP" in cpp, "1-arg vwap should use ta::VWAP, not ta::VWAPBands"
    assert "ta::VWAPBands" not in cpp, "1-arg vwap must not create VWAPBands instance"


def test_vwap_1arg_and_3arg_coexist():
    """Both forms can appear in the same script."""
    src = PRELUDE + """\
v_scalar = ta.vwap(close)
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2.0)
if close > ub and close > v_scalar
    strategy.entry("long", strategy.long)
"""
    assert _has_no_errors(src)
    cpp = _transpile(src)
    assert "ta::VWAP" in cpp
    assert "ta::VWAPBands" in cpp


def test_vwap_3arg_with_integer_stdev_mult():
    """stdev_mult can be an integer (auto-converted to float in Pine)."""
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(close, timeframe.change("1D"), 2)
if close < lb
    strategy.entry("long", strategy.long)
"""
    assert _has_no_errors(src)


def test_vwap_3arg_kwargs():
    """3-arg form should work with keyword arguments."""
    src = PRELUDE + """\
[vw, ub, lb] = ta.vwap(source=close, anchor=timeframe.change("1D"), stdev_mult=1.5)
if close > ub
    strategy.entry("long", strategy.long)
"""
    assert _has_no_errors(src)


def test_vwap_bare_property():
    """Bare property form ta.vwap (no parens) should compile and compute from close, volume, timestamp."""
    src = PRELUDE + """\
v = ta.vwap
if close > v
    strategy.entry("long", strategy.long)
"""
    assert _has_no_errors(src)
    cpp = _transpile(src)
    assert "ta::VWAP" in cpp
    assert "compute(current_bar_.close, current_bar_.volume, current_bar_.timestamp)" in cpp

"""Regression tests for TA constructor lengths derived from arithmetic over
inputs and from function-local / parameter expressions.

Bug history: when a ``ta.*`` length argument was not a literal or a direct
input alias — i.e. arithmetic over an input/const (``wilderLen = rsiLen*2-1``)
or a function-local / parameter-derived length (``wp = sf*2-1``; a helper
``f(src,_len) => ta.sma(src,_len)``) — the transpiler silently emitted a TA
constructor period of 1 with no overwriting runtime reset. The smoother then
degenerated to a no-op, producing a wrong indicator and wrong signals.

These tests pin the three faithful behaviors:
  1. class-scope arithmetic over an input folds the ctor-init to the literal
     AND emits an override-aware runtime reset;
  2. function-local / parameter-derived lengths (including a length threaded
     through a nested user-function call) resolve to the real input;
  3. a legitimate input that genuinely defaults to 1 stays period 1;
and the guardrail: a genuinely-unresolvable computed length raises instead of
silently emitting period 1.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


def _ctor_period(cpp: str, member: str) -> str:
    """Return the ctor-init period for a TA member, e.g. ``_ta_ema_9`` -> '27'."""
    m = re.search(rf"\b{re.escape(member)}\(([^)]*)\)", cpp)
    assert m, f"{member} not found in initializer list"
    return m.group(1).strip()


def _reset_line(cpp: str, member: str) -> str:
    """Return the runtime-reset assignment for a TA member (the line that
    overwrites the ctor placeholder under ``!_ta_initialized_``)."""
    for ln in cpp.splitlines():
        s = ln.strip()
        if s.startswith(f"{member} = ") and s.endswith(";"):
            return s
    return ""


# ---------------------------------------------------------------------------
# 1. Class-scope arithmetic over an input  (wilderLen = rsiLen * 2 - 1)
# ---------------------------------------------------------------------------

def test_class_scope_arithmetic_over_input_length():
    src = """//@version=6
strategy("derived-class-scope")
rsiLen = input.int(14, "RSI Length")
wilderLen = rsiLen * 2 - 1
src = ta.ema(close, wilderLen)
plot(src)
"""
    cpp = transpile(src)
    # Find the EMA member sized by wilderLen.
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    assert members, "no EMA member emitted"
    # The wilderLen-sized EMA folds to 14*2-1 = 27 in the init list.
    sized = [m for m in members if _ctor_period(cpp, m) == "27"]
    assert sized, f"expected an EMA with ctor period 27, got {[(_m, _ctor_period(cpp, _m)) for _m in members]}"
    member = sized[0]
    # And the runtime reset re-derives it from the (possibly overridden) input.
    reset = _reset_line(cpp, member)
    assert 'get_input_int("RSI Length", 14)' in reset
    assert "* 2 - 1" in reset
    assert "ta::EMA(1)" not in reset  # must NOT be the silent no-op


# ---------------------------------------------------------------------------
# 2a. Function-local derived length  (qqeCalc: wp = sf * 2 - 1)
# ---------------------------------------------------------------------------

def test_function_local_derived_length():
    src = """//@version=6
strategy("derived-func-local")
rsiSmooth = input.int(5, "Smooth EMA Length")
qqeCalc(int sf) =>
    wp = sf * 2 - 1
    ta.ema(ta.ema(close, wp), wp)
out = qqeCalc(rsiSmooth)
plot(out)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    # sf = 5 -> wp = 9. The wp-sized EMAs must be period 9, not 1.
    sized = [m for m in members if _ctor_period(cpp, m) == "9"]
    assert len(sized) >= 2, (
        f"expected >=2 EMAs with ctor period 9, got "
        f"{[(_m, _ctor_period(cpp, _m)) for _m in members]}"
    )
    for m in sized:
        reset = _reset_line(cpp, m)
        assert 'get_input_int("Smooth EMA Length", 5)' in reset
        assert "* 2 - 1" in reset
    assert "ta::EMA(1)" not in cpp


# ---------------------------------------------------------------------------
# 2b. Length threaded through a NESTED user-function call
#     f_basisMa(src, _len) => ta.sma(src, _len)   called from f_bbwp(_bbwLen)
# ---------------------------------------------------------------------------

def test_nested_user_func_param_length():
    src = """//@version=6
strategy("derived-nested-param")
i_bbwLen = input.int(7, "BBW Basis Length")
f_basisMa(float _src, int _len) =>
    ta.sma(_src, _len)
f_bbwp(float _price, int _bbwLen) =>
    f_basisMa(_price, _bbwLen) + ta.stdev(_price, _bbwLen)
out = f_bbwp(close, i_bbwLen)
plot(out)
"""
    cpp = transpile(src)
    # The SMA inside f_basisMa must be sized by the real input (7), not 1.
    sma_members = re.findall(r"(_ta_sma_\d+)\(", cpp)
    assert sma_members
    sized = [m for m in sma_members if _ctor_period(cpp, m) == "7"]
    assert sized, (
        f"nested SMA length not resolved; got "
        f"{[(_m, _ctor_period(cpp, _m)) for _m in sma_members]}"
    )
    reset = _reset_line(cpp, sized[0])
    assert 'get_input_int("BBW Basis Length", 7)' in reset
    assert "ta::SMA(1)" not in cpp


# ---------------------------------------------------------------------------
# 3. Legitimate input that genuinely defaults to 1 stays period 1
# ---------------------------------------------------------------------------

def test_legit_input_default_one_preserved():
    src = """//@version=6
strategy("legit-default-one")
atrLen = input.int(1, "UT Bot ATR Period")
a = ta.atr(atrLen)
plot(a)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_atr_\d+)\(", cpp)
    assert members
    m = members[0]
    assert _ctor_period(cpp, m) == "1"
    # The reset is still emitted so an override (e.g. set ATR Period = 10)
    # re-sizes the buffer — the period 1 here is the genuine Pine default.
    reset = _reset_line(cpp, m)
    assert 'get_input_int("UT Bot ATR Period", 1)' in reset


# ---------------------------------------------------------------------------
# Guardrail: a genuinely-unresolvable computed length raises (no silent 1).
# ---------------------------------------------------------------------------

def test_unresolvable_length_raises_loudly():
    # ``barsSince(...)`` is a runtime series, not a const or input — there is
    # no faithful compile-time buffer size and no input to re-derive from.
    src = """//@version=6
strategy("unresolvable-length")
n = ta.barssince(close > open)
v = ta.ema(close, n)
plot(v)
"""
    with pytest.raises(CompileError):
        transpile(src)


# ---------------------------------------------------------------------------
# Determinism: transpiling the same source twice is byte-identical.
# ---------------------------------------------------------------------------

def test_derived_length_transpile_is_deterministic():
    src = """//@version=6
strategy("determinism")
rsiLen = input.int(14, "RSI Length")
wilderLen = rsiLen * 2 - 1
x = ta.ema(close, wilderLen)
plot(x)
"""
    assert transpile(src) == transpile(src)

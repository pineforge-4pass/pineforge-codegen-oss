"""Regression: a bare-``na`` REASSIGNMENT (``x := na``) must lower to a typed
``na<T>()`` matching the target's declared C++ type, not the unconditional
``na<double>()``.

Root cause: ``visit_expr._visit_ident`` renders a bare ``na`` as
``na<double>()``. The *initializer* path retypes it (``_typed_na_init`` /
``_visit_rhs_value(target_cpp_type=...)``), but the ``:=`` reassignment path
called ``_visit_rhs_value`` WITHOUT a target type, so the retype branch never
fired and ``x := na`` emitted ``na<double>()`` for every LHS type.

Assigning a double quiet-NaN into an ``int``/``int64_t`` member is undefined
behaviour: on ARM64 ``FCVTZS(NaN) == 0`` (not the ``na<int64_t>()`` sentinel
``INT64_MIN``), so ``is_na()`` is false forever. Proven in
``gonzowiththewind-sisyphus-happiness``: an ``int64_t sessionOpenTime`` daily
open-suppress timer, reset with ``sessionOpenTime := na`` each new day, was
permanently disarmed because the reset stored ``na<double>()`` instead of
``na<int64_t>()``.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile

from tests._compile import compile_cpp, have_compile_env


def _assign_rhs(cpp: str, lhs: str) -> list[str]:
    """Return the RHS of every ``<lhs> = <rhs>;`` statement (plain assignment,
    not declaration / member-init-list) in the generated C++."""
    out = []
    pat = re.compile(rf"(?<![\w>])\b{re.escape(lhs)}\s*=\s*(na<[^;]*>\(\))\s*;")
    for m in pat.finditer(cpp):
        out.append(m.group(1))
    return out


# --------------------------------------------------------------------------
# (a) class-scope var int, never holds time -> declared ``int`` -> na<int>()
# --------------------------------------------------------------------------
def test_class_scope_int_var_na_reassign_retypes_to_int():
    src = """//@version=6
strategy("t")
var int slot = na
if close > open
    slot := na
plot(na(slot) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "int slot;" in cpp
    assert "slot = na<int>();" in cpp, cpp
    # The reassignment must NOT store a double NaN into an int member.
    assert "slot = na<double>();" not in cpp


# --------------------------------------------------------------------------
# (d)/(gonzo) int var ALSO reassigned to ``time`` -> promoted to int64_t;
# BOTH the ``:= na`` reset AND the ``:= time`` set must use the int64 member.
# --------------------------------------------------------------------------
def test_int64_promoted_var_na_reassign_retypes_to_int64():
    src = """//@version=6
strategy("t")
var int sessionOpenTime = na
newDay = ta.change(dayofweek) != 0
if newDay
    sessionOpenTime := na
if not na(sessionOpenTime)
    strategy.close("L")
if close > open
    sessionOpenTime := time
plot(close)
"""
    cpp = transpile(src)
    assert "int64_t sessionOpenTime;" in cpp
    # The na reset must match the promoted member type, not na<double>().
    assert "sessionOpenTime = na<int64_t>();" in cpp, cpp
    assert "sessionOpenTime = na<double>();" not in cpp


# --------------------------------------------------------------------------
# (b) function-local var int -> lifted to an int member; both the once-only
# init block AND the ``:= na`` reassignment must be typed.
# --------------------------------------------------------------------------
def test_function_local_int_var_na_reassign_retypes_to_int():
    src = """//@version=6
strategy("t")
f() =>
    var int et = na
    if bar_index > 5
        et := na
    na(et) ? 0.0 : 1.0
plot(f())
"""
    cpp = transpile(src)
    assert "int et = na<int>();" in cpp
    # No double-NaN slip into the int member (init block OR reassignment).
    assert "et = na<double>();" not in cpp, cpp
    assert "et = na<int>();" in cpp


# --------------------------------------------------------------------------
# (c) float var -> declared double -> the na spelling stays na<double>()
# (guard against over-reach).
# --------------------------------------------------------------------------
def test_float_var_na_reassign_stays_double():
    src = """//@version=6
strategy("t")
var float px = na
if close > open
    px := na
plot(px)
"""
    cpp = transpile(src)
    assert "double px;" in cpp
    assert "px = na<double>();" in cpp
    assert "px = na<int>();" not in cpp
    assert "px = na<int64_t>();" not in cpp


# --------------------------------------------------------------------------
# (e) bool var -> na<bool>()
# --------------------------------------------------------------------------
def test_bool_var_na_reassign_retypes_to_bool():
    src = """//@version=6
strategy("t")
var bool flag = na
if close > open
    flag := na
plot(na(flag) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "bool flag;" in cpp
    assert "flag = na<bool>();" in cpp, cpp
    assert "flag = na<double>();" not in cpp


# --------------------------------------------------------------------------
# (f) faithful gonzo daily-open-timer reduction: the disarm bug in miniature.
# --------------------------------------------------------------------------
def test_gonzo_daily_open_timer_reduction():
    src = """//@version=6
strategy("gonzo-lite")
var int sessionOpenTime = na
newDay = ta.change(dayofweek) != 0
inRTH = hour >= 9 and hour < 16
if newDay
    sessionOpenTime := na
if inRTH and na(sessionOpenTime)
    sessionOpenTime := time
secSinceOpen = na(sessionOpenTime) ? na : (time - sessionOpenTime) / 1000
if not na(secSinceOpen) and secSinceOpen > 3600
    strategy.entry("L", strategy.long)
plot(close)
"""
    cpp = transpile(src)
    assert "int64_t sessionOpenTime;" in cpp
    rhs = _assign_rhs(cpp, "sessionOpenTime")
    # Every na reassignment of the timer must be int64-typed.
    assert rhs, cpp
    assert all(r == "na<int64_t>()" for r in rhs), rhs


# --------------------------------------------------------------------------
# Compile-level guard: the fixed gonzo-like emit is well-formed against the
# public engine headers (skips cleanly when the engine include is absent).
# --------------------------------------------------------------------------
@pytest.mark.skipif(not have_compile_env(), reason="engine include / compiler unavailable")
def test_int64_na_reassign_compiles():
    src = """//@version=6
strategy("t")
var int sessionOpenTime = na
newDay = ta.change(dayofweek) != 0
if newDay
    sessionOpenTime := na
if na(sessionOpenTime)
    sessionOpenTime := time
plot(close)
"""
    cpp = transpile(src)
    compile_cpp(cpp, label="int64-na-reassign")

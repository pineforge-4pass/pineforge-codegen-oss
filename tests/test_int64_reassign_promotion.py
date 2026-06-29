"""Regression (BUG A): int->int64_t promotion must consider REASSIGNMENTS.

Pine ``var int entryTime = na`` then ``entryTime := time`` stores an epoch-ms
value (~1.74e12) that overflows 32-bit ``int``. The promotion guard only
inspected the *initializer* (here ``na``), not the ``:= time`` reassignment, so
the member was emitted as ``int`` and downstream time math (e.g. MaxHold) misfired.

``time`` (and ``time_close`` / ``timenow``) are bare ``Identifier`` builtins in
Pine, so the guard must also match an identifier RHS, not only a ``FuncCall``.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile


def test_var_int_reassigned_to_time_promotes_to_int64():
    src = """//@version=6
strategy("t")
var int entryTime = na
if close > open
    entryTime := time
plot(na(entryTime) ? na : 1.0)
"""
    cpp = transpile(src)
    # Member declared int64_t, ctor na-init re-typed.
    assert "int64_t entryTime;" in cpp
    assert "entryTime(na<int64_t>())" in cpp
    # No 32-bit slip-through.
    assert "int entryTime;" not in cpp.replace("int64_t entryTime;", "")


def test_local_int_reassigned_to_time_promotes_to_int64():
    src = """//@version=6
strategy("t")
var int et = na
et := time
exitNow = na(et) ? false : (time - et > 1000)
if exitNow
    strategy.close("L")
plot(close)
"""
    cpp = transpile(src)
    assert "int64_t et;" in cpp
    assert "et(na<int64_t>())" in cpp


def test_timenow_identifier_reassign_promotes():
    src = """//@version=6
strategy("t")
var int t0 = na
if barstate.isconfirmed
    t0 := timenow
plot(na(t0) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "int64_t t0;" in cpp


def test_int_var_never_reassigned_to_time_stays_int():
    # Control: a plain int counter must NOT be widened.
    src = """//@version=6
strategy("t")
var int counter = 0
if close > open
    counter := counter + 1
plot(counter)
"""
    cpp = transpile(src)
    assert "int64_t counter" not in cpp
    assert "int counter;" in cpp

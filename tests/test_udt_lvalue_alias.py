"""Regression (BUG C): UDT lvalue init must ALIAS, not value-copy.

Pine UDTs are reference types. A local initialised from a var/global UDT lvalue
(or a ternary/switch selecting such lvalues) and then mutated through must write
back to the global. Codegen previously emitted a value copy, so the mutation was
lost. The fix emits a C++ reference (non-rebinding) or pointer (rebinding) alias.

Also covers the related crash unmasked by the alias fix: a drawing-style visual
constant (``label.style_*``) passed positionally into a user function's
``string`` parameter lowered to a bare ``0`` -> ``std::string((char const*)0)``
-> null ``strlen`` SEGV. It must coerce to ``std::string("")``.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _func_body(cpp: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\w*\(.*?\n    \}}", cpp, re.S)
    assert m is not None, f"function {name} not found"
    return m.group(0)


PROLOGUE = """//@version=6
strategy("t")
type pivot
    float currentLevel
    bool  crossed
var pivot gLow  = pivot.new(na, false)
var pivot gHigh = pivot.new(na, false)
"""


def test_ternary_of_var_udts_mutated_emits_reference_alias():
    src = PROLOGUE + """
upd(bool internal) =>
    pivot p = internal ? gLow : gHigh
    p.currentLevel := low
    p.crossed := false
    0
if close > open
    upd(true)
plot(close)
"""
    cpp = transpile(src)
    body = _func_body(cpp, "upd")
    # Reference alias (non-rebinding), not a value copy.
    assert re.search(r"pivot& p = \(\(internal\) \? \(gLow\) : \(gHigh\)\);", body)
    assert "pivot p = ((internal)" not in body
    # Mutations write through the reference with ``.`` member access.
    assert "p.currentLevel = " in body
    assert "p.crossed = " in body


def test_rebound_udt_local_emits_pointer_alias():
    src = PROLOGUE + """
disp(bool internal) =>
    pivot p = internal ? gHigh : gLow
    p.crossed := true
    p := internal ? gLow : gHigh
    p.crossed := true
    0
if close > open
    disp(false)
plot(close)
"""
    cpp = transpile(src)
    body = _func_body(cpp, "disp")
    # Pointer alias because the local is rebound to a different lvalue.
    assert re.search(r"pivot\* p = \(internal \? &\(gHigh\) : &\(gLow\)\);", body)
    # Field access goes through ``->`` and the rebind takes the address.
    assert "p->crossed = true;" in body
    assert re.search(r"p = \(internal \? &\(gLow\) : &\(gHigh\)\);", body)


def test_value_snapshot_not_aliased():
    # CAUTION control: a local copied from a .new() ctor (not a var/global
    # lvalue) and mutated must stay a VALUE copy, never an alias.
    src = PROLOGUE + """
mk() =>
    pivot p = pivot.new(1.0, false)
    p.currentLevel := 2.0
    p.currentLevel
v = mk()
plot(v)
"""
    cpp = transpile(src)
    body = _func_body(cpp, "mk")
    assert "pivot& p" not in body
    assert "pivot* p" not in body
    assert "pivot p = " in body


def test_readonly_var_udt_local_not_aliased():
    # A local read but never mutated needn't alias (value-copy is fine and
    # avoids surprising reference semantics for pure reads).
    src = PROLOGUE + """
rd(bool internal) =>
    pivot p = internal ? gLow : gHigh
    p.currentLevel
x = rd(true)
plot(x)
"""
    cpp = transpile(src)
    body = _func_body(cpp, "rd")
    assert "pivot& p" not in body
    assert "pivot* p" not in body


def test_array_get_udt_local_mutation_emits_reference_alias():
    src = PROLOGUE + """
var array<pivot> pivots = array.new<pivot>()
upd(int i) =>
    pivot p = array.get(pivots, i)
    p.currentLevel := close
    p.crossed := true
    0
if array.size(pivots) == 0
    array.push(pivots, pivot.new(na, false))
if close > open
    upd(0)
plot(close)
"""
    cpp = transpile(src)
    body = _func_body(cpp, "upd")
    assert "pivot& p = pivots[(i)];" in body
    assert "pivot p = pivots[(i)];" not in body
    assert "p.currentLevel = " in body
    assert "p.crossed = true;" in body


_GLOBAL_ARRAY_PROLOGUE = PROLOGUE + """
var array<pivot> pivots = array.new<pivot>()
if array.size(pivots) == 0
    array.push(pivots, pivot.new(na, false))
"""


def test_global_while_array_get_udt_mutation_emits_reference_alias():
    # Regression: a GLOBAL-scope ``while`` loop-local bound from an array
    # element and field-mutated is hoisted to a class member and its in-loop
    # init lowered to a value-copy assignment, so the mutation was lost. It must
    # instead ALIAS the element (Pine array elements of a UDT are references) so
    # a later ``arr.get(i).f`` read sees the new value.
    src = _GLOBAL_ARRAY_PROLOGUE + """
int wi = 0
while wi < array.size(pivots)
    pivot p = array.get(pivots, wi)
    p.currentLevel := close
    p.crossed := true
    wi += 1
plot(close)
"""
    cpp = transpile(src)
    # Fresh per-iteration reference to the array element (write-back), not copy.
    assert re.search(r"pivot& p = pivots\[\(wi\)\];", cpp)
    assert "pivot p = pivots[(wi)];" not in cpp
    # The hoisted value member is suppressed (the alias replaces it).
    assert "pivot p = pivot{};" not in cpp
    # Mutations write through the reference with ``.`` member access.
    assert "p.currentLevel = " in cpp
    assert "p.crossed = true;" in cpp


def test_global_for_array_get_udt_mutation_emits_reference_alias():
    # Same defect via a GLOBAL ``for i = ...`` loop. Here the get-local stays a
    # true local (not hoisted), but the function-local alias path no-ops at
    # global scope, so it also value-copied. Must alias for write-back.
    src = _GLOBAL_ARRAY_PROLOGUE + """
for fi = 0 to array.size(pivots) - 1
    pivot p = array.get(pivots, fi)
    p.currentLevel := close
    p.crossed := true
plot(close)
"""
    cpp = transpile(src)
    assert re.search(r"pivot& p = pivots\[\(fi\)\];", cpp)
    assert "pivot p = pivots[(fi)];" not in cpp
    assert "p.currentLevel = " in cpp
    assert "p.crossed = true;" in cpp


def test_global_readonly_array_get_udt_stays_value_copy():
    # CAUTION control locking the scope of the fix: a GLOBAL get-local that is
    # only READ (never field-mutated) must keep value-copy semantics — no
    # needless reference/pointer aliasing (this is what keeps the read-only
    # tripwire strategies byte-identical).
    src = _GLOBAL_ARRAY_PROLOGUE + """
float acc = 0.0
for ri = 0 to array.size(pivots) - 1
    pivot p = array.get(pivots, ri)
    acc := acc + p.currentLevel
plot(acc)
"""
    cpp = transpile(src)
    assert "pivot& p" not in cpp
    assert "pivot* p" not in cpp
    # Plain value copy of the element remains.
    assert "pivot p = pivots[(ri)];" in cpp


def test_drawing_style_constant_into_string_param_coerced():
    # The crash unmasked by the alias fix: label.style_* into a string param.
    src = """//@version=6
strategy("t")
draw(string labelStyle) =>
    label.new(bar_index, close, "x", style=labelStyle)
    0
if close > open
    draw(label.style_label_down)
plot(close)
"""
    cpp = transpile(src)
    assert 'draw(std::string(""))' in cpp
    # No bare null-pointer string construction.
    assert "draw(0)" not in cpp

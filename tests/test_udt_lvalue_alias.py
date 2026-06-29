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

"""Transpile contract: enum declaration order vs input.enum (Analyzer + CodeGen)."""

import pytest
from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


def test_transpile_rejects_input_enum_before_enum_decl():
    src = '''//@version=6
strategy("T")
x = input.enum(Signal.Buy, "t")
enum Signal
    Buy
'''
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert "declared above" in exc.value.diagnostics[0].message.lower()


def test_transpile_ok_when_enum_first():
    src = '''//@version=6
strategy("T")
enum Signal
    Buy
x = input.enum(Signal.Buy, "t")
'''
    cpp = transpile(src)
    assert "class GeneratedStrategy" in cpp
    assert "Signal_Buy" in cpp

"""A Pine library source parsed as a module (``library_modules``).

A library starts with ``library()``; what an importing script reaches is what
it ``export``s: functions, methods, types, enums and ``export const``
variables (TradingView's Libraries page; ``export const``: the June 2025
release notes). Everything else at its top level is private, including the
example code a library runs when it is itself on a chart. A library keeps its
own ``//@version``, and may import other libraries. The fixtures are
clean-room synthetic libraries (``fixtures/pine_libraries``), never a
published one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import (
    EnumDecl, FuncDef, MethodDef, StrategyDecl, TypeDecl, VarDecl,
)
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.library_modules import (
    normalize_library_text, parse_library_module,
)
from pineforge_codegen.parser import Parser

LIBS = Path(__file__).parent / "fixtures" / "pine_libraries"


def _lib(path: str) -> str:
    return (LIBS / f"{path}.pine").read_text(encoding="utf-8")


def test_signals_fixture_parts():
    mod = parse_library_module("pftest/Signals/1", _lib("pftest/Signals/1"))
    assert mod.pine_version == 6
    assert mod.title == "Signals"
    assert mod.name == "Signals"
    assert {a: s.path for a, s in mod.imports.items()} == {"base": "pftest/Base/1"}
    assert mod.exports == {
        "pin", "dir", "tag", "counter", "smooth", "span", "Bar2", "Side",
        "RATIO", "scaled", "ptMid", "seeded",
    }
    # Private helpers and constants are parsed, not exported.
    assert {"wicks", "dirAt"} <= set(mod.functions)
    assert "secret" in mod.globals and "secret" not in mod.exports
    assert isinstance(mod.types["Bar2"], TypeDecl)
    assert isinstance(mod.enums["Side"], EnumDecl)
    (method,) = mod.methods["width"]
    assert isinstance(method, MethodDef) and mod.exported(method)
    assert method.type_name == "Bar2"
    ratio = mod.globals["RATIO"]
    assert isinstance(ratio, VarDecl) and ratio.type_hint == "float"
    assert (ratio.annotations or {}).get("declared_const") is True


def test_each_module_keeps_its_own_version():
    v5 = ('// SPDX-License-Identifier: Apache-2.0\n//@version=5\nlibrary("Five")\n'
          'export f(float x) => x / 2\n')
    assert parse_library_module("pftest/Five/1", v5).pine_version == 5
    assert parse_library_module("pftest/Base/1", _lib("pftest/Base/1")).pine_version == 6


@pytest.mark.parametrize("text, message", [
    ('//@version=4\nlibrary("Old")\nexport f(float x) => x\n', "//@version=4"),
    ('library("None")\nexport f(float x) => x\n', "no //@version directive"),
    ('//@version=6\nstrategy("S")\nplot(close)\n', "is not a library"),
    ('//@version=6\nexport f(float x) => x\n', "is not a library"),
])
def test_what_is_not_a_library_is_refused_by_name(text, message):
    with pytest.raises(CompileError) as err:
        parse_library_module("pftest/Odd/1", text)
    text_of = str(err.value)
    assert "pftest/Odd/1" in text_of and message in text_of, text_of


def test_line_ends_are_tradingviews():
    """pine-facade serves some sources with CRLF line ends; TradingView ends a
    line at CRLF, CR or LF, and the lexer reads LF."""
    lf = _lib("pftest/Base/1")
    for form in (lf.replace("\n", "\r\n"), lf.replace("\n", "\r"), "﻿" + lf):
        assert normalize_library_text(form) == lf
        mod = parse_library_module("pftest/Base/1", form)
        assert mod.exports == {"twice", "Pt", "mid"}


def test_export_kinds_parse_in_a_library_only():
    src = ('//@version=6\nlibrary("K")\n'
           'export f(simple int n = 2) => n\n'
           'export method m(array<float> self, series float x) => self.size() + x\n'
           'export type T\n    int a\n'
           'export enum E\n    one\n    two = "Two"\n'
           'export const int N = 3\n')
    program = Parser(Lexer(src).tokenize(), source=src, library=True).parse()
    kinds = [type(s) for s in program.body]
    assert kinds == [StrategyDecl, FuncDef, MethodDef, TypeDecl, EnumDecl, VarDecl]
    assert all((s.annotations or {}).get("exported") for s in program.body[1:])
    # A method parameter may carry a qualifier.
    method = program.body[2]
    assert method.params == ["self", "x"]
    assert method.annotations["param_type_hints"] == ["array<float>", "float"]
    # In a script, `export` and `library()` keep their refusals.
    with pytest.raises(CompileError, match="belong to Pine libraries"):
        transpile('//@version=6\nstrategy("S")\nexport f(float x) => x\n')


def test_a_misplaced_export_is_a_located_error():
    src = '//@version=6\nlibrary("K")\nexport x = 1\n'
    with pytest.raises(CompileError) as err:
        parse_library_module("pftest/K/1", src)
    (diag,) = err.value.diagnostics
    assert diag.location.file == "pftest/K/1" and diag.location.line == 3
    assert "'export' must precede" in diag.message


def test_script_method_parameters_take_qualifiers_and_library_types():
    """``method m(T self, series float x)`` and ``f(lib.Type t)`` used to fail
    to parse (the method loop took ``series`` as the parameter's name)."""
    src = ('//@version=6\nstrategy("S")\n'
           'type P\n    float v\n'
           'method add(P self, series float x) => self.v + x\n'
           'p = P.new(1.0)\nplot(p.add(close))\n')
    assert "add" in transpile(src)
    program = Parser(Lexer(src).tokenize(), source=src).parse()
    method = next(s for s in program.body if isinstance(s, MethodDef))
    assert method.annotations["param_type_hints"] == ["P", "float"]
    dotted = '//@version=6\nstrategy("S")\nf(lib.Type t, float x) => x\n'
    program = Parser(Lexer(dotted).tokenize(), source=dotted).parse()
    func = next(s for s in program.body if isinstance(s, FuncDef))
    assert func.params == ["t", "x"]
    assert func.annotations["param_type_hints"] == ["lib.Type", "float"]


def test_a_parameter_named_like_a_qualifier_is_a_parameter():
    src = '//@version=6\nstrategy("S")\nf(simple, series = 2) => simple + series\nplot(f(1))\n'
    program = Parser(Lexer(src).tokenize(), source=src).parse()
    func = next(s for s in program.body if isinstance(s, FuncDef))
    assert func.params == ["simple", "series"]

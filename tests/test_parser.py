"""Tests for the rewritten PineScript v6 parser (Tasks 5 & 6)."""

import glob
import os
import re

import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.ast_nodes import *


def _parse_expr(src: str):
    """Helper: parse a single expression from source."""
    tokens = Lexer(src + "\n").tokenize()
    p = Parser(tokens)
    return p._parse_expression()


def _parse(src: str) -> Program:
    tokens = Lexer(src).tokenize()
    return Parser(tokens, source=src).parse()


# === Expression tests (Task 5) ===


def test_number_literal():
    node = _parse_expr("42")
    assert isinstance(node, NumberLiteral)
    assert node.value == 42


def test_binary_op_precedence():
    node = _parse_expr("1 + 2 * 3")
    assert isinstance(node, BinOp)
    assert node.op == "+"
    assert isinstance(node.right, BinOp)
    assert node.right.op == "*"


def test_comparison():
    node = _parse_expr("a > b")
    assert isinstance(node, BinOp)
    assert node.op == ">"


def test_logical_and_or():
    node = _parse_expr("a and b or c")
    assert isinstance(node, BinOp)
    assert node.op == "or"
    assert isinstance(node.left, BinOp)
    assert node.left.op == "and"


def test_not():
    node = _parse_expr("not x")
    assert isinstance(node, UnaryOp)
    assert node.op == "not"


def test_ternary():
    node = _parse_expr("x ? 1 : 2")
    assert isinstance(node, Ternary)


def test_func_call():
    node = _parse_expr("ta.sma(close, 14)")
    assert isinstance(node, FuncCall)
    assert isinstance(node.callee, MemberAccess)
    assert len(node.args) == 2


def test_func_call_kwargs():
    node = _parse_expr('strategy.entry("L", strategy.long, stop=high)')
    assert isinstance(node, FuncCall)
    assert "stop" in node.kwargs


def test_subscript():
    node = _parse_expr("close[1]")
    assert isinstance(node, Subscript)
    assert isinstance(node.object, Identifier)


def test_member_access():
    node = _parse_expr("strategy.long")
    assert isinstance(node, MemberAccess)
    assert node.member == "long"


def test_unary_minus():
    node = _parse_expr("-x")
    assert isinstance(node, UnaryOp)
    assert node.op == "-"


def test_string_literal():
    node = _parse_expr('"hello"')
    assert isinstance(node, StringLiteral)
    assert node.value == "hello"


def test_na_literal():
    node = _parse_expr("na")
    assert isinstance(node, NaLiteral)


def test_bool_literal():
    node = _parse_expr("true")
    assert isinstance(node, BoolLiteral)
    assert node.value is True


def test_color_literal():
    node = _parse_expr("#FF00FF")
    assert isinstance(node, ColorLiteral)
    assert node.value == "#FF00FF"


def test_for_loop_var_field():
    prog = _parse("for i = 0 to 10\n    x = i\n")
    stmt = prog.body[0]
    assert stmt.var == "i"


def test_parens():
    node = _parse_expr("(a + b) * c")
    assert isinstance(node, BinOp)
    assert node.op == "*"
    assert isinstance(node.left, BinOp)


# === Statement tests (Task 6) ===


def test_var_decl():
    prog = _parse("x = 14\n")
    assert isinstance(prog.body[0], VarDecl)
    assert prog.body[0].name == "x"


def test_var_with_keyword():
    prog = _parse("var float x = 0.0\n")
    decl = prog.body[0]
    assert isinstance(decl, VarDecl)
    assert decl.is_var is True
    assert decl.type_hint == "float"


def test_reassignment():
    prog = _parse("x := 5\n")
    assert isinstance(prog.body[0], Assignment)
    assert prog.body[0].op == ":="


def test_compound_assignment():
    prog = _parse("x += 1\n")
    stmt = prog.body[0]
    assert isinstance(stmt, Assignment)
    assert stmt.op == "+="


def test_if_else():
    prog = _parse("if x > 0\n    y = 1\nelse\n    y = 2\n")
    stmt = prog.body[0]
    assert isinstance(stmt, IfStmt)
    assert len(stmt.body) == 1
    assert stmt.else_body is not None


def test_else_if_chain():
    prog = _parse("if x > 0\n    y = 1\nelse if x < 0\n    y = 2\nelse\n    y = 3\n")
    stmt = prog.body[0]
    assert isinstance(stmt, IfStmt)
    assert len(stmt.else_body) == 1
    assert isinstance(stmt.else_body[0], IfStmt)
    inner = stmt.else_body[0]
    assert inner.else_body is not None


def test_for_loop():
    prog = _parse("for i = 0 to 10\n    x = i\n")
    stmt = prog.body[0]
    assert isinstance(stmt, ForStmt)


def test_for_loop_with_step():
    prog = _parse("for i = 0 to 10 by 2\n    x = i\n")
    stmt = prog.body[0]
    assert isinstance(stmt, ForStmt)
    assert stmt.step is not None


def test_while_loop():
    prog = _parse("while x > 0\n    x := x - 1\n")
    assert isinstance(prog.body[0], WhileStmt)


def test_switch_with_expr():
    src = 'switch maType\n    "EMA" =>\n        x = 1\n    "SMA" =>\n        x = 2\n'
    prog = _parse(src)
    stmt = prog.body[0]
    assert isinstance(stmt, SwitchStmt)
    assert stmt.expr is not None


def test_func_def_block_body():
    # The parser always uses block form for indented bodies (is_single_expr=False).
    prog = _parse("f(x, y) =>\n    x + y\n")
    stmt = prog.body[0]
    assert isinstance(stmt, FuncDef)
    assert stmt.params == ["x", "y"]
    assert stmt.is_single_expr is False


def test_func_def_multi_line():
    prog = _parse("f(x) =>\n    y = x * 2\n    y + 1\n")
    stmt = prog.body[0]
    assert isinstance(stmt, FuncDef)
    assert len(stmt.body) >= 2


def test_tuple_assign():
    prog = _parse("[a, b] = ta.macd(close, 12, 26, 9)\n")
    stmt = prog.body[0]
    assert isinstance(stmt, TupleAssign)
    assert stmt.names == ["a", "b"]


def test_strategy_decl():
    src = 'strategy("Test", overlay=true)\n'
    prog = _parse("//@version=6\n" + src)
    decl = [s for s in prog.body if isinstance(s, StrategyDecl)][0]
    assert isinstance(decl.args[0], StringLiteral)
    assert decl.args[0].value == "Test"
    assert "overlay" in decl.kwargs


def test_version_annotation():
    prog = _parse("//@version=6\nstrategy(\"T\")\n")
    assert prog.version == 6


def test_break_continue():
    prog = _parse("for i = 0 to 5\n    if i == 3\n        break\n    continue\n")
    for_stmt = prog.body[0]
    assert isinstance(for_stmt, ForStmt)


def test_for_in_simple():
    src = "for x in myArray\n    y = x + 1\n"
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    stmt = ast.body[0]
    assert isinstance(stmt, ForInStmt)
    assert stmt.var == "x"
    assert stmt.vars is None
    assert isinstance(stmt.iterable, Identifier)
    assert stmt.iterable.name == "myArray"
    assert len(stmt.body) == 1


def test_for_in_destructured():
    src = "for [idx, val] in myArray\n    y = val\n"
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    stmt = ast.body[0]
    assert isinstance(stmt, ForInStmt)
    assert stmt.vars == ["idx", "val"]
    assert stmt.var is None
    assert isinstance(stmt.iterable, Identifier)
    assert len(stmt.body) == 1


def test_udt_parse():
    src = "type MyType\n    float price = 0.0\n    int count = 0\n"
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    stmt = ast.body[0]
    assert isinstance(stmt, TypeDecl)
    assert stmt.name == "MyType"
    assert len(stmt.fields) == 2
    assert stmt.fields[0].name == "price"
    assert stmt.fields[1].name == "count"


def test_enum_parse():
    from pineforge_codegen.ast_nodes import EnumDecl
    src = "enum Direction\n    Up\n    Down\n    Sideways\n"
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    stmt = ast.body[0]
    assert isinstance(stmt, EnumDecl)
    assert stmt.name == "Direction"
    assert stmt.members == ["Up", "Down", "Sideways"]


def test_enum_parse_field_string_values():
    """TV manual: enum members may use = \"title\" for dropdown / str.tostring."""
    from pineforge_codegen.ast_nodes import EnumDecl, StringLiteral
    src = '''enum tz
    utc = "UTC"
    exch = ""
    ny = "America/New_York"
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    stmt = ast.body[0]
    assert isinstance(stmt, EnumDecl)
    assert stmt.name == "tz"
    assert stmt.members == ["utc", "exch", "ny"]
    assert isinstance(stmt.member_values["utc"], StringLiteral)
    assert stmt.member_values["utc"].value == "UTC"
    assert stmt.member_values["exch"].value == ""
    assert stmt.member_values["ny"].value == "America/New_York"


def test_parse_series_float_param_single_name():
    """series float x is one parameter named x, not (series, x)."""
    src = """f(series float src, int fast) =>
    src + fast
"""
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    fd = ast.body[0]
    assert isinstance(fd, FuncDef)
    assert fd.params == ["src", "fast"]


BASIC_STRATEGIES = sorted(glob.glob("pineforge-engine/corpus/basic/*/strategy.pine"))


@pytest.mark.parametrize("pine_file", BASIC_STRATEGIES,
                         ids=[os.path.basename(os.path.dirname(p)) for p in BASIC_STRATEGIES])
def test_parse_basic_strategy(pine_file):
    """Parse each basic strategy without errors."""
    src = open(pine_file).read()
    prog = _parse(src)
    assert isinstance(prog, Program)
    assert len(prog.body) > 0


# === Regression: UDF param qualifiers + postfix-array declarations ===

def test_udf_param_simple_qualifier_is_one_param():
    """`simple string m` is a single qualified param, not two params."""
    prog = _parse(
        "//@version=6\nstrategy(\"t\")\n"
        "ma(float s, int l, simple string m) =>\n    ta.ema(s, l)\n"
    )
    fdef = next(s for s in prog.body if isinstance(s, FuncDef))
    assert fdef.params == ["s", "l", "m"]


def test_udf_param_series_and_const_qualifiers():
    prog = _parse(
        "//@version=6\nstrategy(\"t\")\n"
        "f(series float a, const int b, simple bool c) =>\n    a\n"
    )
    fdef = next(s for s in prog.body if isinstance(s, FuncDef))
    assert fdef.params == ["a", "b", "c"]


def test_postfix_array_decl_var_keyword():
    """`var float[] x = ...` must register a VarDecl (was silently dropped)."""
    prog = _parse(
        "//@version=6\nstrategy(\"t\")\n"
        "var float[] qp = array.from(0.1, 0.2, 0.3)\n"
    )
    decl = next((s for s in prog.body if isinstance(s, VarDecl) and s.name == "qp"), None)
    assert decl is not None
    assert decl.is_var is True
    assert "array<float>" in (decl.type_hint or "")


def test_postfix_array_decl_bare():
    prog = _parse(
        "//@version=6\nstrategy(\"t\")\n"
        "int[] xs = array.new_int(3, 0)\n"
    )
    decl = next((s for s in prog.body if isinstance(s, VarDecl) and s.name == "xs"), None)
    assert decl is not None
    assert "array<int>" in (decl.type_hint or "")


def test_postfix_array_and_simple_qualifier_transpile():
    """End-to-end: both fixed forms transpile to C++ without error."""
    from pineforge_codegen import transpile
    cpp = transpile(
        "//@version=6\nstrategy(\"t\")\n"
        "ma(float s, int l, simple string m) =>\n    ta.ema(s, l)\n"
        "var float[] qp = array.from(0.1, 0.2, 0.3)\n"
        "if close > 0\n    plot(ma(qp.get(0), 10, \"EMA\"))\n"
    )
    assert "ma_cs0(double source" not in cpp  # no spurious 'simple' param split
    assert len(cpp.splitlines()) > 10

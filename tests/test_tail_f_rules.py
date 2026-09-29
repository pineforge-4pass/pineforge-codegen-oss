"""Lane TAIL-F's rules, one by one, on the emitted AST and C++.

The TradingView tapes behind them are replayed in
``tests/test_e2e_tail_f_tapes.py`` (``fixtures/tail_f_tv``):

- a ``switch`` arm written on its ``=>`` line is a comma statement list, its
  value the last statement's (``parser._parse_arm_line``);
- a library function overloaded by its parameters' qualifiers alone binds to
  the overload whose qualifiers are the weakest its arguments fit
  (``library_inline._Linker._overload``);
- the support checker visits every arm of a ``switch``, not only the default;
- a ``barmerge`` constant is a value, and a request that reads no data takes
  any gaps and lookahead;
- a declaration in a top-level block whose type the member of its name
  cannot hold is its own variable (``block_locals``);
- a fresh array binds to a callee's array parameter, and an array a call
  returns is refused there;
- a product of two C++ ``int`` operands that a ``%`` or ``/`` reads is
  64-bit, as Pine's ``int`` is (every other one keeps its spelling).
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.ast_nodes import Assignment, ExprStmt, FuncCall, SwitchStmt, VarDecl
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests._pine_libraries import library_sources

HEAD = '//@version=6\nstrategy("T", overlay = true)\n'
TAIL = 'if x > 0\n    strategy.entry("L", strategy.long)\n'


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), source=source).parse()


def _switch(source: str) -> SwitchStmt:
    stmt = _parse(source).body[-1]
    value = stmt.value if isinstance(stmt, VarDecl) else stmt
    assert isinstance(value, SwitchStmt)
    return value


def _errors(source: str, **kwargs) -> list:
    with pytest.raises(CompileError) as err:
        transpile(source, **kwargs)
    return [d for d in err.value.diagnostics if d.level == Level.ERROR]


# -- switch arms on their `=>` line ------------------------------------------

def test_an_arm_line_joins_statements_with_commas():
    switch = _switch(HEAD + 'k = 1\nx = switch k\n'
                     '    0 => runtime.error("x"), 1.5\n'
                     '    => y = k * 2, y := y + 1, y\n')
    (value, body), = switch.cases
    assert [type(s) for s in body] == [ExprStmt, ExprStmt]
    assert isinstance(body[0].expr, FuncCall)
    assert [type(s) for s in switch.default_body] == [VarDecl, Assignment, ExprStmt]


def test_a_lone_expression_arm_keeps_its_node():
    """Every arm that parsed before is the location-less ``ExprStmt`` it was."""
    switch = _switch(HEAD + 'k = 1\nx = switch k\n    0 => 1.5\n    => 2.5\n')
    for body in (switch.cases[0][1], switch.default_body):
        (stmt,) = body
        assert isinstance(stmt, ExprStmt) and stmt.loc is None


def test_a_call_before_the_next_arm_is_no_function_definition():
    """``f(a)`` followed by the next arm's ``=>`` on the next line: the
    function-definition lookahead skips line breaks, an arm never defines."""
    switch = _switch(HEAD + 'f(v) => v + 1\ng(v) => v - 1\nk = 1\n'
                     'x = switch k\n    0 => f(k)\n    => g(k)\n')
    assert isinstance(switch.cases[0][1][0].expr, FuncCall)
    assert isinstance(switch.default_body[0].expr, FuncCall)


def test_an_arm_line_compiles_as_its_block():
    cpp = transpile(HEAD + 'var int seq = 0\nk = bar_index % 3\n'
                    'int x = switch k\n'
                    '    0 => seq := seq * 10 + 1, seq := seq % 1000, seq * 2\n'
                    '    1 => seq += 7, seq\n'
                    '    => runtime.error("never"), -1\n' + TAIL)
    compile_cpp(cpp, label="arm line")


# -- library overloads by qualifier --------------------------------------------

OVERLOADS = library_sources("pftest/Overloads/1")


def test_an_overload_binds_by_its_arguments_qualifiers():
    cpp = transpile(HEAD + 'import pftest/Overloads/1 as ov\nlen = input.int(3)\n'
                    'fixed = len * 2\na = ov.pick(7)\nb = ov.pick(len)\nc = ov.pick(fixed)\n'
                    'd = ov.pick(bar_index)\ne = ov.pick(fixed + bar_index)\n'
                    'x = a + b + c + d + e\n' + TAIL, libraries=OVERLOADS)
    assert "a = Overloads_v1__pick(7);" in cpp
    assert "b = Overloads_v1__pick(len);" in cpp
    assert "c = Overloads_v1__pick(fixed);" in cpp
    assert "d = Overloads_v1__pick_2(" in cpp
    assert "e = Overloads_v1__pick_2(" in cpp
    compile_cpp(cpp, label="overloads")


def test_a_reassigned_or_var_declaration_is_a_series_argument():
    cpp = transpile(HEAD + 'import pftest/Overloads/1 as ov\nvar int n = 3\nm = 4\n'
                    'm := m + 1\na = ov.pick(n)\nb = ov.pick(m)\nx = a + b\n' + TAIL,
                    libraries=OVERLOADS)
    assert "a = Overloads_v1__pick_2(" in cpp and "b = Overloads_v1__pick_2(" in cpp


def test_overloads_that_differ_by_type_stay_refused():
    lib = ('//@version=6\nlibrary("Typed")\n'
           'export f(int n) =>\n    n + 1\nexport f(string s) =>\n    str.length(s)\n')
    (err,) = _errors(HEAD + 'import pftest/Typed/1 as t\nx = t.f(3)\n' + TAIL,
                     libraries={"pftest/Typed/1": lib})
    assert "defines 'f' 2 times (overloads)" in err.message


def test_an_overload_binds_by_its_arguments_count_and_keywords():
    lib = ('//@version=6\nlibrary("Arity")\n'
           'export f(int n) =>\n    n + 1\nexport f(int n, int m) =>\n    n * m\n')
    cpp = transpile(HEAD + 'import pftest/Arity/1 as t\na = t.f(3)\nb = t.f(3, m = 4)\n'
                    'x = a + b\n' + TAIL, libraries={"pftest/Arity/1": lib})
    assert "a = Arity_v1__f(3);" in cpp
    assert "b = Arity_v1__f_2(3, 4);" in cpp


# -- the support checker visits every switch arm ----------------------------------

def test_a_request_of_another_symbol_in_an_arm_is_lowered():
    """It kept no lowering (the checker walked the default arm alone), and the
    request contexts refused it as a chart request they could not key."""
    out = transpile_full(HEAD + 'f(simple string sym, simple string tf) =>\n'
                         '    s = str.format("{0}_X", sym)\n'
                         '    switch\n        tf == "D" => request.security(s, tf, close)\n'
                         '        => request.security(s, tf, close[1], lookahead = barmerge.lookahead_on)\n'
                         'use = input.bool(false)\nx = use ? f("BINANCE:BTCUSDT", "D") : 1.0\n' + TAIL)
    warned = [d.message for d in out["diagnostics"] if "no data is pinned" in d.message]
    assert len(warned) == 2, warned


def test_what_the_checker_finds_in_an_arm_warns():
    """The arms it never visited keep the lowering they compiled to: an
    unsupported call there transpiled silently, a text constant compiled as
    its string; each now warns."""
    out = transpile_full(HEAD + 'k = bar_index % 2\nx = switch k\n'
                         '    0 => request.seed("seed_crypto_santiment", "BTC", close)\n'
                         '    => 1.0\n' + TAIL)
    (seed,) = [d for d in out["diagnostics"] if "request.seed" in d.message]
    assert seed.level == Level.WARNING
    out = transpile_full(HEAD + 'm = close > open ? "L" : "R"\nta_ = switch m\n'
                         '    "L" => text.align_left\n    "R" => text.align_right\n'
                         'if barstate.islast\n    label.new(bar_index, close, "x", textalign = ta_)\n'
                         'x = 1\n' + TAIL)
    assert all(d.level == Level.WARNING for d in out["diagnostics"])
    assert any("text.align_left" in d.message for d in out["diagnostics"])
    compile_cpp(out["cpp"], label="text constant in an arm")


def test_a_library_keyword_binds_by_its_spelled_name():
    """A library's own keyword call binds by the parameter's spelled name,
    whichever function the inliner renamed first: ``f(n = 2)`` read the
    default 5 once ``f`` was processed, and the choice of an overload moved."""
    lib = ('//@version=6\nlibrary("Kw")\nexport f(int n = 5) =>\n    n + 1\n'
           'export g() =>\n    f(n = 2)\n')
    cpp = transpile(HEAD + 'import pftest/Kw/1 as kw\na = kw.f(1)\nc = kw.g()\nx = a + c\n' + TAIL,
                    libraries={"pftest/Kw/1": lib})
    assert "return Kw_v1__f(2);" in cpp
    ov = ('//@version=6\nlibrary("Ov")\nexport f(simple int n) =>\n    n * 10\n'
          'export f(series int n) =>\n    n * 100\nexport g() =>\n    f(n = 2)\n')
    for calls in ('c = ov.g()\nx = c\n', 'a = ov.f(1)\nc = ov.g()\nx = a + c\n',
                  'a = ov.f(1)\nb = ov.f(bar_index)\nc = ov.g()\nx = a + b + c\n'):
        cpp = transpile(HEAD + 'import pftest/Ov/1 as ov\n' + calls + TAIL,
                        libraries={"pftest/Ov/1": ov})
        assert "return Ov_v1__f(2);" in cpp, calls


# -- barmerge constants as values ----------------------------------------------------

def test_a_barmerge_constant_is_a_value():
    cpp = transpile(HEAD + 'g = input.bool(false)\n'
                    'var gs = g ? barmerge.gaps_on : barmerge.gaps_off\n'
                    'x = gs == barmerge.gaps_off ? 1 : 0\n' + TAIL)
    assert "((g) ? (1) : (0))" in cpp
    compile_cpp(cpp, label="barmerge value")


def test_a_chart_request_keeps_its_constant_gaps():
    (err,) = _errors(HEAD + 'var gs = barmerge.gaps_on\n'
                     'x = request.security(syminfo.tickerid, "D", close, gs)\n' + TAIL)
    assert "gaps must be barmerge.gaps_on or barmerge.gaps_off" in err.message


def test_a_request_of_another_symbol_with_computed_gaps_reads_no_data():
    out = transpile_full(HEAD + 'var gs = barmerge.gaps_on\nuse = input.bool(false)\n'
                         'x = use ? request.security("TVC:DXY", "60", close, gs) : 1.0\n' + TAIL)
    (warn,) = [d for d in out["diagnostics"] if "no data is pinned" in d.message]
    assert "barmerge constants" in (warn.message + (warn.hint or ""))


def test_an_unknown_barmerge_member_is_refused():
    (err,) = _errors(HEAD + 'var gs = barmerge.gaps_maybe\nx = 1\n' + TAIL)
    assert "is not a barmerge constant" in err.message


# -- block locals of different types --------------------------------------------------

def test_block_locals_of_different_types_are_their_own_variables():
    cpp = transpile(HEAD + 'var names = array.from("A", "B")\nfloat x = 0.0\n'
                    'if close > open\n    k = 0\n    x += k\n'
                    'if close < open\n    k = names.get(0)\n    x += str.length(k)\n' + TAIL)
    assert "int k = 0;" in cpp and "std::string k__pfblk1" in cpp
    compile_cpp(cpp, label="block locals")


def test_block_locals_one_member_holds_keep_their_names():
    cpp = transpile(HEAD + 'float x = 0.0\nif close > open\n    k = 0\n    x += k\n'
                    'if close < open\n    k = 1.5\n    x += k\n' + TAIL)
    assert "__pfblk" not in cpp


def test_a_nested_declaration_shadows_a_renamed_block_local():
    cpp = transpile(HEAD + 'var names = array.from("A", "B")\nfloat x = 0.0\n'
                    'if close > open\n    k = 0\n    x += k\n'
                    'if close < open\n    k = names.get(0)\n    for k = 0 to 1\n        x += k\n'
                    '    x += str.length(k)\n' + TAIL)
    compile_cpp(cpp, label="shadowed block local")


# -- arrays passed to a callee's array parameter ------------------------------------

def test_fresh_arrays_bind_to_an_array_parameter():
    cpp = transpile(HEAD + 'f(array<float> a) =>\n    a.push(1.0)\n    a.size()\n'
                    'var m = matrix.new<float>(2, 2, 0.0)\n'
                    'x = f(m.row(0)) + f(array.from(1.0)) + f(array.copy(array.from(2.0)))\n' + TAIL)
    assert cpp.count("__pf_call_arg_") >= 3
    compile_cpp(cpp, label="fresh array arguments")


def test_a_builtins_array_keeps_its_lowering():
    """``request.security_lower_tf`` is held in a member: the call compiled."""
    cpp = transpile(HEAD + 'f(float[] a) => array.size(a)\n'
                    'x = f(request.security_lower_tf(syminfo.tickerid, "1", close))\n' + TAIL)
    assert "x = f(_req_sec_lower_tf_0);" in cpp


def test_a_selection_of_a_held_and_a_fresh_array_is_refused():
    (err,) = _errors(HEAD + 'f(array<float> a) =>\n    a.push(1.0)\n    a.size()\n'
                     'var v = array.new_float(0)\n'
                     'x = f(close > open ? v : array.copy(v))\n' + TAIL)
    assert "which takes the array itself" in err.message


# -- 64-bit int products ---------------------------------------------------------------

def test_an_int_product_is_64_bit():
    cpp = transpile(HEAD + 'var int s = 42\nvar st = array.new_int(1, 42)\n'
                    's := (s * 48271) % 2147483647\n'
                    'v = (array.get(st, 0) * 48271) % 2147483647\n'
                    'x = s + v\n' + TAIL)
    assert "((int64_t)(s) * (48271))" in cpp
    assert cpp.count("(int64_t)([&](auto&& __pf_array)") == 1
    compile_cpp(cpp, label="int product")


def test_a_product_no_modulo_or_division_reads_keeps_its_spelling():
    cpp = transpile(HEAD + 'var int n = 400\nn += 1\nm = n * 7200000\nq = (n * 3) / 2\n'
                    'x = m + q\n' + TAIL)
    assert "(n * 7200000)" in cpp
    assert "((int64_t)(n) * (3))" in cpp


def test_a_float_for_in_element_keeps_its_product():
    cpp = transpile(HEAD + 'prices = array.from(1.5, 2.25, 3.75)\nfloat total = 0.0\n'
                    'for v in prices\n    total += v * 2 / 3\nx = total\n' + TAIL)
    assert "total += ((double)((v * 2)) / (double)(3));" in cpp
    cpp = transpile(HEAD + 'counts = array.from(1, 2)\nfloat total = 0.0\n'
                    'for c in counts\n    total += c * 3 / 2\nx = total\n' + TAIL)
    assert "((int64_t)(c) * (3))" in cpp


def test_a_float_or_rounded_operand_keeps_its_product():
    """``math.round(x)`` is a Pine int emitted as a double ``std::round``:
    casting it would read an na as a number."""
    cpp = transpile(HEAD + 'y = close * 2\nz = math.round(close) * 3\nx = y + z\n' + TAIL)
    products = [line for line in cpp.splitlines() if " y = " in line or " z = " in line]
    assert products and not any("(int64_t)" in line for line in products)

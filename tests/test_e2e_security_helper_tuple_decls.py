"""Tuple declarations inside ``request.security`` helpers.

A helper destructuring a tuple -- ``[m, s, h] = ta.macd(...)``,
``[_, dir] = ta.supertrend(...)``, ``[wtA, wtB] = f_wavetrend(...)`` -- was
refused with "request.security does not support multi-statement helpers with
control flow": the helper plan listed ``TupleAssign`` among the loops and
switches it cannot lower. It is a declaration, not control flow. The linear
emitter now evaluates the right side once in the requested context
(``auto _secN_f_K_tuple = ...``) and binds each named element to its own
local: a TA tuple result's field, a nested helper's ``std::get``; an element
read with history gets its helper series. A bare expression statement (an if
block's trailing value, ``lastHigh := ph`` followed by ``lastHigh``) is
admitted too, and the prepasses that size TA variants, requested-bar history
and mutable-global dependencies walk both.

TradingView's tape of ``sec2_tuple_assign`` (``fixtures/security2_tv``): a
``ta.bb`` tuple, a nested helper's tuple, ``_`` placeholders and a helper read
at ``[1]`` under ``lookahead_on``, beside the same helper on the chart -- all
265 exit Signals match.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


def test_helper_tuple_declarations_match_the_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_tuple_assign")
    exits = replay(engine, base, {"probe": Build(source("sec2_tuple_assign"))})
    tape = tape_exits("sec2_tuple_assign")
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"helper tuple declarations: {len(tape)} of {len(tape)} exit Signals equal "
          "TradingView's")


def test_helper_tuple_declaration_binds_ta_fields_and_nested_tuples():
    cpp = transpile(source("sec2_tuple_assign"))
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void _eval_security_1(")]
    tuple_temps = re.findall(r"auto (_sec0_f_bands_\d+_tuple) = ", body)
    assert len(tuple_temps) == 2, body
    # ta.bb's result struct: its fields, not std::get.
    assert re.search(rf"auto _sec0_f_bands_\d+_mid = {tuple_temps[0]}\.middle;", body)
    assert re.search(rf"auto _sec0_f_bands_\d+_lo = {tuple_temps[0]}\.lower;", body)
    # The nested helper's std::tuple.
    assert re.search(rf"auto _sec0_f_bands_\d+_hh = std::get<0>\({tuple_temps[1]}\);", body)


def test_helper_bare_expression_statement_is_a_block_value():
    src = """//@version=6
strategy("helper block value")
f() =>
    ph = ta.pivothigh(high, 2, 2)
    var float last = na
    if not na(ph)
        last := ph
        last
    last
l = request.security(syminfo.tickerid, "60", f())
if close > l
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(src)
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert "(void)" not in body  # the trailing ``last`` has no effect to keep


def test_helper_loops_lower_in_the_requested_context():
    # A loop in a helper runs on the requested bar
    # (tests/test_e2e_security_helper_loops.py, TradingView's tapes
    # te_sec_loop_*): close[i] reads the requested close i bars back.
    src = """//@version=6
strategy("helper loop")
f() =>
    float s = 0.0
    for i = 0 to 2
        s += close[i]
    s
x = request.security(syminfo.tickerid, "60", f())
plot(x)
"""
    cpp = transpile(src)
    evaluator = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert "for (int _sec0_f_" in evaluator
    assert "_sec0_hist_close[_hidx - 1]" in evaluator


@pytest.mark.parametrize("statement", ["g()", "array.push(hist, close)"])
def test_helper_call_statements_stay_refused(statement):
    # A bare call statement could mutate chart state from the requested
    # context (a collection, a drawing): only a block value is admitted.
    src = f"""//@version=6
strategy("helper call statement")
var hist = array.new<float>()
g() => close
f() =>
    x = close
    {statement}
    x
v = request.security(syminfo.tickerid, "60", f())
plot(v)
"""
    with pytest.raises(CompileError, match="may only use local declarations"):
        transpile(src)


@pytest.mark.parametrize("helpers", [
    'g() => [close, close > open ? "up" : "dn"]',
    # The element is a parameter, bound to a string argument.
    'g(s) => [close, s]',
    # The element is a callee local.
    'g() =>\n    t = close > open ? "up" : "dn"\n    [close, t]',
    # The callee's final expression is another helper's tuple.
    'h() => [close, close > open ? "up" : "dn"]\ng() => h()',
])
def test_string_tuple_element_read_with_history_is_refused(helpers):
    # A helper series holds doubles: a string element read with history is
    # refused by name instead of reaching the C++ compile.
    call = "g(close > open ? \"up\" : \"dn\")" if "g(s)" in helpers else "g()"
    src = f"""//@version=6
strategy("string element history")
{helpers}
f() =>
    [c, s] = {call}
    s[1] == "up" ? c : 0.0
v = request.security(syminfo.tickerid, "60", f())
plot(v)
"""
    with pytest.raises(CompileError, match="string element read with history"):
        transpile(src)

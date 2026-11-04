"""A request.security helper whose value is a ``switch``.

A helper of one statement, or one whose last statement is a ``switch``, is the
value of that switch, as a helper ending in an ``if`` already is (the builder
lowers a switch with one-expression arms as nested ternaries, and the
single-expression and linear helper paths both take an ``IfStmt``). It was
refused with "multi-statement helpers must end with a final expression result",
although TradingView runs the script. A ``switch`` before the final expression
stays refused, with its own message.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

HEADER = '//@version=6\nstrategy("security switch helper", initial_capital = 100000)\n'
ENTRY = 'if x > 0\n    strategy.entry("L", strategy.long)\n'


def _evaluator(cpp: str, sec_id: int = 0) -> str:
    start = cpp.index(f"void _eval_security_{sec_id}")
    body = cpp[start:]
    return body[:body.index("\n    }\n")]


def test_a_helper_that_is_one_switch_without_a_selector():
    cpp = transpile(
        HEADER
        + "choose(bool missing, int fixed) =>\n"
        "    switch\n"
        "        not missing => fixed\n"
        "        => na\n"
        'missing = input.bool(true, "Missing")\n'
        'fixed = input.int(2, "Fixed", minval = 1, maxval = 2)\n'
        "x = request.security(syminfo.tickerid, timeframe.period, choose(missing, fixed))\n"
        + ENTRY
    )
    body = _evaluator(cpp)
    assert "_req_sec_0 =" in body, body
    assert "?" in body, body


def test_a_helper_that_is_one_switch_on_a_selector():
    cpp = transpile(
        HEADER
        + "pick(int mode) =>\n"
        "    switch mode\n"
        "        1 => 10.0\n"
        "        2 => 20.0\n"
        "        => 30.0\n"
        "x = request.security(syminfo.tickerid, timeframe.period, pick(2))\n"
        + ENTRY
    )
    body = _evaluator(cpp)
    assert "_req_sec_0 =" in body, body
    assert "10.0" in body and "20.0" in body and "30.0" in body, body


def test_a_helper_ending_in_a_switch_after_a_local():
    cpp = transpile(
        HEADER
        + "pick(int mode) =>\n"
        "    base = 5.0\n"
        "    switch mode\n"
        "        1 => base + 10.0\n"
        "        => base\n"
        "x = request.security(syminfo.tickerid, timeframe.period, pick(1))\n"
        + ENTRY
    )
    body = _evaluator(cpp)
    assert "_req_sec_0 =" in body, body
    assert "10.0" in body, body


def test_a_switch_before_the_final_expression_stays_refused():
    with pytest.raises(CompileError) as raised:
        transpile(
            HEADER
            + "bad(int mode) =>\n"
            "    switch mode\n"
            "        1 => 10.0\n"
            "        => 20.0\n"
            "    close\n"
            "x = request.security(syminfo.tickerid, timeframe.period, bad(1))\n"
            + ENTRY
        )
    assert "control flow" in raised.value.diagnostics[0].message

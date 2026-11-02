"""Forms of a requested if/switch whose TradingView reply is known.

Each row cites the reply: a refusal with its code, or an acceptance with the first
entry quantities of its tape. The accepted rows only assert that translation
succeeds; the values are measured by the population and value tables, not here.
"""
from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

HEADER = (
    '//@version=6\n'
    'strategy("selection reply", currency = currency.NONE, initial_capital = 1000000)\n'
)
ORDERS = (
    'if bar_index > 6 and bar_index % 7 == 3\n'
    '    strategy.entry("L", strategy.long, qty = 1)\n'
    'if bar_index > 6 and bar_index % 7 == 5\n'
    '    strategy.close("L")\n'
)


def _refusal(source: str) -> CompileError:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    return caught.value


def test_mixed_value_and_void_arm_in_a_request_is_refused_like_tradingview():
    # TradingView CE10235: the blocks of one if return incompatible types (one
    # value arm, one void arm), line 3. The request form is refused at the
    # requested value, the same refusal as a void helper result.
    source = HEADER + (
        'chosen = if bar_index % 2 == 0\n'
        '    bar_index * 3\n'
        'else\n'
        '    strategy.close("L")\n'
        'got_v = request.security(syminfo.tickerid, timeframe.period, chosen)\n'
        'qty_v = 1 + int(nz(got_v)) % 5\n'
        'if bar_index > 6 and bar_index % 7 == 3\n'
        '    strategy.entry("L", strategy.long, qty = qty_v)\n'
    )
    (diagnostic,) = _refusal(source).diagnostics
    assert diagnostic.code == "PF-E3102"
    assert diagnostic.location.line == 7


def test_if_expression_as_a_call_argument_is_a_located_refusal():
    # TradingView CE10156 (syntax error) at 4:5: the if block cannot stand in a
    # call argument. PineForge's parser refuses it at the same position.
    source = HEADER + (
        'probe_v = math.abs(if bar_index % 2 == 0\n'
        '    bar_index * 3\n'
        'else\n'
        '    bar_index * 2)\n'
    ) + ORDERS
    (diagnostic,) = _refusal(source).diagnostics
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 5)


@pytest.mark.parametrize("body", [
    # TradingView accepts an if expression bound to a name used as a ternary
    # condition (its tape: 26 entries, first quantities 1,2,1,2,1,2,1).
    'cond_v = if bar_index % 2 == 0\n'
    '    true\n'
    'else\n'
    '    false\n'
    'probe_v = cond_v ? 1 : 2\n',
])
def test_forms_tradingview_accepts_still_translate(body):
    assert isinstance(transpile(HEADER + body + ORDERS), str)


def test_series_condition_if_choosing_a_request_timeframe_selects_per_bar():
    # Both registered literals are compared before an unmatched value stops.
    body = ('tf_v = if close > open\n'
            '    "60"\n'
            'else\n'
            '    "240"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, close)\n')
    cpp = transpile(HEADER + body + ORDERS)
    assert 'register_security_eval(0, "60", input_tf_, false, false)' in cpp
    assert 'register_security_eval(1, "240", input_tf_, false, false)' in cpp
    assert (
        '    if (_pf_selected_tf == "60") return _req_sec_0;\n'
        '    if (_pf_selected_tf == "240") return _req_sec_1;\n'
        '    _PF_ENGINE_INVARIANT("Requested timeframe matched no registered timeframe", std::logic_error);\n'
        '}())'
    ) in cpp

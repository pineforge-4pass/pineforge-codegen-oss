"""Consumed selections must not substitute a default for a void arm."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


PLAIN_SOURCE = '''//@version=6
strategy("One value arm and one void arm, plain position", currency = currency.NONE, initial_capital = 1000000)
chosen = if bar_index % 2 == 0
    bar_index * 3
else
    strategy.close("L")
qty_v = 1 + int(nz(chosen)) % 5
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = qty_v)
'''

REQUEST_SOURCE = '''//@version=6
strategy("One value arm and one void arm, requested", currency = currency.NONE, initial_capital = 1000000)
chosen = if bar_index % 2 == 0
    bar_index * 3
else
    strategy.close("L")
got_v = request.security(syminfo.tickerid, timeframe.period, chosen)
qty_v = 1 + int(nz(got_v)) % 5
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = qty_v)
'''

HEADER = '//@version=6\nstrategy("Selection branch types")\n'


def assert_located_refusal(source, code, line, col):
    with pytest.raises(CompileError) as caught:
        transpile(source)
    if code == "PF-E3102":
        assert all(d.code != "PF-E5088" for d in caught.value.diagnostics)
    errors = [d for d in caught.value.diagnostics if d.code == code]
    assert len(errors) == 1
    diagnostic = errors[0]
    assert diagnostic.location is not None
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    return diagnostic


def test_consumed_if_rejects_value_and_void_arms():
    diagnostic = assert_located_refusal(PLAIN_SOURCE, "PF-E5088", 3, 10)
    assert diagnostic.args == {"kind": "if"}
    assert "branches of this if" in diagnostic.message
    assert "one returns a value and another returns nothing" in diagnostic.message
    assert "separate statement" in diagnostic.hint


def test_consumed_switch_rejects_value_and_void_cases():
    source = HEADER + '''selected = switch bar_index % 2
    0 => bar_index * 3
    1 => strategy.cancel("L")
qty = nz(selected)
'''
    diagnostic = assert_located_refusal(source, "PF-E5088", 3, 12)
    assert diagnostic.args == {"kind": "switch"}
    assert "branches of this switch" in diagnostic.message


def test_reassignment_consumes_the_selection_value():
    source = HEADER + '''float selected = 0
selected := if close > open
    strategy.cancel("L")
else
    7
'''
    diagnostic = assert_located_refusal(source, "PF-E5088", 4, 13)
    assert diagnostic.args == {"kind": "if"}


@pytest.mark.parametrize("terminal", (
    'strategy.entry("L", strategy.long)',
    'strategy.cancel("L")',
    "act()",
))
def test_void_calls_include_user_function_returns(terminal):
    source = HEADER + '''act() =>
    strategy.close("L")
selected = if close > open
    7
else
    ''' + terminal + "\nqty = nz(selected)\n"
    diagnostic = assert_located_refusal(source, "PF-E5088", 5, 12)
    assert diagnostic.args == {"kind": "if"}


@pytest.mark.parametrize("use", ("selected = select_value()", "plot(select_value())"))
def test_consumed_helper_return_checks_its_selection(use):
    source = HEADER + '''select_value() =>
    if close > open
        7
    else
        strategy.close("L")
''' + use + "\n"
    diagnostic = assert_located_refusal(source, "PF-E5088", 4, 5)
    assert diagnostic.args == {"kind": "if"}


@pytest.mark.parametrize("body", (
    '''if close > open
    7
else
    strategy.close("L")
''',
    '''switch
    close > open => 7
    => strategy.close("L")
''',
    '''selected = if close > open
    7
qty = nz(selected)
''',
    '''selected = if close > open
    7
else
    2.5
qty = selected
''',
    '''selected = if close > open
    strategy.close("L")
else
    strategy.cancel("L")
''',
    '''selected = switch
    close > open => strategy.close("L")
    => strategy.cancel("L")
''',
    '''selected = switch
    close > open => 7
    => 2.5
qty = selected
''',
    '''selected = switch
    close > open => 7
qty = nz(selected)
''',
    '''selected = if close > open
    7
else
    na
qty = nz(selected)
''',
    '''select_value() =>
    if close > open
        7
    else
        2.5
selected = select_value()
''',
    '''select_value() =>
    if close > open
        7
    else
        strategy.close("L")
select_value()
''',
    '''select_value() =>
    if close > open
        7
    else
        strategy.close("L")
    2.5
selected = select_value()
''',
))
def test_compatible_or_unconsumed_selections_translate(body):
    assert transpile(HEADER + body)


def test_requested_selection_keeps_its_existing_refusal():
    diagnostic = assert_located_refusal(REQUEST_SOURCE, "PF-E3102", 7, 62)
    assert diagnostic.args == {}

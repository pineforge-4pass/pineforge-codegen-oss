"""A chart-context request of a direction constant is not a series argument.

TradingView runs ``chosen = request.security(syminfo.tickerid, timeframe.period,
strategy.direction.long)`` followed by ``strategy.risk.allow_entry_in(chosen)``
(a probe's tape: every entry is made), although the qualifier table declares
allow_entry_in's parameter simple and every request was typed series; the
script was refused with "Cannot call 'strategy.risk.allow_entry_in' with a
series argument for simple parameter 'value'.".

Only the measured shape is accepted: the request goes through the chart's own
symbol and timeframe and its expression is a direction constant. A request of
another symbol or timeframe, or of any other expression, was not measured and
keeps the refusal that TradingView's own series forms are measured to get
(tests/test_allow_entry_in_qualifier.py).
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

HEADER = '//@version=6\nstrategy("request const direction", initial_capital = 100000)\n'
LONG = "strategy.direction.long"
ALL = "strategy.direction.all"
CALL = "strategy.risk.allow_entry_in"
ENTRY = 'if bar_index > 6 and bar_index % 7 == 3\n    strategy.entry("L", strategy.long)\n'
MESSAGE = "series argument for simple parameter 'value'"


def test_a_chart_context_request_of_a_constant_is_accepted():
    cpp = transpile(
        HEADER
        + f"chosen = request.security(syminfo.tickerid, timeframe.period, {LONG})\n"
        f"{CALL}(chosen)\n" + ENTRY
    )
    assert "set_pine_risk_direction(" in cpp


def test_the_keyword_forms_of_the_same_request_are_accepted():
    cpp = transpile(
        HEADER
        + "chosen = request.security(symbol = syminfo.tickerid, timeframe = timeframe.period, "
        f"expression = {LONG})\n{CALL}(value = chosen)\n" + ENTRY
    )
    assert "set_pine_risk_direction(" in cpp


@pytest.mark.parametrize(
    "request_call",
    [
        pytest.param(
            f"request.security(syminfo.tickerid, timeframe.period, close > open ? {LONG} : {ALL})",
            id="series-expression",
        ),
        pytest.param(
            f'request.security(syminfo.tickerid, "D", {LONG})',
            id="other-timeframe",
        ),
        pytest.param(
            f'request.security("BINANCE:ETHUSDT", timeframe.period, {LONG})',
            id="other-symbol",
        ),
        pytest.param(
            'request.security(syminfo.tickerid, timeframe.period, "long")',
            id="other-constant",
        ),
    ],
)
def test_every_other_request_stays_a_series_argument(request_call):
    with pytest.raises(CompileError) as raised:
        transpile(HEADER + f"chosen = {request_call}\n{CALL}(chosen)\n" + ENTRY)
    messages = [diagnostic.message for diagnostic in raised.value.diagnostics]
    assert any(MESSAGE in message for message in messages), messages

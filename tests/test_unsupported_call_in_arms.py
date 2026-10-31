"""Unimplemented calls keep their located error inside value selections."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


_HEADER = '//@version=6\nstrategy("Unsupported calls")\n'


@pytest.mark.parametrize(
    "body, phase, code, line, col",
    [
        pytest.param(
            'selected = switch 0\n'
            '    0 => timeframe.from_seconds(60)\n'
            '    => "15"\n',
            "ANALYZER", "PF-E1035", 4, 32,
            # Nine characters precede the 22-character callee on line 4.
            id="switch-arm",
        ),
        pytest.param(
            'selected = switch 0\n'
            '    0 =>\n'
            '        text = if close > open\n'
            '            timeframe.from_seconds(60)\n'
            '        else\n'
            '            "15"\n'
            '        text\n'
            '    => "15"\n',
            "ANALYZER", "PF-E1035", 6, 35,
            # Twelve spaces and the 22-character callee precede '(' on line 6.
            id="if-value-in-switch",
        ),
        pytest.param(
            'selected = switch 0\n'
            '    0 =>\n'
            '        text = if close > open\n'
            '            "15"\n'
            '        else\n'
            '            timeframe.from_seconds(60)\n'
            '        text\n'
            '    => "15"\n',
            "ANALYZER", "PF-E1035", 8, 35,
            # The else value has the same 12-space prefix on line 8.
            id="else-value-in-switch",
        ),
        pytest.param(
            'selected = switch 0\n'
            '    0 =>\n'
            '        text = switch 1\n'
            '            1 => timeframe.from_seconds(60)\n'
            '            => "15"\n'
            '        text\n'
            '    => "15"\n',
            "ANALYZER", "PF-E1035", 6, 40,
            # Twelve spaces, '1 => ' and the callee place '(' at column 40.
            id="nested-switch-value",
        ),
        pytest.param(
            'for index = 0 to 1\n'
            '    selected = switch index\n'
            '        0 => timeframe.from_seconds(60)\n'
            '        => "15"\n',
            "ANALYZER", "PF-E1035", 5, 36,
            # Eight spaces, '0 => ' and the callee place '(' at column 36.
            id="switch-value-in-loop",
        ),
        pytest.param(
            'selected = switch 0\n'
            '    0 => str.length(timeframe.from_seconds(60))\n'
            '    => 0\n',
            "ANALYZER", "PF-E1035", 4, 43,
            # Nine prefix characters plus 'str.length(' add 20 before the callee.
            id="call-argument-in-switch",
        ),
    ],
)
def test_unimplemented_call_in_value_arm_is_an_error(body, phase, code, line, col):
    with pytest.raises(CompileError) as caught:
        transpile(_HEADER + body, filename="unsupported_calls.pine")

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level.value == "error"
    assert (diagnostic.phase.value, diagnostic.code) == (phase, code)
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    assert diagnostic.location.file == "unsupported_calls.pine"
    assert diagnostic.message == "timeframe.from_seconds(...) is not implemented yet."


@pytest.mark.parametrize(
    "body, phase, code, line, col",
    [
        pytest.param(
            "tf = timeframe.from_seconds(900)\n",
            "ANALYZER", "PF-E1035", 3, 28,
            # 'tf = ' has five characters; '(' follows the 22-character callee.
            id="plain-declaration",
        ),
        pytest.param(
            'selected = if close > open\n'
            '    timeframe.from_seconds(60)\n'
            'else\n'
            '    "15"\n',
            "ANALYZER", "PF-E1035", 4, 27,
            # Four spaces and the callee place '(' at column 27 on line 4.
            id="plain-if-value",
        ),
        pytest.param(
            'selected = if close > open\n'
            '    "15"\n'
            'else\n'
            '    timeframe.from_seconds(60)\n',
            "ANALYZER", "PF-E1035", 6, 27,
            # Four spaces and the callee place '(' at column 27 on line 6.
            id="plain-else-value",
        ),
        pytest.param(
            "for index = 0 to 1\n"
            "    selected = timeframe.from_seconds(60)\n",
            "ANALYZER", "PF-E1035", 4, 38,
            # Four spaces plus 'selected = ' add 15 characters before the callee.
            id="plain-loop-body",
        ),
        pytest.param(
            "selected = str.length(timeframe.from_seconds(60))\n",
            "ANALYZER", "PF-E1035", 3, 45,
            # 'selected = str.length(' has 22 characters before the callee.
            id="plain-call-argument",
        ),
    ],
)
def test_existing_unimplemented_call_diagnostic_is_unchanged(body, phase, code, line, col):
    with pytest.raises(CompileError) as caught:
        transpile(_HEADER + body)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert (
        diagnostic.level.value, diagnostic.phase.value, diagnostic.code,
        diagnostic.location.line, diagnostic.location.col,
    ) == ("error", phase, code, line, col)


def test_switch_strategy_preserves_other_diagnostics():
    source = '''//@version=6
strategy("Unsupported calls", overlay = true, initial_capital = 1000000, pyramiding = 2, process_orders_on_close = false)
cycleIndex = int(math.floor(bar_index / 20))
flip = cycleIndex % 2 == 0
selected = switch cycleIndex % 2
    0 => timeframe.from_seconds(60)
    => "15"
signalValue = str.length(selected)
tradeQty = na(signalValue) ? 0.75 : (signalValue > 0 ? 1.25 : signalValue < 0 ? 2.5 : 3.75) + math.abs(signalValue) / (1.0 + math.abs(signalValue))
if bar_index % 20 == 5
    strategy.entry("entry", strategy.long, qty = tradeQty)
if bar_index % 20 == 15
    strategy.close("entry", comment = "scheduled")
'''
    with pytest.raises(CompileError) as caught:
        transpile(source)

    assert [
        (d.level.value, d.phase.value, d.code, d.location.line, d.location.col)
        for d in caught.value.diagnostics
    ] == [
        # 'cycleIndex = int(math.floor(' has 28 characters before bar_index.
        ("warning", "ANALYZER", "PF-W1067", 3, 29),
        # Four spaces, '0 => ' and the callee put '(' at column 32.
        ("error", "ANALYZER", "PF-E1035", 6, 32),
        # 'if ' puts bar_index at column 4 on line 10.
        ("warning", "ANALYZER", "PF-W1067", 10, 4),
        # The second 'if ' puts bar_index at column 4 on line 12.
        ("warning", "ANALYZER", "PF-W1067", 12, 4),
    ]

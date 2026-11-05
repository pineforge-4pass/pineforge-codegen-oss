"""A refused series TA length says whether TradingView accepts the call.

PineForge sizes a ta.* indicator buffer from a length that is constant or
derived from an input. A series length (a loop counter, a value computed per
bar) is refused. For the ta.* functions whose series length TradingView
compiles, the refusal names the Pine function and says that TradingView
accepts the call; for the functions where TradingView refuses a series length
too (ta.ema, ta.rsi, ...) it keeps the plain wording, which must not claim
that TradingView accepts them.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

ACCEPTED_CODE = "PF-E5092"
REFUSED_TOO_CODE = "PF-E5009"
HINT = "Use a literal, an input.*() value, or arithmetic over those for TA lengths."
REASON = (
    "it is neither a compile-time constant nor derived from an input, so "
    "PineForge cannot size the indicator buffer."
)


def _loop_source(call: str) -> str:
    return (
        '//@version=6\nstrategy("Series length")\n'
        'float total = 0.0\n'
        'for i = 2 to 4\n'
        f'    total += {call}\n'
        'plot(total)\n'
    )


def _bar_source(call: str) -> str:
    return (
        '//@version=6\nstrategy("Series length")\n'
        'n = bar_index % 7 + 1\n'
        f'float out = {call}\n'
        'plot(out)\n'
    )


def _refusal(source: str):
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.hint == HINT
    return diagnostic


@pytest.mark.parametrize("source, argument, name", [
    (_loop_source("ta.percentile_nearest_rank(close, i, 50)"), "i",
     "ta.percentile_nearest_rank"),
    (_loop_source("ta.percentile_linear_interpolation(close, i, 50)"), "i",
     "ta.percentile_linear_interpolation"),
    (_bar_source("ta.sma(close, n)"), "n", "ta.sma"),
    (_bar_source("ta.wma(close, n)"), "n", "ta.wma"),
])
def test_a_series_length_tradingview_compiles_says_so(source, argument, name):
    diagnostic = _refusal(source)
    assert diagnostic.code == ACCEPTED_CODE
    assert diagnostic.message == (
        f"Unsupported TA constructor length '{argument}' for {name}: {REASON} "
        f"TradingView accepts a series length for {name}(); "
        "PineForge does not support it yet."
    )
    expected_line = next(
        number for number, line in enumerate(source.splitlines(), start=1)
        if f"{name}(" in line
    )
    assert diagnostic.location.line == expected_line


@pytest.mark.parametrize("call, class_name", [
    ("ta.ema(close, n)", "ta::EMA"),
    ("ta.rsi(close, n)", "ta::RSI"),
])
def test_a_series_length_tradingview_refuses_too_keeps_the_plain_wording(call, class_name):
    diagnostic = _refusal(_bar_source(call))
    assert diagnostic.code == REFUSED_TOO_CODE
    assert diagnostic.message == (
        f"Unsupported TA constructor length 'n' for {class_name}: {REASON}")
    assert "TradingView accepts" not in diagnostic.message

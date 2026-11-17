"""A refused series TA length says that TradingView accepts the call only where that is recorded.

PineForge sizes a ta.* indicator buffer from a length that is constant or
derived from an input. A series length (a loop counter, a value computed per
bar) is refused for functions that still need a constructor-sized buffer.
ta.percentile_nearest_rank and ta.sma lower to their engine series classes.
The supported SMA call no longer emits its former PF-E5092 refusal.
Other unsupported functions keep the plain wording, with the C++ class name, which
makes no claim about TradingView: the functions where TradingView refuses a
series length too (ta.ema, ta.rsi, ...), and the ones with no recorded result
(ta.wma, ta.percentile_linear_interpolation, ta.pivothigh, ...). A function
joins the first group only with its own recorded result.
"""

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp

ACCEPTED_CODE = "PF-E5092"
PLAIN_CODE = "PF-E5009"
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
    (_bar_source("ta.sma(close, n)"), "n", "ta.sma"),
])
def test_supported_series_length_does_not_emit_the_former_refusal(source, argument, name):
    result = transpile_full(source)
    assert all(d.code != ACCEPTED_CODE for d in result["diagnostics"])
    assert "_PFSeriesSma _ta_sma_" in result["cpp"]
    assert f"ta_number({argument})" in result["cpp"]
    compile_cpp(result["cpp"], label=f"supported {name} series length")


@pytest.mark.parametrize("source, argument, class_name", [
    # TradingView refuses a series length for these too.
    (_bar_source("ta.ema(close, n)"), "n", "ta::EMA"),
    (_bar_source("ta.rsi(close, n)"), "n", "ta::RSI"),
    # No recorded TradingView result either way: no claim.
    (_bar_source("ta.wma(close, n)"), "n", "ta::WMA"),
    (_bar_source("ta.vwma(close, n)"), "n", "ta::VWMA"),
    (_bar_source("ta.stdev(close, n)"), "n", "ta::StdDev"),
    (_bar_source("ta.cci(close, n)"), "n", "ta::CCI"),
    (_loop_source("ta.percentile_linear_interpolation(close, i, 50)"), "i",
     "ta::PercentileLinearInterpolation"),
], ids=["ema", "rsi", "wma", "vwma", "stdev", "cci", "percentile_linear_interpolation"])
def test_a_function_without_a_recorded_result_keeps_the_plain_wording(
        source, argument, class_name):
    diagnostic = _refusal(source)
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message == (
        f"Unsupported TA constructor length '{argument}' for {class_name}: {REASON}")
    assert "TradingView accepts" not in diagnostic.message

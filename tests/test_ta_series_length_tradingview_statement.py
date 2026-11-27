"""A refused series TA length says that TradingView accepts the call only where that is recorded.

PineForge sizes a ta.* indicator buffer from a length that is constant or
derived from an input. A series length (a loop counter, a value computed per
bar) is refused for functions that still need a constructor-sized buffer.
ta.percentile_nearest_rank lowers to its engine series class, and so does a
chart ta.sma whose length the chart computes per bar: the first test below
runs it and reads each bar's mean. Where a ta.sma series length is still
refused (a request.security copy, a length a callable receives, one that reads
a script-level var and is not shown to vary by bar;
tests/test_ta_series_sma_binding.py and
tests/test_codegen_ta_stable_var_literal.py), the refusal names the Pine
function and says that TradingView accepts the call: ta.sma has a RECORDED
TradingView result that a series length compiles (the engine's note on its
TradingView compile probes).
Every other function keeps the plain wording, with the C++ class name, which
makes no claim about TradingView: the functions where TradingView refuses a
series length too (ta.ema, ta.rsi, ...), and the ones with no recorded result
(ta.wma, ta.percentile_linear_interpolation, ...). A function
joins the first group only with its own recorded result. ta.pivothigh and
ta.pivotlow no longer reach a refusal: a series leftbars or rightbars is read
on every call (tests/test_codegen_ta_dynamic_length.py).
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _series_sma_oracle as oracle
from tests._compile import run_emitted_tu

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


def _per_bar(cpp: str, members, label: str):
    return oracle.per_bar_rows(
        run_emitted_tu(cpp, oracle.per_bar_driver(members), opt="-O0", label=label),
        len(members))


# ``n = bar_index % 7 + 1`` is 1 .. 7, 1 .. 7, 1 .. 4 over the eighteen bars
# of tests/_series_sma_oracle.py. Bar b has b + 1 closes, never fewer than n,
# so no bar is na. A row is (n, the sum of the last n closes): the first seven
# windows start at bar 0, the next seven at bar 7 and the last four at bar 14.
CHART_WINDOWS = (
    (1, 10), (2, 22), (3, 33), (4, 48), (5, 62), (6, 80), (7, 93),
    (1, 17), (2, 37), (3, 53), (4, 75), (5, 94), (6, 119), (7, 140),
    (1, 24), (2, 52), (3, 75), (4, 102),
)


def test_a_chart_series_sma_length_averages_each_bars_own_window():
    # The script bytes of the former refusal case, unchanged.
    source = _bar_source("ta.sma(close, n)")
    # The table first, against constant-length calls on the same bars.
    reference = _per_bar(
        transpile(oracle.EVERY_BAR_REFERENCE), oracle.EVERY_BAR_MEMBERS,
        "constant_length_reference")
    oracle.assert_per_bar(
        oracle.scheduled(reference, CHART_WINDOWS), CHART_WINDOWS,
        "ta.sma(close, <constant n>)")
    observed = _per_bar(transpile(source), ("out",), "chart_series_sma")
    oracle.assert_per_bar(
        oracle.column(observed), CHART_WINDOWS, "ta.sma(close, n)")


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

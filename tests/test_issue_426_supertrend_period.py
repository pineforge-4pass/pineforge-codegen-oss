"""Supertrend accepts a series factor, but its ATR period must be simple."""

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation
from tests import _series_sma_oracle as feed
from tests._compile import run_emitted_tu


FILENAME = "supertrend-period.pine"
HEADER = '//@version=6\nstrategy("Supertrend period")\n'
IF_WRITE = """p = 3
chosen = if close > open
    p := p + 1
else
    p
"""
SWITCH_WRITE = """var int p = 3
chosen = switch
    close > open => p := p + 1
    => p
"""
FACTOR = "mult = (close - open + 6.0) / 4.0\n"


def _native_rows(body, members, inputs=()):
    cpp = transpile(HEADER + body, filename=FILENAME)
    output = run_emitted_tu(
        cpp, feed.per_bar_driver(members, inputs), opt="-O0", label=FILENAME,
    )
    return feed.per_bar_rows(output, len(members))


@pytest.mark.parametrize("selection,persistent", [
    pytest.param(IF_WRITE, False, id="plain-if"),
    pytest.param(SWITCH_WRITE, True, id="persistent-switch"),
])
def test_period_reads_after_selection(selection, persistent):
    # The same prefix as the refusal: read both the selection's value and the
    # binding passed to atrPeriod. A plain binding restarts on every bar.
    rows = _native_rows(selection + "afterWrite = p\n", ("chosen", "afterWrite"))
    p = 3
    for bar, (row, close, opening) in enumerate(zip(rows, feed.CLOSES, feed.OPENS, strict=True)):
        p = (p if persistent else 3) + (close > opening)
        assert row == (p, p), bar
    assert len({read for _, read in rows}) > 1


def test_legal_periods_and_series_factor():
    # "1" is the driver's one-minute chart. The chart period is simple
    # (non-input); the factor is series even though only its first value binds.
    body = FACTOR + """chartPeriod = str.length(timeframe.period) + 2
setting = input.int(3, "Period")
[fast, fastDir] = ta.supertrend(1.75, 3)
[slow, slowDir] = ta.supertrend(1.75, 6)
[alternate, _] = ta.supertrend(0.875, 3)
[fromInput, inputDir] = ta.supertrend(1.75, setting)
[fromFactor, factorDir] = ta.supertrend(atrPeriod=chartPeriod, factor=mult)
[fromHalf, _] = ta.supertrend(mult / 2.0, chartPeriod)
"""
    members = ("mult", "chartPeriod", "setting", "fast", "fastDir", "slow", "slowDir",
               "alternate", "fromInput", "inputDir", "fromFactor", "factorDir", "fromHalf")
    rows = _native_rows(body, members, (("Period", 6),))
    factors = [(close - opening + 6) / 4 for close, opening in zip(feed.CLOSES, feed.OPENS)]
    assert feed.column(rows) == factors
    assert factors[0] == 1.75 and len(set(factors)) > 1
    for bar, row in enumerate(rows):
        _, period, setting, fast, fd, slow, sd, alternate, by_input, di, by_factor, df, half = row
        assert (period, setting) == (3, 6), bar
        assert (by_input, di) == pytest.approx((slow, sd), nan_ok=True), bar
        # TradingView binds a legal series factor on this site's first execution.
        assert (by_factor, df) == pytest.approx((fast, fd), nan_ok=True), bar
        assert half == pytest.approx(alternate, nan_ok=True), bar

    # Neither check can pass on an ignored setting, identical reference
    # series, or an empty / all-na warm-up trace.
    fast, slow, alternate = (feed.column(rows, i)[6:] for i in (3, 5, 7))
    assert all(math.isfinite(v) for line in (fast, slow, alternate) for v in line)
    assert len(set(fast)) > 1 and len(set(slow)) > 1
    assert fast != pytest.approx(slow)
    assert fast != pytest.approx(alternate)


@pytest.mark.parametrize("prefix,arguments", [
    pytest.param(IF_WRITE, "2.0, p", id="if-positional"),
    # Both operands vary. Binding the first written keyword by position, or
    # blaming the first dynamic constructor slot, would diagnose the factor.
    pytest.param(SWITCH_WRITE + FACTOR, "atrPeriod=p, factor=mult", id="switch-named"),
])
def test_refuses_series_atr_period(prefix, arguments):
    call = f"[band, direction] = ta.supertrend({arguments})"
    source = HEADER + prefix + call + "\nplot(band)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=FILENAME)

    diagnostic, = caught.value.diagnostics
    assert (diagnostic.level, diagnostic.phase, diagnostic.code) == (
        Level.ERROR, Phase.CODEGEN, "PF-E5009",
    )
    assert diagnostic.args == {"a": "p", "class_name": "ta::Supertrend"}
    # The registered call's span is its opening parenthesis.
    line = source.splitlines().index(call) + 1
    col = call.index("(") + 1
    assert diagnostic.location == SourceLocation(FILENAME, line, col, col + 1)

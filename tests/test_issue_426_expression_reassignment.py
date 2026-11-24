"""A write in a selection value changes subsequent reads of the same binding."""

import pytest

from pineforge_codegen import transpile
from tests import _series_sma_oracle as bars
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("expression reassignment")\n'
IF_WRITE = "if close > open\n    p := p + 1\n"


def _values(body, member):
    output = run_emitted_tu(
        transpile(HEADER + body), bars.per_bar_driver((member,)),
        opt="-O0", label="issue-426-" + member,
    )
    return bars.column(bars.per_bar_rows(output, 1))


@pytest.mark.parametrize("write", [
    pytest.param("x = " + IF_WRITE, id="if"),
    pytest.param("x = 0\nx := switch\n    close > open =>\n        p := p + 1\n",
                 id="switch-rhs"),
])
def test_plain_read_after_write(write):
    actual = _values("p = 5\n" + write + "out = p\n", "out")
    assert actual == [6 if c > o else 5 for c, o in zip(bars.CLOSES, bars.OPENS)]


@pytest.mark.parametrize("init", ["5", "timeframe.in_seconds()"], ids=["literal", "setup"])
def test_persistent_length_statement_control(init):
    # Both initializers start at five on this five-second chart.
    driver = bars.per_bar_driver(("z",)).replace(
        'count, "1", "1"', 'count, "5S", "5S"').replace("i * 60000LL", "i * 5000LL")
    body = f"var int p = {init}\n" + IF_WRITE + "z = ta.sma(close, p)\n"
    output = run_emitted_tu(transpile(HEADER + body), driver,
                            opt="-O0", label="issue-426-statement")
    length = 5
    windows = []
    for count, (c, o) in enumerate(zip(bars.CLOSES, bars.OPENS), 1):
        length += int(c > o)
        windows.append((length, sum(bars.CLOSES[count-length:count])
                        if count >= length else None))
    bars.assert_per_bar(bars.column(bars.per_bar_rows(output, 1)), windows, init)


def test_mixed_writes_use_the_current_window():
    # On this minute feed the stable statement alone implies length five.
    # The expression makes it six on each rising bar.
    values = _values(
        "p = 5\nif timeframe.isdaily\n    p := 6\n"
        + "x = " + IF_WRITE + "z = ta.sma(close, p)\n", "z",
    )
    windows = []
    for count, (c, o) in enumerate(zip(bars.CLOSES, bars.OPENS), start=1):
        length = 6 if c > o else 5
        total = sum(bars.CLOSES[count - length:count]) if count >= length else None
        windows.append((length, total))
    bars.assert_per_bar(values, windows, "mixed statement and expression")


def test_callable_shadow_leaves_global_length_constant():
    # The function's p is declared before the selection. Its write cannot
    # invalidate the unrelated global p used to size the EMA constructor.
    values = _values(
        "p = 5\n"
        "localValue() =>\n"
        "    int p = 20\n"
        "    x = if close > open\n"
        "        p := p + 1\n"
        "    p\n"
        "out = localValue()\n"
        "z = ta.ema(close, p)\n", "out",
    )
    assert values == [21 if c > o else 20 for c, o in zip(bars.CLOSES, bars.OPENS)]

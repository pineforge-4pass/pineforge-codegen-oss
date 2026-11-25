"""A skipped read must not remove a completed bar from a requested global's history."""

import math

import pytest

from pineforge_codegen import transpile
from tests.test_security_helper_arg_history import (
    _assert_completed,
    _body_samples,
    _history,
    _run_requested,
)


def _requested_rows(binding, read):
    # The native runner takes strategies. Keep the reporter's request and
    # make its result observable to a trade as well as to the plot.
    source = f"""//@version=6
strategy("History read after skipped reads")
{binding}
h() => bar_index % 3 != 1 ? {read} : na
v = request.security(syminfo.tickerid, "60", h())
plot(v, "requested spread[1]")
if v > 0
    strategy.entry("Long", strategy.long)
"""
    return _run_requested(transpile(source))["chart15"]


@pytest.fixture(scope="module")
def previous_bar_control():
    # Lag the hourly close-open samples before masking the skipped reads.
    expected = [
        previous if hour % 3 != 1 else math.nan
        for hour, previous in enumerate(_history(_body_samples(), 1))
    ]
    _assert_completed(_requested_rows("", "close[1] - open[1]"), expected)
    return expected


@pytest.mark.parametrize("binding", [
    pytest.param("spread = close - open", id="direct"),
    pytest.param("f() => close - open\nspread = f()", id="helper"),
])
def test_previous_requested_bar_after_skipped_read(binding, previous_bar_control):
    _assert_completed(_requested_rows(binding, "spread[1]"), previous_bar_control)

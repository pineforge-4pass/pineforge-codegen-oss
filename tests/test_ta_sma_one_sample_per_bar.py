"""A ``ta.sma`` call keeps one sample per bar: the input of the bar's last call.

A loop can run one ``ta.sma`` call several times on a bar. TradingView keeps
one sample per bar for that call, the input of the bar's LAST call, so its
window holds the last n bars' samples (issue #502). The generated code used
to add every call's input to the window, which then held the last n CALL
inputs, across bars.

The bars are invented: four closes, 10, 20, 30 and 40. A loop calls
``ta.sma(close + i, 3)`` for ``i`` = 0 and 1 on each of them, so the last
call's inputs are 11, 21, 31 and 41. One sample per bar answers na on bars 0
and 1 (fewer than three bars), (11 + 21 + 31) / 3 = 21 on bar 2 and
(21 + 31 + 41) / 3 = 31 on bar 3. The last three call inputs answer
(11 + 20 + 21) / 3 on bar 1, (21 + 30 + 31) / 3 on bar 2 and
(31 + 40 + 41) / 3 = 37.33 on bar 3.

The driver and its row parser are those of ``tests/_series_sma_oracle.py``.
Every expected value is written here by hand from the closes; none is read
from the C++ under test.
"""

from __future__ import annotations

import math
import re

import pytest

from pineforge_codegen import transpile
from tests import _series_sma_oracle as oracle
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("ta.sma one sample per bar")\n'
CLOSES = (10, 20, 30, 40)

# One ``ta.sma`` call that a loop runs more than once on a bar, in each
# spelling the generator lowers apart: a reassignment in the loop body (the
# issue's reproduction), a declaration in the loop body, and a call written in
# a function the loop calls. The last one varies the number of calls, 1, 2, 3
# and 1 on the four bars: the last inputs are then 10, 21, 32 and 40, which
# add up to the same 63 on bar 2 and 93 on bar 3.
SEVERAL_CALLS_PER_BAR = {
    "reassignment": (
        "float z = na\n"
        "for i = 0 to 1\n"
        "    z := ta.sma(close + i, 3)\n"
    ),
    "declaration": (
        "float z = na\n"
        "for i = 0 to 1\n"
        "    s = ta.sma(close + i, 3)\n"
        "    z := s\n"
    ),
    "function": (
        "f(x) => ta.sma(x, 3)\n"
        "float z = na\n"
        "for i = 0 to 1\n"
        "    z := f(close + i)\n"
    ),
    "varying-count": (
        "float z = na\n"
        "k = bar_index % 3 + 1\n"
        "for i = 0 to k - 1\n"
        "    z := ta.sma(close + i, 3)\n"
    ),
}
# The value the bar's last call leaves in ``z``: None where it is na.
LAST_CALL_PER_BAR = (None, None, 21.0, 31.0)

# One call per bar: at the top level, and in a loop of a single pass, which
# takes the step of a call a loop repeats and must answer as the first does.
ONE_CALL_PER_BAR = {
    "top-level": "z = ta.sma(close, 3)\n",
    "single-pass-loop": (
        "float z = na\n"
        "for i = 0 to 0\n"
        "    z := ta.sma(close + i, 3)\n"
    ),
}
# (10 + 20 + 30) / 3 and (20 + 30 + 40) / 3.
ONE_CALL_VALUES = (None, None, 20.0, 30.0)

# The marker a repeated call's member is declared with, and that member.
MARKER = re.compile(r"^\s*int (_pf_ta_bar(_ta_sma_\w+)) = -1;$", re.M)


def _per_bar(source: str, label: str) -> list[float]:
    """``z`` on every bar: one run per bar, each from a fresh strategy."""
    output = run_emitted_tu(
        transpile(HEADER + source),
        oracle.per_bar_driver(("z",), closes=CLOSES), opt="-O0", label=label)
    return oracle.column(oracle.per_bar_rows(output, 1, bars=len(CLOSES)))


def _assert_bars(actual, expected, what: str) -> None:
    assert len(actual) == len(expected), (what, actual)
    for bar, (value, want) in enumerate(zip(actual, expected)):
        if want is None:
            assert math.isnan(value), (
                f"{what}: bar {bar} is {value!r}; na is expected")
        else:
            assert abs(value - want) <= 1e-9, (
                f"{what}: bar {bar} is {value!r}; {want} is expected")


@pytest.mark.parametrize("case", list(SEVERAL_CALLS_PER_BAR))
def test_a_call_a_loop_repeats_opens_one_sample_per_bar(case: str) -> None:
    # The shape of the emitted step, with no compiler: the first call on a
    # bar takes compute() and sets the member's bar marker, and every later
    # call on that bar takes recompute(), which replaces the bar's sample.
    cpp = transpile(HEADER + SEVERAL_CALLS_PER_BAR[case])

    markers = MARKER.findall(cpp)
    assert len(markers) == 1, markers
    marker, member = markers[0]
    assert (
        f"(history_advances_new_bar() && {marker} != bar_index_ "
        f"? ({marker} = bar_index_, {member}.compute("
    ) in cpp
    # No pass of the loop steps the member by the new-bar flag alone.
    assert f"(history_advances_new_bar() ? {member}.compute(" not in cpp


@pytest.mark.parametrize("case", list(SEVERAL_CALLS_PER_BAR))
def test_several_calls_on_a_bar_keep_the_last_calls_input(case: str) -> None:
    actual = _per_bar(SEVERAL_CALLS_PER_BAR[case], f"sma-several-calls-{case}")

    _assert_bars(actual, LAST_CALL_PER_BAR, case)


@pytest.mark.parametrize("case", list(ONE_CALL_PER_BAR))
def test_one_call_on_a_bar_answers_the_mean_of_the_last_three_closes(case: str) -> None:
    actual = _per_bar(ONE_CALL_PER_BAR[case], f"sma-one-call-{case}")

    _assert_bars(actual, ONE_CALL_VALUES, case)


def test_a_call_no_loop_reaches_has_no_bar_marker() -> None:
    cpp = transpile(HEADER + ONE_CALL_PER_BAR["top-level"])

    assert "_pf_ta_bar" not in cpp

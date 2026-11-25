"""A folded SMA length reads the declaration visible at the assignment."""

import math

import pytest

from pineforge_codegen import transpile
from tests import _series_sma_oracle as oracle
from tests._compile import run_emitted_tu


HEADER = """//@version=6
strategy("block-local named like an input")
n = input.int(5)
big = input.bool(true)
p = 3
reach_signal = 0
if big
"""

AFTER_LOCAL = """    n = 7
    p := n
    reach_signal := 100 + bar_index
"""

BEFORE_LOCAL = """    p := n
    n = 7
    reach_signal := 100 + bar_index
"""

NESTED = """    n = 7
    reach_signal := 100 + bar_index
    if big
        p := n
        reach_signal += 100
"""

ELSE = """else
    p := n
    reach_signal := 300 + bar_index
"""


@pytest.mark.parametrize(
    "block, overrides, input_n, reach, length",
    [
        pytest.param(AFTER_LOCAL, (), 5, 100, 7, id="after-local-default"),
        # The same script must pick the local on the then path and the
        # overridden input on the else path.
        pytest.param(
            AFTER_LOCAL + ELSE, (("n", "9"),), 9, 100, 7,
            id="after-local-override",
        ),
        pytest.param(BEFORE_LOCAL, (), 5, 100, 5, id="before-local-default"),
        pytest.param(
            BEFORE_LOCAL, (("n", "11"),), 11, 100, 11, id="before-local-override",
        ),
        pytest.param(NESTED, (), 5, 200, 7, id="nested-default"),
        pytest.param(NESTED, (("n", "13"),), 13, 200, 7, id="nested-override"),
        pytest.param(
            AFTER_LOCAL + ELSE, (("n", "8"), ("big", "false")), 8, 300, 8,
            id="else-override",
        ),
    ],
)
def test_folded_sma_length_follows_lexical_binding(
    block, overrides, input_n, reach, length,
):
    source = HEADER + block + "sma_value = ta.sma(close, p)\nplot(sma_value)\n"
    members = ("sma_value", "p", "reach_signal", "n")
    rows = oracle.per_bar_rows(
        run_emitted_tu(
            transpile(source, filename="issue-443.pine"),
            oracle.per_bar_driver(members, overrides),
            opt="-O0",
            label=f"issue 443: n={input_n}, reach={reach}, length={length}",
        ),
        len(members),
    )

    assert oracle.column(rows, 2) == [reach + bar for bar in range(oracle.BARS)]
    assert oracle.column(rows, 3) == [input_n] * oracle.BARS
    assert oracle.column(rows, 1) == [length] * oracle.BARS

    values = oracle.column(rows)
    first = next((bar for bar, value in enumerate(values, 1)
                  if not math.isnan(value)), None)
    assert first == length, f"first SMA value on bar {first}, expected bar {length}"
    reference = [
        (length, sum(oracle.CLOSES[end - length:end]) if end >= length else None)
        for end in range(1, oracle.BARS + 1)
    ]
    oracle.assert_per_bar(values, reference, f"SMA({length})")

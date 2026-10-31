"""A block's tuple binding leaves calendar reads outside the block intact."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("tuple block scope")\n'
PAIR = 'pair(float value) => [value, value + 0.5]\nobserved = 0.0\n'

BLOCKS = [
    # The tuple's second element is visible only in the taken if branch.
    pytest.param(
        'if close > open\n'
        '    [a, second] = pair(close - open)\n'
        '    observed := second\n',
        id="if",
    ),
    # One iteration reads the tuple before break closes its owning block.
    pytest.param(
        'while close > open\n'
        '    [a, second] = pair(close - open)\n'
        '    observed := second\n'
        '    break\n',
        id="while",
    ),
    # The matched switch arm owns both the tuple and its local read.
    pytest.param(
        'switch\n'
        '    close > open =>\n'
        '        [a, second] = pair(close - open)\n'
        '        observed := second\n',
        id="switch",
    ),
    # With close 12.5 > open 10, the else tuple holds 12.5 - 10 + 0.5 = 3.
    pytest.param(
        'if close <= open\n'
        '    observed := 0.0\n'
        'else\n'
        '    [a, second] = pair(close - open)\n'
        '    observed := second\n',
        id="else",
    ),
]


@pytest.mark.parametrize("block", BLOCKS)
def test_calendar_read_after_a_tuple_block_uses_the_builtin(block: str) -> None:
    cpp = transpile(HEADER + PAIR + block + 's = second\n')
    # The live block reads its tuple; after the block, second is the calendar.
    assert "observed = second;" in cpp
    # BAR_BUILTINS maps second to the timestamp and exchange-timezone helper.
    assert "s = pine_second(current_bar_.timestamp, syminfo_.timezone);" in cpp
    compile_cpp(cpp, label="calendar read after tuple block")


DRIVER = r'''
#include <iostream>
int main() {
    Bar bar{10.0, 15.0, 9.0, 12.5, 1.0, 7000};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    std::cout << strategy.observed << " " << strategy.s << "\n";
}
'''


@pytest.mark.parametrize("block", BLOCKS)
def test_tuple_block_and_calendar_keep_distinct_values(block: str) -> None:
    cpp = transpile(HEADER + PAIR + block + 's = second\n')
    # The tuple holds 12.5 - 10 + 0.5 = 3; epoch 7000 ms has calendar second 7.
    assert run_emitted_tu(
        cpp, DRIVER, opt="-O0", label="tuple block and calendar values",
    ) == "3 7\n"

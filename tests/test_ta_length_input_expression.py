"""Constant arithmetic defaults retain the input's live TA length."""

import re

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("default,value", [("5 * 4", 20), ("(7 + 5) * 2", 24),
                                         ("30 - 6", 24), ("-(-18)", 18)])
@pytest.mark.parametrize("named", [False, True])
def test_constant_input_expression_sizes_and_resets_ta(default, value, named):
    def source(expression):
        argument = f"defval={expression}" if named else expression
        return (
            '//@version=6\nstrategy("Input length")\n'
            f'window = input.int({argument}, title="Window")\n'
            'average = ta.sma(close, window)\nplot(average)\n'
        )

    cpp = transpile(source(default))
    control = transpile(source(str(value)))
    periods = lambda text: re.findall(r"_ta_sma_\d+\((\d+)\)", text)
    assert periods(cpp) == periods(control) == [str(value)]
    reset = next(line.strip() for line in cpp.splitlines()
                 if re.search(r"_ta_sma_\d+ = ta::SMA\(", line))
    assert 'get_input_int("Window", ' in reset
    assert "ta::SMA(1)" not in reset

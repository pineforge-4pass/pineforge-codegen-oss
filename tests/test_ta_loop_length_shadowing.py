"""A percentile loop length reads its lexical loop binding."""

import re

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("input_name", ["length", "window"])
@pytest.mark.parametrize("expression", ["{counter}", "{counter} + 1"])
def test_percentile_loop_length_uses_the_counter(input_name, expression):
    def source(counter):
        length = expression.format(counter=counter)
        return (
            '//@version=6\nstrategy("Loop lengths")\n'
            f'{input_name} = input.int(20, "Window")\n'
            'float total = 0.0\n'
            f'for {counter} = 2 to 4\n'
            f'    total += ta.percentile_nearest_rank(close, {length}, 50)\n'
            'plot(total)\n'
        )

    for counter in (input_name, "iteration"):
        script = source(counter)
        cpp = transpile(script)
        assert "pineforge::source::SeriesPercentileNearestRank" in cpp
        line = next(
            line for line in cpp.splitlines()
            if "_ta_percentile_nearest_rank_" in line and ".compute(" in line
        )
        assert re.search(
            rf"ta_number\([^)]*\b{re.escape(counter)}\b", line
        ), line
        assert 'get_input_int("Window"' not in line

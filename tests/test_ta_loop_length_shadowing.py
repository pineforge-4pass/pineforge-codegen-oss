"""A loop length must never pick up a same-spelled global input."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


@pytest.mark.parametrize("input_name", ["length", "window"])
@pytest.mark.parametrize("expression", ["{counter}", "{counter} + 1"])
def test_percentile_loop_length_refusal_is_independent_of_input_name(input_name, expression):
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

    diagnostics = []
    for counter in (input_name, "iteration"):
        script = source(counter)
        with pytest.raises(CompileError, match="Unsupported TA constructor length") as caught:
            transpile(script)
        diagnostic = caught.value.diagnostics[0]
        assert diagnostic.code == "PF-E5009"
        expected_line = next(
            number for number, line in enumerate(script.splitlines(), start=1)
            if "ta.percentile_nearest_rank" in line
        )
        assert diagnostic.location.line == expected_line
        written_length = expression.format(counter=counter)
        diagnostics.append(diagnostic.message.replace(f"'{written_length}'", "'counter'"))
    assert diagnostics[0] == diagnostics[1]

"""Empty argument selection is covered without native engine inputs."""

import pytest

from pineforge_codegen import transpile


def engine_call(function, argument):
    if function == "change":
        return (
            "tf_change(prev_bar_timestamp_, current_bar_.timestamp, "
            + argument + ", syminfo_.timezone, syminfo_.session)"
        )
    return "tf_to_seconds(" + argument + ")"


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("argument,lowered", [
    ('""', 'std::string("")'),
    ('"240"', 'std::string("240")'),
    ("frameChoice", "frameChoice"),
])
def test_empty_plain_argument_selects_chart_and_nonempty_selects_argument(function, argument, lowered):
    cpp = transpile(
        '//@version=6\nstrategy("Chart timeframe selection")\n'
        'frameChoice = input.timeframe("")\n'
        f"value = timeframe.{function}({argument})\n"
    )
    expected = (
        "((" + lowered + ").empty() ? "
        + engine_call(function, "script_tf_") + " : "
        + engine_call(function, lowered) + ")"
    )
    assert expected in cpp


@pytest.mark.parametrize("function", ["change", "in_seconds"])
def test_call_argument_is_evaluated_once_before_chart_selection(function):
    cpp = transpile(
        '//@version=6\nstrategy("Chart timeframe selection")\n'
        'frameChoice = input.timeframe("")\n'
        'chosenFrame() => frameChoice\n'
        f"value = timeframe.{function}(chosenFrame())\n"
    )
    assert "const std::string _pf_timeframe_arg = " in cpp
    selected = "(_pf_timeframe_arg.empty() ? script_tf_ : _pf_timeframe_arg)"
    assert engine_call(function, selected) in cpp
    assert cpp.count("const std::string _pf_timeframe_arg = (chosenFrame());") == 1

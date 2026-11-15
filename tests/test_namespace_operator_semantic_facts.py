"""Valid operator operands must survive legacy namespace storage types."""

from pineforge_codegen import transpile


SIZING_LABEL_SOURCE = '''//@version=6
strategy("Sizing label")
txt = "Sizing: " + strategy.cash
if barstate.islast
    label.new(bar_index, high, txt)
'''

ORDINARY_CONTROL_SOURCE = '''//@version=6
strategy("Ordinary addition")
plot(close + open)
'''


def test_strategy_sizing_string_constant_still_transpiles():
    # Acceptance only: the old numeric emission is a separate value defect.
    transpile(SIZING_LABEL_SOURCE)


def test_ordinary_numeric_operator_still_transpiles():
    transpile(ORDINARY_CONTROL_SOURCE)

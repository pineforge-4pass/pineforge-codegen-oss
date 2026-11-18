"""The reported UDT script must compile before any naming-shape assertion."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


REPORTER = """//@version=6
strategy("UDT named Trade", overlay = true, initial_capital = 10000)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
type Trade
    float entry = na
    int bar = na
var Trade last = Trade.new()
if ta.crossover(fast, slow)
    last := Trade.new(close, bar_index)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""


@pytest.mark.parametrize("type_name", ["Trade", "TradeInfo"])
def test_reported_trade_type_compiles(type_name):
    source = (
        REPORTER.replace("\ntype Trade\n", f"\ntype {type_name}\n")
        .replace("var Trade last = Trade.new()", f"var {type_name} last = {type_name}.new()")
        .replace("last := Trade.new(", f"last := {type_name}.new(")
    )
    compile_cpp(
        transpile(source, check_support=True),
        label=f"reported UDT {type_name}",
    )

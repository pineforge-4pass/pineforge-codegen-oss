"""Authored object types cannot meet names imported from the engine."""

import re

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


@pytest.mark.parametrize(
    "type_name", ("Trade", "TradeInfo", "Point", "State", "Entry", "Bar", "Range", "Fill")
)
def test_every_authored_object_type_gets_an_escaped_cpp_name(type_name):
    # Preserve the full reporter body, including both distinct crossover calls.
    source = (
        REPORTER.replace("\ntype Trade\n", f"\ntype {type_name}\n")
        .replace("var Trade last = Trade.new()", f"var {type_name} last = {type_name}.new()")
        .replace("last := Trade.new(", f"last := {type_name}.new(")
    )
    cpp = transpile(source)
    assert not re.search(rf"^struct {re.escape(type_name)} \{{", cpp, re.M)
    handles = re.findall(r"^struct (\w+) \{\n    int32_t __pf_id", cpp, re.M)
    assert len(handles) == 1 and handles[0] != type_name
    compile_cpp(cpp, label=f"authored type {type_name}")


def test_type_escapes_avoid_authored_prefixes_through_composite_and_callable_types():
    source = """//@version=6
strategy("object type escape collisions")
type Trade
    float entry = na
type pf_safe_Trade
    Trade value = na
identity(Trade value) =>
    value
var Trade last = Trade.new(close)
var pf_safe_Trade holder = pf_safe_Trade.new(last)
var array<Trade> values = array.new<Trade>()
values.push(identity(holder.value))
last := values.get(values.size() - 1)
plot(last.entry)
"""
    cpp = transpile(source)
    handles = re.findall(r"^struct (\w+) \{\n    int32_t __pf_id", cpp, re.M)
    assert len(handles) == len(set(handles)) == 2
    assert "Trade" not in handles and "pf_safe_Trade" not in handles
    compile_cpp(cpp, label="object type prefix collisions")

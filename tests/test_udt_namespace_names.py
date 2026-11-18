"""Authored object types cannot meet names imported from the engine."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests.test_array_retained_alias import _reference_cpp


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
    ("type_name", "cpp_name"),
    (
        ("Trade", "pf_safe_Trade"),
        ("TradeTick", "pf_safe_TradeTick"),
        ("PyramidEntry", "pf_safe_PyramidEntry"),
        ("NativeLifecycleKind", "pf_safe_NativeLifecycleKind"),
        ("PineRequestDelivery", "PineRequestDelivery"),
        ("TradeInfo", "TradeInfo"),
        ("Point", "Point"),
        ("State", "State"),
        ("Entry", "Entry"),
        ("Bar", "pf_safe_Bar"),
        ("Range", "Range"),
        ("Fill", "Fill"),
    ),
)
def test_reported_object_names_escape_only_collisions(type_name, cpp_name):
    # Preserve the full reporter body, including both distinct crossover calls.
    source = (
        REPORTER.replace("\ntype Trade\n", f"\ntype {type_name}\n")
        .replace("var Trade last = Trade.new()", f"var {type_name} last = {type_name}.new()")
        .replace("last := Trade.new(", f"last := {type_name}.new(")
    )
    cpp = transpile(source)
    handles = re.findall(r"^struct (\w+) \{\n    int32_t __pf_id", cpp, re.M)
    assert handles == [cpp_name]
    if cpp_name == type_name:
        assert cpp == _reference_cpp(source)
    compile_cpp(cpp, label=f"authored type {type_name}")


def test_type_escapes_avoid_authored_prefixes_through_composite_and_callable_types():
    source = """//@version=6
strategy("object type escape collisions")
type Trade
    float entry = na
type pf_safe_Trade
    Trade value = na
identity(Trade value, float pf_safe_Trade_2) =>
    value
var Trade last = Trade.new(close)
var pf_safe_Trade holder = pf_safe_Trade.new(last)
var array<Trade> values = array.new<Trade>()
values.push(identity(holder.value.copy(), close))
last := values.get(values.size() - 1)
plot(last.entry)
"""
    cpp = transpile(source)
    handles = re.findall(r"^struct (\w+) \{\n    int32_t __pf_id", cpp, re.M)
    assert handles == ["pf_safe_Trade_3", "pf_safe_Trade"]
    compile_cpp(cpp, label="object type prefix collisions")


@pytest.mark.parametrize("body", [
    pytest.param("""type Ledger
    float value = na
var Ledger original = Ledger.new(close)
var Ledger alias = original
var Ledger detached = original.copy()
observed = alias.value
""", id="object-copy"),
    pytest.param("""type Ledger
    float value = na
method readValue(Ledger self) =>
    self.value
var Ledger item = Ledger.new(close)
observed = item.readValue()
""", id="method"),
    pytest.param("""type Ledger
    float value = na
var array<Ledger> items = array.new<Ledger>()
items.push(Ledger.new(close))
Ledger item = items.get(0)
observed = item.value
""", id="array"),
    pytest.param("""type Ledger
    float value = na
var Ledger item = na
missing = na(item)
""", id="nullable"),
    pytest.param("""type pf_safe_Ledger
    float value = na
var pf_safe_Ledger item = pf_safe_Ledger.new(close)
observed = item.value
""", id="authored-prefix"),
])
def test_noncolliding_object_uses_keep_complete_base_cpp(body):
    source = '//@version=6\nstrategy("object naming controls")\n' + body
    cpp = transpile(source)
    assert cpp == _reference_cpp(source)
    compile_cpp(cpp, label="noncolliding object naming")


@pytest.mark.parametrize(("type_name", "header_user"), [
    ("MapCapacityError", "var mapping = map.new<string, float>()\n"),
    ("NumericMatrix", "var grid = matrix.new<float>(1, 1)\n"),
    ("GenericMatrix", "var grid = matrix.new<int>(1, 1)\n"),
    ("DrawingArena", "var segment = line.new(0, close, 1, close)\n"),
])
def test_optional_header_collisions_share_one_type_identity(type_name, header_user):
    body = f"""type {type_name}
    float value = na
type pf_safe_{type_name}
    {type_name} value = na
identity({type_name} item, float pf_safe_{type_name}_2) =>
    item
method readValue({type_name} self) =>
    self.value
var {type_name} item = {type_name}.new(close)
var pf_safe_{type_name} holder = pf_safe_{type_name}.new(item)
var array<{type_name}> items = array.new<{type_name}>()
items.push(identity(holder.value.copy(), close))
observed = items.get(0).readValue()
"""
    header = '//@version=6\nstrategy("optional header object names")\n'
    control = header + body
    control_cpp = transpile(control)
    # The same type is harmless without the header selected by header_user.
    assert control_cpp == _reference_cpp(control)
    assert re.findall(
        r"^struct (\w+) \{\n    int32_t __pf_id", control_cpp, re.M
    ) == [type_name, f"pf_safe_{type_name}"]
    compile_cpp(control_cpp, label=f"unselected header type {type_name}")

    cpp = transpile(header + header_user + body)
    assert re.findall(
        r"^struct (\w+) \{\n    int32_t __pf_id", cpp, re.M
    ) == [f"pf_safe_{type_name}_3", f"pf_safe_{type_name}"]
    compile_cpp(cpp, label=f"selected header type {type_name}")


def test_conditional_type_names_preserve_value_clones_na_and_slice_storage():
    cpp = transpile("""//@version=6
strategy("separate type and value names")
type NumericMatrix
    float value = na
method readValue(NumericMatrix self) =>
    self.value
state() =>
    var float DrawingArena = 1.0
    DrawingArena += 1.0
    DrawingArena
first = state()
second = state()
var grid = matrix.new<float>(1, 1)
var segment = line.new(0, close, 1, close)
var NumericMatrix item = na
item := NumericMatrix.new(close)
var array<NumericMatrix> items = array.new<NumericMatrix>()
items.push(item.copy())
window = items.slice(0, 1)
observed = window.get(0).readValue()
requested = request.security(syminfo.tickerid, input.string("60", "TF"), close)
""")
    assert "struct pf_safe_NumericMatrix {\n    int32_t __pf_id" in cpp
    assert "pf_safe_NumericMatrix{}" in cpp
    assert "_PFSliceArray<pf_safe_NumericMatrix>" in cpp
    assert "pf_safe_DrawingArena" not in cpp
    assert "DrawingArena_cs1" in cpp
    assert "pineforge::DrawingArena<LineRec>" in cpp
    assert 'get_input_string("TF", "60")' in cpp
    compile_cpp(cpp, label="conditional types across clone and storage consumers")

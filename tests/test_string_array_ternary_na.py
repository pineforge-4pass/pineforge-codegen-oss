"""String-valued array initializers retain string storage around na selections."""

import pytest

from pineforge_codegen import transpile
from tests._compile import STRATEGY_FP_FLAGS, compile_cpp


@pytest.mark.parametrize(
    "source, declaration, initializer",
    [
        pytest.param(
            '''//@version=6
strategy("String array values", pyramiding = 0, calc_on_every_tick = false, calc_on_order_fills = false, process_orders_on_close = false, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000)
float sourceValue = (int(bar_index / 20) % 2 == 0 ? 1.0 : -1.0) * float(close)
bool condition = int(bar_index / 20) % 2 == 0
array<string> texts = array.from(str.tostring(sourceValue))
string selectedText = condition ? array.get(texts, 0) : na
float leaf = na(selectedText) ? -1.0 : float(str.length(selectedText))
float signal = float(leaf) + (0.11)
float tradeQty = 1.0 + math.abs(nz(signal, -0.75)) % 7.0
var string active = ""
var string value = ""
if bar_index % 20 == 5 and strategy.position_size == 0
    value := str.tostring(signal)
    active := "entry-" + value
    strategy.entry(active, strategy.long, qty = tradeQty)
if bar_index % 20 == 15 and strategy.position_size != 0
    strategy.close(active, comment = value)
''',
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>{",
            # array<string> holds str.tostring's string before get selects it or na.
            id="array-get",
        ),
        pytest.param(
            '''//@version=6
strategy("String array values", pyramiding = 0, calc_on_every_tick = false, calc_on_order_fills = false, process_orders_on_close = false, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000)
float sourceValue = (int(bar_index / 20) % 2 == 0 ? 1.0 : -1.0) * float(low)
bool condition = int(bar_index / 20) % 2 == 0
array<string> texts = array.from(str.tostring(sourceValue))
string selectedText = condition ? na : array.pop(texts)
float leaf = na(selectedText) ? -1.0 : float(str.length(selectedText))
float signal = float(leaf) + (0.44)
float tradeQty = 1.0 + math.abs(nz(signal, -0.75)) % 7.0
var string active = ""
var string value = ""
if bar_index % 20 == 5 and strategy.position_size == 0
    value := str.tostring(signal)
    active := "entry-" + value
    strategy.entry(active, strategy.long, qty = tradeQty)
if bar_index % 20 == 15 and strategy.position_size != 0
    strategy.close(active, comment = value)
''',
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>{",
            # pop returns the stored string when the na arm is not selected.
            id="array-pop",
        ),
        pytest.param(
            '''//@version=6
strategy("String array values", pyramiding = 0, calc_on_every_tick = false, calc_on_order_fills = false, process_orders_on_close = false, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000)
float sourceValue = (int(bar_index / 20) % 2 == 0 ? 1.0 : -1.0) * float(high)
bool condition = int(bar_index / 20) % 2 == 0
array<string> texts = array.from(str.tostring(sourceValue))
string selectedText = condition ? array.shift(texts) : na
float leaf = na(selectedText) ? -1.0 : float(str.length(selectedText))
float signal = float(leaf) + (0.33)
float tradeQty = 1.0 + math.abs(nz(signal, -0.75)) % 7.0
var string active = ""
var string value = ""
if bar_index % 20 == 5 and strategy.position_size == 0
    value := str.tostring(signal)
    active := "entry-" + value
    strategy.entry(active, strategy.long, qty = tradeQty)
if bar_index % 20 == 15 and strategy.position_size != 0
    strategy.close(active, comment = value)
''',
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>{",
            # shift returns the first stored string before the na test consumes it.
            id="array-shift",
        ),
    ],
)
def test_string_array_ternary_na_translates_and_compiles(source, declaration, initializer):
    cpp = transpile(source)
    assert declaration in cpp
    assert initializer in cpp
    compile_cpp(cpp, label="string_array_ternary_na", extra_flags=STRATEGY_FP_FLAGS)


@pytest.mark.parametrize(
    "expression, declaration, initializer",
    [
        pytest.param(
            'str.upper("small")',
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>{",
            # upper's registered string result determines the array element family.
            id="uppercase-string",
        ),
        pytest.param(
            'str.format("{0}", close)',
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>{",
            # format returns a string even when its formatted argument is numeric.
            id="formatted-string",
        ),
    ],
)
def test_array_from_infers_other_builtin_string_results(expression, declaration, initializer):
    source = (
        '//@version=6\nstrategy("String results")\n'
        f"texts = array.from({expression})\n"
    )
    cpp = transpile(source)
    assert declaration in cpp
    assert initializer in cpp
    compile_cpp(cpp, label="array_from_string_result", extra_flags=STRATEGY_FP_FLAGS)


@pytest.mark.parametrize(
    "expression, declaration, initializer",
    [
        pytest.param(
            'array.from("text")',
            "std::vector<std::string> texts;",
            'texts = std::vector<std::string>{std::string("text")};',
            # A literal string already supplies string storage and its literal value.
            id="string-literal",
        ),
        pytest.param(
            "array.new_string()",
            "std::vector<std::string> texts;",
            "texts = std::vector<std::string>();",
            # The typed empty constructor already emits an empty string vector.
            id="empty-string-array",
        ),
    ],
)
def test_existing_string_array_constructors_are_unchanged(expression, declaration, initializer):
    source = (
        '//@version=6\nstrategy("String constructors")\n'
        f"array<string> texts = {expression}\n"
    )
    cpp = transpile(source)
    assert declaration in cpp
    assert initializer in cpp

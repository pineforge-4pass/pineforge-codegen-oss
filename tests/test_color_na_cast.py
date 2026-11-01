"""Color casts preserve missing values, packed colors and argument evaluation."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import Level
from tests._compile import compile_cpp, run_emitted_tu


_VALUES_SOURCE = """//@version=6
strategy("Color cast values")
copyColor(color shade) => color(shade)
missingColor() =>
    color shade = na
    shade
var calls = array.new<int>(1, 0)
countedColor(color shade) =>
    array.set(calls, 0, array.get(calls, 0) + 1)
    shade
color original_color = color.rgb(32, 64, 96)
color missing_color = na
literal_na = na(color(na))
bound_na = na(color(missing_color))
returned_na = na(color(missingColor()))
keyword_na = na(color(x = missing_color))
nested_na = na(color(color(na)))
color preserved = color(original_color)
preserved_na = na(preserved)
preserved_equal = preserved == original_color
float red_channel = color.r(preserved)
rgb_na = na(color.rgb(1, 2, 3))
var color held = color.rgb(32, 64, 96)
if bar_index == 1
    held := color(na)
held_na = na(held)
color function_missing = copyColor(missing_color)
color function_color = copyColor(original_color)
function_na = na(function_missing)
direct_function_na = na(copyColor(missing_color))
color counted = color(countedColor(original_color))
evaluations = array.get(calls, 0)
counted_equal = counted == original_color
color historical_color = bar_index == 0 ? color(na) : original_color
history_na = na(color(historical_color[2]))
"""

_VALUES_DRIVER = r"""
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 120000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 180000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    std::cout << std::setprecision(17)
              << "literal_na " << strategy.literal_na << "\n"
              << "bound_na " << strategy.bound_na << "\n"
              << "returned_na " << strategy.returned_na << "\n"
              << "keyword_na " << strategy.keyword_na << "\n"
              << "nested_na " << strategy.nested_na << "\n"
              << "preserved " << strategy.preserved << "\n"
              << "preserved_na " << strategy.preserved_na << "\n"
              << "preserved_equal " << strategy.preserved_equal << "\n"
              << "red_channel " << strategy.red_channel << "\n"
              << "rgb_na " << strategy.rgb_na << "\n"
              << "held_na " << strategy.held_na << "\n"
              << "function_na " << strategy.function_na << "\n"
              << "direct_function_na " << strategy.direct_function_na << "\n"
              << "function_color " << strategy.function_color << "\n"
              << "evaluations " << strategy.evaluations << "\n"
              << "counted_equal " << strategy.counted_equal << "\n"
              << "history_na " << strategy.history_na << "\n";
}
"""

_VALUE_ROWS = [
    ("literal_na", 1.0),  # Casting na gives a missing color, so na() is true.
    ("bound_na", 1.0),  # A color variable holding na keeps its missing value.
    ("returned_na", 1.0),  # A function's missing color remains missing through the cast.
    ("keyword_na", 1.0),  # Binding the same missing argument by x preserves na.
    ("nested_na", 1.0),  # Applying the color cast twice still yields a missing color.
    ("preserved", 4280303712.0),  # Opaque ARGB is 0xFF204060: 4278190080 + 2113632.
    ("preserved_na", 0.0),  # Control: the concrete packed color is not missing.
    ("preserved_equal", 1.0),  # A value cast must retain every packed color bit.
    ("red_channel", 32.0),  # The red byte of 0xFF204060 is (value >> 16) & 255 = 32.
    ("rgb_na", 0.0),  # Control: rgb(1, 2, 3) is the nonmissing color 0xFF010203.
    ("held_na", 1.0),  # The reset on bar 1 persists through bar 2 without another write.
    ("function_na", 1.0),  # A missing color parameter stays missing through the return cast.
    ("direct_function_na", 1.0),  # na() observes the missing function return without a binding.
    ("function_color", 4280303712.0),  # The same function returns opaque 0xFF204060 unchanged.
    ("evaluations", 3.0),  # One cast argument call on each of three bars increments three times.
    ("counted_equal", 1.0),  # The evaluated function returns the original packed color.
    ("history_na", 1.0),  # On bar 2, history offset 2 reads bar 0's missing color.
]


@pytest.fixture(scope="module", params=["-O0", "-O2"])
def color_values(request: pytest.FixtureRequest) -> dict[str, float]:
    output = run_emitted_tu(
        transpile(_VALUES_SOURCE), _VALUES_DRIVER,
        opt=request.param, label="color-cast-values",
    )
    return {name: float(value) for name, value in
            (line.split() for line in output.splitlines())}


@pytest.mark.parametrize("name, expected", _VALUE_ROWS, ids=[row[0] for row in _VALUE_ROWS])
def test_color_cast_values(
    color_values: dict[str, float], name: str, expected: float,
) -> None:
    assert color_values[name] == expected


_PROBE_SOURCE = """//@version=6
strategy("Color selection", pyramiding = 0, calc_on_every_tick = false, calc_on_order_fills = false, process_orders_on_close = false, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000)
color shade = int(bar_index / 20) % 2 == 0 ? color(na) : color.rgb(32, 64, 96)
float leaf = na(shade) ? -1.0 : color.r(shade)
float signal = float(leaf) + (0.11)
float quantity = 1.0 + math.abs(nz(signal, -0.75)) % 7.0
var string active = ""
var string value = ""
if bar_index % 20 == 5 and strategy.position_size == 0
    value := str.tostring(signal)
    active := "color-" + value
    strategy.entry(active, strategy.long, qty = quantity)
if bar_index % 20 == 15 and strategy.position_size != 0
    strategy.close(active, comment = value)
"""

_PROBE_DRIVER = r"""
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[80];
    for (int i = 0; i < 80; ++i)
        bars[i] = Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000LL * (i + 1)};
    std::cout << std::setprecision(17);
    for (int count : {1, 6, 16, 20, 21, 26, 36, 40,
                      41, 46, 56, 60, 61, 66, 76, 80}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << count - 1 << " " << is_na(strategy.shade) << " "
                  << strategy.leaf << " " << strategy.signal << " "
                  << strategy.quantity << "\n";
    }
}
"""

_PROBE_ROWS = [
    (0, (1.0, -1.0, -0.89, 1.89)),  # Block 0 selects na: leaf -1, signal -0.89, qty 1.89.
    (5, (1.0, -1.0, -0.89, 1.89)),  # The first entry uses block 0's missing-color branch.
    (15, (1.0, -1.0, -0.89, 1.89)),  # The first close still falls within block 0.
    (19, (1.0, -1.0, -0.89, 1.89)),  # The last bar of block 0 still selects na.
    (20, (0.0, 32.0, 32.11, 5.11)),  # Control: block 1 selects red 32; 1 + 32.11 % 7 = 5.11.
    (25, (0.0, 32.0, 32.11, 5.11)),  # Control: the second entry uses the real-color arm.
    (35, (0.0, 32.0, 32.11, 5.11)),  # Control: the second close stays in the real-color arm.
    (39, (0.0, 32.0, 32.11, 5.11)),  # Control: the last bar of block 1 keeps red 32.
    (40, (1.0, -1.0, -0.89, 1.89)),  # Block 2 returns to the missing-color arm.
    (45, (1.0, -1.0, -0.89, 1.89)),  # The third entry again uses quantity 1.89.
    (55, (1.0, -1.0, -0.89, 1.89)),  # The third close still uses the missing-color arm.
    (59, (1.0, -1.0, -0.89, 1.89)),  # The last bar of block 2 still selects na.
    (60, (0.0, 32.0, 32.11, 5.11)),  # Control: block 3 returns to the real-color arm.
    (65, (0.0, 32.0, 32.11, 5.11)),  # Control: the fourth entry uses quantity 5.11.
    (75, (0.0, 32.0, 32.11, 5.11)),  # Control: the fourth close stays in block 3.
    (79, (0.0, 32.0, 32.11, 5.11)),  # Control: the final bar keeps the real-color branch.
]


@pytest.fixture(scope="module", params=["-O0", "-O2"])
def color_probe(request: pytest.FixtureRequest) -> dict[int, tuple[float, ...]]:
    output = run_emitted_tu(
        transpile(_PROBE_SOURCE), _PROBE_DRIVER,
        opt=request.param, label="color-selection",
    )
    rows = [line.split() for line in output.splitlines()]
    return {int(row[0]): tuple(float(value) for value in row[1:]) for row in rows}


@pytest.mark.parametrize("bar, expected", _PROBE_ROWS, ids=[str(row[0]) for row in _PROBE_ROWS])
def test_color_selection_branches(
    color_probe: dict[int, tuple[float, ...]], bar: int, expected: tuple[float, ...],
) -> None:
    assert color_probe[bar] == pytest.approx(expected, rel=0.0, abs=1e-12)


_ORDERS_SOURCE = _PROBE_SOURCE + """
closed_count = strategy.closedtrades
first_quantity = strategy.closedtrades.size(0)
second_quantity = strategy.closedtrades.size(1)
third_quantity = strategy.closedtrades.size(2)
fourth_quantity = strategy.closedtrades.size(3)
"""

_ORDERS_DRIVER = r"""
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[80];
    for (int i = 0; i < 80; ++i)
        bars[i] = Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000LL * (i + 1)};
    GeneratedStrategy strategy;
    strategy.run(bars, 80);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    std::cout << std::setprecision(17)
              << strategy.closed_count << " " << strategy.first_quantity << " "
              << strategy.second_quantity << " " << strategy.third_quantity << " "
              << strategy.fourth_quantity << "\n";
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_color_selection_order_quantities(opt: str) -> None:
    output = run_emitted_tu(
        transpile(_ORDERS_SOURCE), _ORDERS_DRIVER, opt=opt, label="color-selection-orders",
    )
    # Four entries at 5/25/45/65 close at 16/36/56/76, alternating the two quantities.
    expected = (4.0, 1.89, 5.11, 1.89, 5.11)
    assert tuple(float(value) for value in output.split()) == pytest.approx(
        expected, rel=0.0, abs=1e-12,
    )


@pytest.mark.parametrize(
    "source", [_VALUES_SOURCE, _PROBE_SOURCE, _ORDERS_SOURCE],
    ids=["values", "selection", "orders"],
)
def test_color_cast_sources_compile(source: str, request: pytest.FixtureRequest) -> None:
    compile_cpp(transpile(source), label=request.node.name)


def test_color_cast_keeps_an_accurate_warning() -> None:
    output = transpile_full('//@version=6\nstrategy("Color cast")\nx = color(na)\n')
    warnings = [diagnostic for diagnostic in output["diagnostics"]
                if "color(...) cast" in diagnostic.message]
    assert len(warnings) == 1  # There is exactly one authored color cast.
    warning = warnings[0]
    assert warning.level == Level.WARNING
    assert warning.code == "PF-W1553"
    assert warning.message == (
        "color(...) cast preserves color values and na; "
        "PineForge does not render visual output."
    )

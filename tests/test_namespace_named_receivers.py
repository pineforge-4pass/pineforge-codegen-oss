"""Authored record receivers retain their field types when shadowing namespaces."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_HEADER = """//@version=6
strategy("namespace named record receivers")
type Holder
    float factor
"""

_CONTROLS = """none_value = adjustment.none
dividends_value = adjustment.dividends
splits_value = adjustment.splits
"""

_BARS = (
    (100.0, 102.0, 99.0, 101.5),
    (200.0, 201.0, 197.0, 197.25),
)

_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    pineforge::Bar bars[] = {
BAR_INITIALIZERS
    };
    for (auto& bar : bars) {
        GeneratedStrategy strategy;
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << std::setprecision(17)
                  << strategy.actual << ' '
                  << strategy.none_value << ' '
                  << strategy.dividends_value << ' '
                  << strategy.splits_value << '\n';
    }
}
"""


def _assert_field_values(body: str) -> None:
    driver = _DRIVER.replace(
        "BAR_INITIALIZERS",
        ",\n".join(
            f"        {{{open_}, {high}, {low}, {close}, 10.0, 1743466500000LL}}"
            for open_, high, low, close in _BARS
        ),
    )
    output = run_emitted_tu(
        transpile(_HEADER + body + _CONTROLS),
        driver,
        opt="-O0",
        label="namespace named record receiver",
    )
    actual = [list(map(float, line.split())) for line in output.splitlines()]
    # A fresh instance initializes its persistent local from each input bar.
    # The unshadowed namespace constants retain their existing integer values.
    assert actual == [
        [close - open_, 0, 1, 2]
        for open_, _high, _low, close in _BARS
    ]


@pytest.mark.parametrize("name", ["adjustment", "backadjustment"])
def test_typed_parameter_keeps_float_field(name: str) -> None:
    _assert_field_values(
        f"""read_field(Holder {name}) =>
    var value = {name}.factor
    value
actual = read_field(Holder.new(close - open))
"""
    )


def test_untyped_parameter_keeps_float_field() -> None:
    _assert_field_values(
        """read_field(adjustment) =>
    var value = adjustment.factor
    value
actual = read_field(Holder.new(close - open))
"""
    )


def test_method_receiver_keeps_float_field() -> None:
    _assert_field_values(
        """method read_field(Holder adjustment) =>
    var value = adjustment.factor
    value
record = Holder.new(close - open)
actual = record.read_field()
"""
    )


def test_tuple_element_inside_function_keeps_float_field() -> None:
    _assert_field_values(
        """pair(float factor) =>
    [Holder.new(factor), 0]
read_field() =>
    [adjustment, _] = pair(close - open)
    var value = adjustment.factor
    value
actual = read_field()
"""
    )

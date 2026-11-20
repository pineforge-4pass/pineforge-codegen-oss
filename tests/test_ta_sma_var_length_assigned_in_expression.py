"""An assignment inside a value-producing expression makes a TA length mutable."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("TA SMA var length assigned in expression")\n'

CLOSES = (
    121.3,
    124.7,
    121.9,
    125.1,
    123.4,
    126.8,
    122.2,
    127.5,
    124.9,
    128.3,
    123.7,
    129.6,
)
LENGTH = 5

_DRIVER_TEMPLATE = r"""
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(%COUNT%);
    const double closes[] = {%CLOSES%};
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = closes[i];
        bars[i].high = closes[i] + 1.0;
        bars[i].low = closes[i] - 1.0;
        bars[i].close = closes[i];
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL
            + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.z << "\n";
    }
}
"""

DRIVER = (
    _DRIVER_TEMPLATE
    .replace("%COUNT%", str(len(CLOSES)))
    .replace("%CLOSES%", ", ".join(repr(c) for c in CLOSES))
)

METHOD_PREFIX = "method id(int self) => self\n"

REFUSAL_SHAPES = [
    pytest.param(
        """var int p = 5
x = if close > open
    p := p + 1
    0
else
    0
z = ta.sma(close, p)
""",
        id="if-expression",
    ),
    pytest.param(
        """var int p = 5
x = switch bar_index % 2
    0 =>
        p := p + 1
        0
    => 0
z = ta.sma(close, p)
""",
        id="switch-expression",
    ),
    # A `for` loop used as a value (`x = for i = 1 to 2 ...`) is not a row: the
    # parser does not accept it (PF-E0037, measured on AWS), so such a script
    # never reaches the length admission this file tests.
    pytest.param(
        """var int p = 5
x = if close > open
    y = if bar_index % 2 == 0
        p := p + 1
        0
    else
        0
    y
else
    0
z = ta.sma(close, p)
""",
        id="nested-if-expression",
    ),
]


def _run(source: str, label: str) -> str:
    return run_emitted_tu(transpile(source), DRIVER, opt="-O0", label=label)


def _assert_sma_lines(output: str) -> None:
    lines = output.split()
    assert len(lines) == len(CLOSES), output
    for count, line in enumerate(lines, start=1):
        if count < LENGTH:
            assert line.lstrip("-").lower() == "nan", (count, line)
        else:
            window = CLOSES[count - LENGTH:count]
            assert float(line) == pytest.approx(
                sum(window) / LENGTH,
                rel=1e-5,
            ), (count, line)


@pytest.mark.parametrize("shape", REFUSAL_SHAPES)
@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("", id="without-method"),
        pytest.param(METHOD_PREFIX, id="with-method"),
    ],
)
def test_assignment_to_length_inside_expression_is_refused(
    prefix: str,
    shape: str,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + prefix + shape)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5092"
    assert "Unsupported TA constructor length 'p' for ta.sma" in str(caught.value)


def test_assignment_to_a_different_var_inside_if_keeps_literal_length() -> None:
    source = HEADER + """var int p = 5
var int q = 0
x = if close > open
    q := q + 1
    0
else
    0
z = ta.sma(close, p)
"""

    _assert_sma_lines(_run(source, "ta-sma-expression-assignment-control"))


def test_reading_length_inside_if_keeps_literal_length() -> None:
    source = HEADER + """var int p = 5
x = if close > open
    p
else
    0
z = ta.sma(close, p)
"""

    _assert_sma_lines(_run(source, "ta-sma-expression-read-control"))

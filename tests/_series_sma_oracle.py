"""Bars, a per-bar driver and constant-length references for series ``ta.sma`` tests.

A test of a ``ta.sma`` whose length the script computes runs the script's own
Pine text natively and compares the value of every bar with a table written
in the test. The table is worked out by hand from the bars below: each row is
the length the script's own text gives that call and the sum of the values
that window holds, or no sum where the call answers na. Nothing in a table is
read from the C++ the script under test is lowered to.

Before the script under test is transpiled, the same table is checked against
a native reference that does not use the series lowering: ``ta.sma`` calls
with a constant length, which the constructor has always sized, run on the
same bars and on the same call clock (every bar, the bars an ``if`` reaches,
every pass of a loop). A reference that disagrees with a table fails the test
on any codegen; a script whose series length is refused fails at its own
``transpile``, after its reference has run.

This module holds text and arithmetic only. Each test compiles and runs with
``tests._compile.run_emitted_tu`` itself.
"""

from __future__ import annotations

import math

# Eighteen one-minute bars. A bar opens at the close before it (the first at
# 9), so ``close > open`` holds on bars 0, 1, 3, 5, 7, 8, 10, 12, 14, 15, 17.
# The closes add up to 10, 22, 33, 48, 62, 80, 93, 110, 130, 146, 168, 187,
# 212, 233, 257, 285, 308, 335 through each bar, which is where the sums of
# the tables come from: the last n closes at bar b are the total through b
# less the total through b - n.
CLOSES = (10, 12, 11, 15, 14, 18, 13, 17, 20, 16, 22, 19, 25, 21, 24, 28, 23, 27)
OPENS = (9,) + CLOSES[:-1]
BARS = len(CLOSES)

# The windows of the every-bar reference: ``w<k> = ta.sma(close, <k>)``.
WINDOWS = tuple(range(1, 14))
EVERY_BAR_MEMBERS = tuple(f"w{window}" for window in WINDOWS)

# Every bar runs each constant-length call once.
EVERY_BAR_REFERENCE = (
    '//@version=6\nstrategy("constant-length sma reference")\n'
    + "".join(f"w{window} = ta.sma(close, {window})\n" for window in WINDOWS)
)

# The constant-length call runs only on the bars its ``if`` reaches, as a call
# inside ``if close > open`` does; on the other bars the value is na.
RISING_BAR_REFERENCE = (
    '//@version=6\nstrategy("constant-length sma reference, rising bars")\n'
    "float gated = na\n"
    "if close > open\n"
    "    gated := ta.sma(close, 7)\n"
)

# Three passes on every bar, the call running on each of them, as one call
# inside ``for q = 1 to 3`` does. The call keeps one sample per bar, the input
# of the bar's last pass, so the value left after the loop is the mean of the
# last three bars' closes, and na on the first two bars.
LOOP_REFERENCE = (
    '//@version=6\nstrategy("constant-length sma reference, loop")\n'
    "float picked = na\n"
    "for k = 1 to 3\n"
    "    picked := ta.sma(close, 3)\n"
)

_DRIVER = r"""
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <type_traits>
#include <vector>

// A member the emitter keeps as a series is read at its current bar.
template <class T> static double pf_test_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}

static void pf_test_print(double value) {
    if (std::isnan(value)) std::cout << "na";
    else std::cout << value;
}

int main() {
    const double opens[] = {@OPENS@};
    const double closes[] = {@CLOSES@};
    const int total = @BARS@;
    std::vector<pineforge::Bar> bars(total);
    for (int i = 0; i < total; ++i)
        bars[i] = pineforge::Bar{
            opens[i], std::max(opens[i], closes[i]),
            std::min(opens[i], closes[i]), closes[i], 1.0,
            1704067200000LL + i * 60000LL};
    std::cout << std::setprecision(17);
    // One run per bar, each from a fresh strategy and ending on that bar: the
    // members then hold that bar's values, and no run sees a later bar.
    for (int count = 1; count <= total; ++count) {
        GeneratedStrategy strategy;
@INPUTS@        strategy.run(bars.data(), count, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 14;
        }
@PRINTS@        std::cout << "\n";
    }
    return 0;
}
"""


def per_bar_driver(members, inputs=(), closes=CLOSES) -> str:
    """A ``main`` that prints one line per bar: the value each of ``members``
    (top-level variables of the script) holds on that bar, na as ``na``.
    ``inputs`` are ``(title, value)`` overrides set before every run.
    ``closes`` replaces the bars above with a test's own: each opens at the
    close before it, the first one below its close, as those do."""
    sets = "".join(
        f'        strategy_set_input(&strategy, "{title}", "{value}");\n'
        for title, value in inputs
    )
    prints = '        std::cout << " ";\n'.join(
        f"        pf_test_print(pf_test_value(strategy.{member}));\n"
        for member in members
    )
    opens = (closes[0] - 1,) + tuple(closes[:-1])
    return (
        _DRIVER
        .replace("@OPENS@", ", ".join(str(value) for value in opens))
        .replace("@CLOSES@", ", ".join(str(value) for value in closes))
        .replace("@BARS@", str(len(closes)))
        .replace("@INPUTS@", sets)
        .replace("@PRINTS@", prints)
    )


def per_bar_rows(output: str, width: int, bars: int = BARS) -> list[tuple[float, ...]]:
    """The driver's output: for each of the ``bars`` bars, the ``width``
    values it printed."""
    rows = [
        tuple(math.nan if cell == "na" else float(cell) for cell in line.split())
        for line in output.splitlines()
    ]
    assert len(rows) == bars and all(len(row) == width for row in rows), output
    return rows


def column(rows, index: int = 0) -> list[float]:
    return [row[index] for row in rows]


def scheduled(rows, table) -> list[float]:
    """From the every-bar reference's rows, the constant window each bar's
    own length names: bar ``b`` with length ``n`` reads ``w<n>`` at ``b``."""
    return [row[WINDOWS.index(length)] for row, (length, _) in zip(rows, table)]


def assert_per_bar(actual, table, what: str) -> None:
    """``actual`` holds ``table``'s mean on every bar. A row of ``table`` is
    ``(length, sum)``: the window length of the bar's call and the sum of the
    values in that window. ``sum`` is None where the call answers na."""
    assert len(actual) == len(table) == BARS, (what, actual)
    for bar, (value, (length, total)) in enumerate(zip(actual, table)):
        if total is None:
            assert math.isnan(value), (
                f"{what}: bar {bar} is {value!r}; na is expected "
                f"(length {length})")
        else:
            assert abs(value - total / length) <= 1e-9, (
                f"{what}: bar {bar} is {value!r}; {total} / {length} is "
                "expected")

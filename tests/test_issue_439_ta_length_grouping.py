"""Operators in an SMA length apply to the whole value returned by a helper."""

import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <type_traits>

template <class T> static double current(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}

struct ObservedStrategy : GeneratedStrategy {
    bool override_n = false;

    void on_source_bar(const pineforge::Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << bar.close << ' ' << current(n) << ' ' << current(s) << ' '
                  << (override_n ? current(control3) : current(control5)) << '\n';
    }
};

int main() {
    pineforge::Bar bars[20]{};
    for (int i = 0; i < 20; ++i) {
        const double close = i + 1;
        bars[i] = pineforge::Bar{close, close, close, close, 1.0,
                                 1704067200000LL + 60000LL * i};
    }
    std::cout << std::setprecision(17);
    for (bool override_n : {false, true}) {
        ObservedStrategy strategy;
        strategy.override_n = override_n;
        if (override_n) strategy.set_input("n", "3");
        strategy.run(bars, 20, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << '\n';
            return 1;
        }
    }
}
"""


@pytest.mark.parametrize(
    "helpers, length_expr, periods, reported",
    [
        pytest.param("f(int x) => x + 1\n", "f(n) * 2",
                     (12, 8), (14.5, 16.5), id="multiply"),
        pytest.param("g(int x) => -x + 1\nf(int x) => g(x)\n", "-f(n)",
                     (4, 2), (18.5, 19.5), id="nested-unary-minus"),
        pytest.param("f(int x) => x == 5 ? x > 0 : x < 0\n", "f(n) ? 9 : 3",
                     (9, 3), (16.0, 19.0), id="ternary-condition"),
    ],
)
def test_helper_result_grouping_in_sma_length(helpers, length_expr, periods, reported):
    period5, period3 = periods
    source = (
        '//@version=6\nstrategy("example")\n'
        f"{helpers}n = input.int(5)\n"
        f"len = {length_expr}\n"
        "s = ta.sma(close, len)\n"
        f"control5 = ta.sma(close, {period5})\n"
        f"control3 = ta.sma(close, {period3})\n"
    )
    output = run_emitted_tu(transpile(source), DRIVER, opt="-O0", label=length_expr)
    rows = [tuple(map(float, line.split())) for line in output.splitlines()]
    assert len(rows) == 40 and all(len(row) == 4 for row in rows), output

    comparisons = []
    for n, period, last, run in zip((5, 3), periods, reported, (rows[:20], rows[20:])):
        closes, inputs, actual, control = zip(*run)
        assert closes == tuple(range(1, 21))
        assert inputs == (n,) * 20

        # Check the literal-period control independently, including warmup.
        expected = tuple(
            math.nan if close < period else close - (period - 1) / 2
            for close in closes
        )
        assert control == pytest.approx(expected, rel=0, abs=1e-12, nan_ok=True)
        assert control[-1] == last
        comparisons.append((n, actual, control))

    assert tuple(actual[-1] for _, actual, _ in comparisons) == reported
    for n, actual, control in comparisons:
        assert actual == pytest.approx(control, rel=0, abs=1e-12, nan_ok=True), f"n={n}"

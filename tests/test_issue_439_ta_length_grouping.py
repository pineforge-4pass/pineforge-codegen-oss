"""A helper result stays grouped when an operator uses it in an SMA length."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


DRIVER = r"""
#include <iostream>
#include <type_traits>

template <class T> static double last_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}

int main() {
    pineforge::Bar bars[20]{};
    for (int i = 0; i < 20; ++i) {
        const double close = i + 1;
        bars[i] = pineforge::Bar{close, close, close, close, 1.0,
                                 1704067200000LL + 60000LL * i};
    }
    for (bool override_n : {false, true}) {
        GeneratedStrategy strategy;
        if (override_n) strategy.set_input("n", "3");
        strategy.run(bars, 20, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << last_value(strategy.s) << ' '
                  << (override_n ? last_value(strategy.control3)
                                 : last_value(strategy.control5)) << '\n';
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
    default_s, default_control, override_s, override_control = map(float, output.split())

    # On bar 20 the first close of a window of length p is 21 - p.
    assert tuple((21 - period + 20) / 2 for period in periods) == reported
    assert (default_control, override_control) == reported
    assert (default_s, override_s) == reported

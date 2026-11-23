"""A function-local persistent string keeps its type when read."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


DRIVER = r'''
#include <iostream>
#include <vector>

int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 900000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "15", "15");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    std::cout << strategy.x << "\n";
}
'''


@pytest.mark.parametrize("declaration", ["var string", "string"], ids=["var", "plain"])
@pytest.mark.parametrize(
    ("read", "plot_value", "expected"),
    [
        pytest.param("timeframe.in_seconds(s)", "x", "3600", id="timeframe"),
        pytest.param('s + "x"', "str.length(x)", "60x", id="concatenation"),
    ],
)
def test_callable_string_read(declaration, read, plot_value, expected):
    source = (
        '//@version=6\n'
        'strategy("example")\n'
        'f(string a) =>\n'
        f'    {declaration} s = a\n'
        f'    {read}\n'
        'x = f("60")\n'
        f'plot({plot_value})\n'
    )
    output = run_emitted_tu(
        transpile(source), DRIVER, opt="-O0", label="issue 424 callable string read",
    )
    assert output.strip() == expected

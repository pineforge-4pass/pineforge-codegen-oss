"""Issue #382, item 3: the accented-letter results measured on TradingView.

Only the two accented-letter rows are TradingView measurements from the issue.
The ASCII rows control the existing behaviour.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


DRIVER = r'''
#include <iostream>
#include <vector>

int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0 + double(i);
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 60000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "1", "1");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 14;
    }
    std::cout << strategy.result;
}
'''


@pytest.mark.parametrize(
    "operation, source, expected",
    [
        pytest.param("upper", "é", "É", id="measured-upper-accent"),
        pytest.param("lower", "É", "é", id="measured-lower-accent"),
        pytest.param(
            "upper", "aBc xyz 019!", "ABC XYZ 019!", id="control-upper-ascii",
        ),
        pytest.param(
            "lower", "aBc XYZ 019!", "abc xyz 019!", id="control-lower-ascii",
        ),
    ],
)
def test_issue_382_case_mapping(operation, source, expected):
    cpp = transpile(
        '//@version=6\nstrategy("issue 382 case mapping")\n'
        f'source = input.string("{source}")\n'
        f"result = str.{operation}(source)\n"
        "plot(close)\n"
    )
    assert run_emitted_tu(
        cpp, DRIVER, opt="-O0", label=f"issue 382 str.{operation}",
    ) == expected

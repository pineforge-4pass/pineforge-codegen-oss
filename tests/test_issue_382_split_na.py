"""Issue #382 item 10: a literal na separator must build like string(na).

Retained TradingView NA split matrices measure abc as a, b, c for the literal
and typed-variable forms. Check both complete arrays against those values.
"""

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


def test_literal_na_separator_builds_and_matches_string_na():
    cpp = transpile(
        '//@version=6\nstrategy("issue 382 split na")\n'
        's = "abc"\n'
        'p = str.split(s, na)\n'
        'q = str.split(s, string(na))\n'
        'expectedParts = array.from("a", "b", "c")\n'
        'same = array.size(p) == 3 and array.size(q) == 3\n'
        'if same and array.size(p) > 0\n'
        '    for i = 0 to array.size(p) - 1\n'
        '        same := same and array.get(p, i) == array.get(expectedParts, i) and array.get(q, i) == array.get(expectedParts, i)\n'
        'n = same ? 1 : 0\n'
        'plot(n)\n'
    )
    driver = r'''
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
        std::cerr << strategy.last_error() << "\n";
        return 14;
    }
    std::cout << strategy.n << "\n";
    return 0;
}
'''
    assert run_emitted_tu(
        cpp, driver, opt="-O0", label="issue 382 literal na separator",
    ) == "1\n"

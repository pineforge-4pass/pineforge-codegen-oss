"""Issue #382, item 5: an absent substring has an na position, not -1.

Expected results come from the issue's TradingView measurements, including
runtime operands, a longer needle, a match at zero and an empty needle.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("issue 382 pos")\nplot(close)\n'
DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1, "1", "1");
    // OBSERVE
}
'''


@pytest.mark.parametrize("arguments", [
    '"abc", "x"',
    '"abc", "abcd"',
    "sourceText, needleText",
])
def test_absent_pos_is_na_for_direct_and_pine_observers(arguments):
    cpp = transpile(HEADER + f'''
sourceText = close > 0 ? "abc" : "def"
needleText = close > 0 ? "x" : "a"
position = str.pos({arguments})
missing = na(str.pos({arguments}))
replacement = nz(str.pos({arguments}), 0)
''')
    observe = r'''
    std::cout << is_na(strategy.position) << " " << strategy.missing
              << " " << strategy.replacement << "\n";
'''
    assert run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe),
        opt="-O2", label="issue 382 absent position",
    ) == "1 1 0\n"


@pytest.mark.parametrize("needle", ["a", ""])
def test_a_zero_position_is_present(needle):
    cpp = transpile(HEADER + f'''
position = str.pos("abc", "{needle}")
missing = na(position)
''')
    observe = r'''
    std::cout << strategy.position << " " << strategy.missing << "\n";
'''
    assert run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe),
        opt="-O2", label="issue 382 present zero position",
    ) == "0 0\n"

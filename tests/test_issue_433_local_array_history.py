import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


REPRO = """//@version=6
strategy("Local array history")
f(float y) =>
    la = array.from(y)
    na(la[1]) ? -1.0 : (la[1]).get(0)
r = f(close)
if r > 0
    strategy.entry("L", strategy.long)
"""


# Both calls read the array as the end of the previous invocation left it.
# The sparse call and the same-named chart declaration keep separate histories.
ISOLATED = """//@version=6
strategy("Local array snapshots")
f(float y) =>
    la = array.from(y)
    previous = na(la[1]) ? -1.0 : (la[1]).get(0)
    la.set(0, y + 100)
    previous
la = array.from(high)
r = f(close)
s = bar_index % 2 == 0 ? f(open) : -9.0
t = na(la[1]) ? -1.0 : (la[1]).get(0)
if r > 0
    strategy.entry("L", strategy.long)
"""


@pytest.mark.parametrize("source,observed,expected", [
    pytest.param(REPRO, "strategy.r", ["-1", "11", "21", "13", "50"], id="reported"),
    pytest.param(
        ISOLATED, 'strategy.r << " " << strategy.s << " " << strategy.t',
        ["-1 -1 -1", "111 -9 15", "121 101 28", "113 -9 18", "150 103 54"],
        id="isolated-snapshots",
    ),
])
def test_previous_local_array_per_call_site(source, observed, expected):
    driver = r"""
#include <iostream>
int main() {
    pineforge::Bar bars[] = {
        {1, 15, 1, 11, 1, 1743466500000LL},
        {2, 28, 2, 21, 1, 1743466560000LL},
        {3, 18, 3, 13, 1, 1743466620000LL},
        {4, 54, 4, 50, 1, 1743466680000LL},
        {5, 37, 5, 34, 1, 1743466740000LL},
    };
    for (int count = 1; count <= 5; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << OBSERVED << "\n";
    }
}
"""
    output = run_emitted_tu(transpile(source), driver.replace("OBSERVED", observed),
                            opt="-O0", label="issue 433")
    assert output.splitlines() == expected

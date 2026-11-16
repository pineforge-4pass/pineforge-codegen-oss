"""Native Pine formatting pinned only to retained TradingView observations.

R3 decoded-report.json SHA69de08f0 /filledOrders/0/c ends K=09448:46173.
C6 public number_rendering tape SHA1fbab5eb, entry Signal zeros:1.20;
number_edge tape SHA2ae014f6, Signals group:1,234.57 and negcurr:-$1,234.57.
Their Pine sources bind each value and format. No TV fact was found for a
value wider than 00000, negative integer padding, #,##0 or 000.00; those
specific interactions are intentionally not assigned guessed expectations.
"""

from __future__ import annotations

import json

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu, skip_if_no_engine_lib


PINE = '''//@version=6
strategy("integer padding native unit", currency=currency.NONE)
string actual_a = str.tostring(9448, "00000")
string actual_b = str.tostring(46173, "00000")
string actual_group = str.tostring(1234.56789, "#,###.##")
string actual_fraction = str.tostring(1.2, "#.00")
string actual_negative = str.format("{0,number,currency}", -1234.567)
string actual_packet = str.repeat("~", 242) + "|K=" + actual_a + ":" + actual_b
int actual_width = str.length(actual_packet)
'''

DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << "a\t" << strategy.actual_a << '\n'
              << "b\t" << strategy.actual_b << '\n'
              << "group\t" << strategy.actual_group << '\n'
              << "fraction\t" << strategy.actual_fraction << '\n'
              << "negative\t" << strategy.actual_negative << '\n'
              << "width\t" << strategy.actual_width << '\n';
}
'''


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_pine_patterns_match_retained_tv(opt: str) -> None:
    skip_if_no_engine_lib()
    output = run_emitted_tu(transpile(PINE), DRIVER, opt=opt,
                            label="tv-integer-padding")
    actual = dict(line.split("\t", 1) for line in output.splitlines())
    expected = {"a": "09448", "b": "46173", "group": "1,234.57",
                "fraction": "1.20", "negative": "-$1,234.57", "width": "256"}
    print(json.dumps({"optimization": opt, "actual": actual, "expected": expected},
                     sort_keys=True))
    assert actual == expected

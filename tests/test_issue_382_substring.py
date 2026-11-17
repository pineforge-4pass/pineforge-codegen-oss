"""Issue #382, item 9: invalid substring bounds stop; na end returns na.

The issue also measures an na begin as zero. Check both integer sentinels
and floating na expressions before any conversion to an unsigned position.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("issue 382 substring")\nplot(close)\n'
DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1, "1", "1");
    std::cout << !strategy.last_error().empty() << " "
              << strategy.afterSubstring << "\n";
    // OBSERVE
}
'''


def _run(body, observe, opt):
    cpp = transpile(HEADER + body + "\nafterSubstring = 1\n")
    return run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe),
        opt=opt, label="issue 382 substring",
    )


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body", [
    'i = int(close) - 2\nslice_result = str.substring("abc", i)\n',
    'j = int(close)\nslice_result = str.substring("abc", 2, j)\n',
], ids=["negative-begin", "end-before-begin"])
def test_runtime_invalid_bounds_stop_before_the_next_statement(body, opt):
    assert _run(body, "", opt) == "1 0\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_na_end_returns_na_and_the_script_continues(opt):
    body = '''
i = int(close) - 1
int e = close > 0 ? na : 3
floatingEnd = close > 0 ? na : 3
missingIntegerEnd = na(str.substring("abc", i, e))
missingFloatingEnd = na(str.substring("abc", i, floatingEnd))
missingLiteralEnd = na(str.substring("abc", i, na))
'''
    observe = r'''
    std::cout << strategy.missingIntegerEnd << " "
              << strategy.missingFloatingEnd << " "
              << strategy.missingLiteralEnd << "\n";
'''
    assert _run(body, observe, opt) == "0 1\n1 1 1\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_na_begin_explicitly_starts_at_zero(opt):
    body = '''
int b = close > 0 ? na : 1
floatingBegin = close > 0 ? na : 1
integerTail = str.substring("abc", b)
floatingTail = str.substring("abc", floatingBegin)
literalTail = str.substring("abc", na)
'''
    observe = r'''
    std::cout << strategy.integerTail << " "
              << strategy.floatingTail << " "
              << strategy.literalTail << "\n";
'''
    assert _run(body, observe, opt) == "0 1\nabc abc abc\n"

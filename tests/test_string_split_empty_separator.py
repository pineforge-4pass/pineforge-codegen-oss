"""str.split with an empty separator gives one part per UTF-16 unit.

TradingView splits "axb" with "" into three parts, an accent or a CJK character
between two letters into three, and an emoji between two letters into four (an
emoji is two UTF-16 units; measured). The empty text split by an empty separator
gives one part (the array has size 1; measured). The generated code used to
return the whole string as one part, even for plain ASCII. Each row transpiles a small
script, builds the emitted translation unit with the engine headers and library,
runs the strategy on three bars and reads the script's own variables.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu

HEADER = '//@version=6\nstrategy("string split")\n'

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
        std::cerr << strategy.last_error() << "\n";
        return 14;
    }
    // OBSERVE
    return 0;
}
'''


def _run(script: str, observe: str) -> str:
    cpp = transpile(HEADER + script)
    return run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe), opt="-O0", label="string split",
    )


COUNT = 'std::cout << strategy.n << "\\n";'

EMPTY_SEPARATOR_ROWS = [
    pytest.param(
        'p = str.split("axb", "")\nn = array.size(p)\nplot(n)\n',
        COUNT, "3\n", id="ascii-gives-three-parts",
    ),
    pytest.param(
        'p = str.split("aéb", "")\nn = array.size(p)\n'
        'm = array.get(p, 1) == "é" ? 1 : 0\nplot(n + m)\n',
        'std::cout << strategy.n << " " << strategy.m << "\\n";', "3 1\n",
        id="an-accent-is-one-part",
    ),
    pytest.param(
        'p = str.split("a你b", "")\nn = array.size(p)\n'
        'm = array.get(p, 1) == "你" ? 1 : 0\nplot(n + m)\n',
        'std::cout << strategy.n << " " << strategy.m << "\\n";', "3 1\n",
        id="a-cjk-character-is-one-part",
    ),
    pytest.param(
        'p = str.split("a\U0001f600b", "")\nn = array.size(p)\n'
        'f = array.get(p, 0) == "a" ? 1 : 0\nl = array.get(p, 3) == "b" ? 1 : 0\n'
        'plot(n + f + l)\n',
        'std::cout << strategy.n << " " << strategy.f << " " << strategy.l << "\\n";',
        "4 1 1\n", id="an-emoji-is-two-parts",
    ),
    # TradingView splits "abc" by a string variable that holds na into three
    # parts (read): a string na is the empty text, so it is an empty separator.
    pytest.param(
        'string sep = na\np = str.split("abc", sep)\nn = array.size(p)\nplot(n)\n',
        COUNT, "3\n", id="measured-a-string-variable-holding-na-splits-per-unit",
    ),
]


@pytest.mark.parametrize("script, observe, expected", EMPTY_SEPARATOR_ROWS)
def test_an_empty_separator_splits_into_utf16_units(script, observe, expected):
    assert _run(script, observe) == expected


EMPTY_TEXT_ROWS = [
    # TradingView's own str.split("", "") returns an array of size 1 (read).
    pytest.param(
        'p = str.split("", "")\nn = array.size(p)\nplot(n)\n',
        COUNT, "1\n", id="measured-the-empty-text-gives-one-part",
    ),
    # What that one part holds was not read on TradingView: the engine's own
    # split gave the empty text for it and the choice is kept.
    pytest.param(
        'p = str.split("", "")\nn = str.length(array.get(p, 0))\nplot(n)\n',
        COUNT, "0\n", id="design-the-part-is-the-empty-text",
    ),
    # The engine's own split, for a separator that is not empty: unchanged.
    pytest.param(
        'p = str.split("", ",")\nn = array.size(p)\nplot(n)\n',
        COUNT, "1\n", id="control-the-empty-text-split-at-a-comma",
    ),
]


@pytest.mark.parametrize("script, observe, expected", EMPTY_TEXT_ROWS)
def test_the_empty_text_gives_one_part(script, observe, expected):
    assert _run(script, observe) == expected


CONTROL_ROWS = [
    pytest.param(
        'p = str.split("a,b,c", ",")\nn = array.size(p)\nplot(n)\n',
        COUNT, "3\n", id="control-split-at-a-comma",
    ),
    pytest.param(
        'p = str.split("abc", "x")\nn = array.size(p)\nplot(n)\n',
        COUNT, "1\n", id="control-separator-not-found",
    ),
    pytest.param(
        'p = str.split("aéb", "é")\nn = array.size(p)\n'
        'f = array.get(p, 0) == "a" ? 1 : 0\nl = array.get(p, 1) == "b" ? 1 : 0\n'
        'plot(n + f + l)\n',
        'std::cout << strategy.n << " " << strategy.f << " " << strategy.l << "\\n";',
        "2 1 1\n", id="control-split-at-a-character",
    ),
]


@pytest.mark.parametrize("script, observe, expected", CONTROL_ROWS)
def test_a_separator_that_is_not_empty_splits_as_before(script, observe, expected):
    assert _run(script, observe) == expected

"""Issue #382, item 8: measured removed/kept blanks and ASCII controls.

The issue also states that trimming keeps an interior no-break space.
Empty and all-blank strings control the trimming boundaries.
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
    "source, expected",
    [
        pytest.param("\u00a0a\u00a0", "a", id="measured-no-break-space"),
        pytest.param("\u2003a\u2003", "a", id="measured-em-space"),
        pytest.param("\ufeffa", "a", id="measured-zero-width-no-break-space"),
        pytest.param("\x0ba\x0c", "a", id="measured-vertical-tab-form-feed"),
        pytest.param("\u3000a\u3000", "a", id="measured-ideographic-space"),
        pytest.param("\u1680a\u1680", "a", id="measured-ogham-space-mark"),
        pytest.param("\u2028a\u2028", "a", id="measured-line-separator"),
        pytest.param("\u0085a\u0085", "\u0085a\u0085", id="measured-kept-next-line"),
        pytest.param("\x1fa\x1f", "\x1fa\x1f", id="measured-kept-unit-separator"),
        pytest.param("\u200ba\u200b", "\u200ba\u200b", id="measured-kept-zero-width-space"),
        pytest.param(
            "\u180ea\u180e", "\u180ea\u180e", id="measured-kept-mongolian-vowel-separator",
        ),
        pytest.param("a\u00a0b", "a\u00a0b", id="issue-kept-interior-no-break-space"),
        pytest.param(" \t\r\na\t b\n\r\t ", "a\t b", id="control-ascii"),
        pytest.param("", "", id="control-empty"),
        pytest.param(" \t\r\n", "", id="control-all-ascii-blanks"),
    ],
)
def test_issue_382_trim(source, expected):
    # Pine supports \t and \n escapes; a carriage return must stay literal.
    literal = (
        source.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\t", "\\t").replace("\n", "\\n")
    )
    cpp = transpile(
        '//@version=6\nstrategy("issue 382 trim")\n'
        f'result = str.trim("{literal}")\n'
        "plot(close)\n"
    )
    assert run_emitted_tu(
        cpp, DRIVER, opt="-O0", label="issue 382 str.trim",
    ) == expected

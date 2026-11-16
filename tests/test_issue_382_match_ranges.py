"""Issue #382: a class range consumes one character between its endpoints.

These rows retain the reported range and its measured endpoint controls.
Unmeasured extreme-width range proposals remain reference-only.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._compile import run_emitted_tu


RANGE_CASES = (
    ("aéb", "a[à-ÿ]b", "aéb"),
    # Both endpoints are included; their immediate neighbours are excluded.
    ("aàb", "a[à-ÿ]b", "aàb"),
    ("aÿb", "a[à-ÿ]b", "aÿb"),
    ("aßb", "a[à-ÿ]b", ""),
    ("aĀb", "a[à-ÿ]b", ""),
)


DRIVER = r"""
#include <iostream>

int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0;
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
}
"""


@pytest.fixture(scope="module")
def matched_ranges():
    source = '//@version=6\nstrategy("issue 382 match ranges")\n'
    for i, (text, pattern, _) in enumerate(RANGE_CASES):
        source += (
            f"result{i} = str.match({pine_string_literal(text)}, "
            f"{pine_string_literal(pattern)})\n"
        )
    source += "plot(str.length(result0))\n"
    observe = "\n".join(
        f'std::cout << strategy.result{i} << "\\n";'
        for i in range(len(RANGE_CASES))
    )
    output = run_emitted_tu(
        transpile(source), DRIVER.replace("// OBSERVE", observe),
        opt="-O0", label="issue 382 str.match ranges",
    ).splitlines()
    assert len(output) == len(RANGE_CASES), output
    return output


@pytest.mark.parametrize("case_index", range(len(RANGE_CASES)))
def test_issue_382_match_codepoint_range(case_index, matched_ranges):
    text, pattern, expected = RANGE_CASES[case_index]
    actual = matched_ranges[case_index]
    assert actual == expected, (text, pattern, actual, expected)

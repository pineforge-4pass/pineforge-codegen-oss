"""Issue #382, item 4: regex atoms consume characters, not UTF-8 bytes.

The first four rows are the issue's measured TradingView examples. The next
three exercise its stated counted-repeat, non-word and non-space rules on the
same texts. The ASCII controls are its verified normalization workaround.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._compile import run_emitted_tu


ROWS = (
    ("aéb", "a.b", "aéb"),
    ("aéb", "a[^x]b", "aéb"),
    ("aéb", "a[é]b", "aéb"),
    ("aééb", "aé+b", "aééb"),
    ("aééb", "a.{2}b", "aééb"),
    ("aéb", r"a\Wb", "aéb"),
    ("aéb", r"a\Sb", "aéb"),
    ("aeb", "a.b", "aeb"),
    ("aeb", "a[e]b", "aeb"),
    ("aeb", "a[a-z]b", "aeb"),
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


def test_issue_382_match_examples_and_ascii_controls():
    source = '//@version=6\nstrategy("issue 382 match")\n'
    for i, (text, pattern, _) in enumerate(ROWS):
        source += (
            f"result{i} = str.match({pine_string_literal(text)}, "
            f"{pine_string_literal(pattern)})\n"
        )
    source += "plot(str.length(result0))\n"
    observe = "\n".join(
        f'std::cout << strategy.result{i} << "\\n";' for i in range(len(ROWS))
    )
    output = run_emitted_tu(
        transpile(source), DRIVER.replace("// OBSERVE", observe),
        opt="-O0", label="issue 382 str.match",
    ).splitlines()
    assert len(output) == len(ROWS), output
    for (text, pattern, expected), actual in zip(ROWS, output):
        assert actual == expected, (text, pattern, actual, expected)


# One translation unit per row, so each row has its own outcome. The ASCII
# rows hold on the base lowering too (bytes and characters agree there); the
# pattern-sensitive rows give one text several patterns whose answers differ
# (a proper substring, a capture group, no match), so an answer that ignores
# the pattern, or returns the whole source, cannot satisfy them.
SINGLE_ROWS = [
    pytest.param("aeb", "a.b", "aeb", id="ascii-dot"),
    pytest.param("aeb", "a[e]b", "aeb", id="ascii-class"),
    pytest.param("aeb", "a[a-z]b", "aeb", id="ascii-range"),
    pytest.param("xaebz", "a.b", "aeb", id="ascii-proper-substring"),
    pytest.param("xaebz", "a(.)b", "e", id="ascii-capture-group"),
    pytest.param("xaebz", r"a\db", "", id="ascii-no-match"),
    pytest.param("xaébz", "a.b", "aéb", id="unicode-proper-substring"),
    pytest.param("xaébz", "a(.)b", "é", id="unicode-capture-group"),
    pytest.param("xaébz", "a.{2}b", "", id="unicode-no-match"),
]


@pytest.mark.parametrize("text, pattern, expected", SINGLE_ROWS)
def test_match_answers_one_text_and_pattern(text, pattern, expected):
    source = (
        '//@version=6\nstrategy("issue 382 match row")\n'
        f"matched = str.match({pine_string_literal(text)}, "
        f"{pine_string_literal(pattern)})\n"
        "plot(str.length(matched))\n"
    )
    output = run_emitted_tu(
        transpile(source),
        DRIVER.replace("// OBSERVE", 'std::cout << "[" << strategy.matched << "]\\n";'),
        opt="-O0", label="issue 382 str.match row",
    )
    assert output == f"[{expected}]\n", (text, pattern, output)

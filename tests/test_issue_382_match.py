"""Issue #382, item 4: regex atoms consume characters, not UTF-8 bytes.

The first four rows are the issue's measured TradingView examples. The next
three exercise its stated counted-repeat, non-word and non-space rules on the
same texts. The ASCII controls are its verified normalization workaround.
"""

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

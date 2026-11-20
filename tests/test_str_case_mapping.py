"""Native coverage for the Unicode case mapping of str.upper and str.lower.

The eleven mappings captured from TradingView are listed in the comment above
the vector table; every other row is derived from the Unicode 15.0 data."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("Unicode case mapping")\n'


# Test vectors for the case lane: (label, input, upper, lower), every text written with
# \uXXXX / \UXXXXXXXX escapes only. upper and lower are the interpreter's str.upper() and
# str.lower() (Python 3.12.3, Unicode 15.0.0).
# Captured from TradingView on 2026-11-20 (the eleven cases of the capture u-case-set): e-acute-lower,
# e-acute-upper, sharp-s, lone-capital-sigma, final-sigma-word, digraph-dz-lower, dotted-capital-i,
# dotless-i, latin1 (lower of the capitals, and ntilde/u-acute upper). Every other row is derived from the
# Unicode Character Database through the interpreter, not captured.
CASE_VECTORS = [
    ('ascii', 'Hello, World 123', 'HELLO, WORLD 123', 'hello, world 123'),
    ('empty', '', '', ''),
    ('latin1', '\u00e0\u00e9\u00ee\u00f5\u00fc \u00c0\u00c9\u00ce\u00d5\u00dc', '\u00c0\u00c9\u00ce\u00d5\u00dc \u00c0\u00c9\u00ce\u00d5\u00dc', '\u00e0\u00e9\u00ee\u00f5\u00fc \u00e0\u00e9\u00ee\u00f5\u00fc'),
    ('sharp-s', 'stra\u00dfe', 'STRASSE', 'stra\u00dfe'),
    ('e-acute-lower', '\u00e9', '\u00c9', '\u00e9'),
    ('e-acute-upper', '\u00c9', '\u00c9', '\u00e9'),
    ('n-tilde-u-acute', '\u00f1andu\u0301 \u00f1and\u00fa', '\u00d1ANDU\u0301 \u00d1AND\u00da', '\u00f1andu\u0301 \u00f1and\u00fa'),
    ('dotted-capital-i', '\u0130', '\u0130', 'i\u0307'),
    ('dotless-i', '\u0131', 'I', '\u0131'),
    ('kelvin-sign', '\u212a', '\u212a', 'k'),
    ('long-s', '\u017f', 'S', '\u017f'),
    ('lone-capital-sigma', '\u03a3', '\u03a3', '\u03c3'),
    ('final-sigma-word', '\u0391\u03a3', '\u0391\u03a3', '\u03b1\u03c2'),
    ('sigma-between-letters', '\u0391\u03a3\u0391', '\u0391\u03a3\u0391', '\u03b1\u03c3\u03b1'),
    ('sigma-final-before-period', '\u0391\u03a3.', '\u0391\u03a3.', '\u03b1\u03c2.'),
    ('sigma-final-after-apostrophe', '\u0391\u0027\u03a3', '\u0391\u0027\u03a3', '\u03b1\u0027\u03c2'),
    ('sigma-before-space-then-letter', '\u0391\u03a3 \u0391', '\u0391\u03a3 \u0391', '\u03b1\u03c2 \u03b1'),
    ('sigma-after-space', '\u0391 \u03a3', '\u0391 \u03a3', '\u03b1 \u03c3'),
    ('sigma-then-ignorable-then-cased', '\u0391\u03a3.\u0391', '\u0391\u03a3.\u0391', '\u03b1\u03c3.\u03b1'),
    ('sigma-then-combining-then-cased', '\u0391\u03a3\u0301\u0391', '\u0391\u03a3\u0301\u0391', '\u03b1\u03c3\u0301\u03b1'),
    ('two-sigmas', '\u03a3\u03a3', '\u03a3\u03a3', '\u03c3\u03c2'),
    ('greek-word', '\u039f\u0394\u03a5\u03a3\u03a3\u0395\u03a5\u03a3', '\u039f\u0394\u03a5\u03a3\u03a3\u0395\u03a5\u03a3', '\u03bf\u03b4\u03c5\u03c3\u03c3\u03b5\u03c5\u03c2'),
    ('digraph-dz-lower', '\u01c6', '\u01c4', '\u01c6'),
    ('digraph-dz-title', '\u01c5', '\u01c4', '\u01c6'),
    ('digraph-dz-upper', '\u01c4', '\u01c4', '\u01c6'),
    ('ligature-ff', '\ufb00', 'FF', '\ufb00'),
    ('ligature-ffi', '\ufb03', 'FFI', '\ufb03'),
    ('armenian-ech-yiwn', '\u0587', '\u0535\u0552', '\u0587'),
    ('greek-ypogegrammeni', '\u1f80', '\u1f08\u0399', '\u1f80'),
    ('greek-prosgegrammeni', '\u1fb3', '\u0391\u0399', '\u1fb3'),
    ('n-apostrophe', '\u0149', '\u02bcN', '\u0149'),
    ('j-caron', '\u01f0', 'J\u030c', '\u01f0'),
    ('deseret-small', '\U00010428', '\U00010400', '\U00010428'),
    ('deseret-capital', '\U00010400', '\U00010400', '\U00010428'),
    ('adlam', '\U0001e922', '\U0001e900', '\U0001e922'),
    ('cjk-and-emoji', '\u4e2d\u6587 \U0001f600', '\u4e2d\u6587 \U0001f600', '\u4e2d\u6587 \U0001f600'),
    ('cherokee-small', '\uab70', '\u13a0', '\uab70'),
    ('georgian-mtavruli', '\u1c90', '\u1c90', '\u10d0'),
    ('mixed', 'Stra\u00dfe \u03a3\u03af\u03c3\u03c5\u03c6\u03bf\u03c2 \u0130stanbul \u01c5', 'STRASSE \u03a3\u038a\u03a3\u03a5\u03a6\u039f\u03a3 \u0130STANBUL \u01c4', 'stra\u00dfe \u03c3\u03af\u03c3\u03c5\u03c6\u03bf\u03c2 i\u0307stanbul \u01c6'),
]


def _pine_string(value: str) -> str:
    escaped = (value.replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("\n", "\\n")
               .replace("\r", "\\r")
               .replace("\t", "\\t"))
    return f'"{escaped}"'


def _case_source(function: str, value: str, *, series: bool) -> str:
    literal = _pine_string(value)
    argument = literal
    if series:
        argument = f"bar_index % 2 == 0 ? {literal} : {literal}"
    return HEADER + f"out = str.{function}({argument})\n"


def _hex_driver() -> str:
    return r'''
#include <iomanip>
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(1);
    bars[0].open = 1.0;
    bars[0].high = 1.0;
    bars[0].low = 1.0;
    bars[0].close = 1.0;
    bars[0].volume = 1.0;
    bars[0].timestamp = 1704067200000LL;
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    for (unsigned char byte : strategy.out)
        std::cout << std::hex << std::setw(2) << std::setfill('0')
                  << static_cast<unsigned>(byte);
}
'''


@pytest.mark.parametrize(
    "function,vector,series",
    [
        pytest.param(function, vector, series,
                     id=f"{function}-{vector[0]}-{'series' if series else 'constant'}")
        for function, column in (("upper", 2), ("lower", 3))
        for vector in CASE_VECTORS
        for series in (False, True)
    ],
)
def test_case_mapping_vectors(function, vector, series):
    _label, value, upper, lower = vector
    expected = (upper if function == "upper" else lower).encode("utf-8").hex()
    cpp = transpile(_case_source(function, value, series=series))
    assert run_emitted_tu(cpp, _hex_driver(), opt="-O0",
                          label=f"{function} {vector[0]}") == expected


def test_case_helper_is_absent_when_unused():
    cpp = transpile(HEADER + "out = 1\n")
    assert "_pf_case_" not in cpp
    assert "_pf_case_upper_simple" not in cpp


def test_upper_only_helper_runs():
    cpp = transpile(_case_source("upper", "hello", series=False))
    assert run_emitted_tu(cpp, _hex_driver(), opt="-O0",
                          label="upper-only helper") == "48454c4c4f"


def test_case_argument_is_evaluated_once_in_a_loop_and_outside():
    cpp = transpile(HEADER + """var int counter = 0
bump() =>
    counter += 1
    "a"
text = str.upper(bump())
for i = 0 to 2
    text := str.lower(bump())
out = counter
""")
    driver = r'''
#include <iostream>
int main() {
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 1704067200000LL};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1, "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    std::cout << strategy.out;
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O0",
                          label="case argument evaluation") == "4"


def test_case_mapping_preserves_invalid_utf8_bytes():
    cpp = transpile(_case_source("upper", "é", series=False))
    driver = r'''
#include <iostream>
#include <string>
int main() {
    const std::string lone{char(0xFF)};
    const std::string truncated{char(0xE2), char(0x82)};
    const std::string overlong{char(0xC0), char(0x80)};
    const std::string mixed{char(0xFF), char(0xC3), char(0xA9),
                            char(0xE2), char(0x82)};
    const std::string expected_mixed{char(0xFF), char(0xC3), char(0x89),
                                     char(0xE2), char(0x82)};
    const bool ok = _pf_case_upper(lone) == lone
        && _pf_case_upper(truncated) == truncated
        && _pf_case_upper(overlong) == overlong
        && _pf_case_upper(mixed) == expected_mixed;
    std::cout << (ok ? "ok" : "bad");
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O0",
                          label="invalid UTF-8 case mapping") == "ok"


def test_case_mapping_ascii_bytes_match_c_locale_rules():
    cpp = transpile(_case_source("upper", "a", series=False))
    driver = r'''
#include <iostream>
#include <string>
int main() {
    std::string input;
    for (unsigned value = 0; value < 0x80; ++value)
        input.push_back(static_cast<char>(value));
    std::string expected_upper = input;
    std::string expected_lower = input;
    for (unsigned value = 0; value < 0x80; ++value) {
        if (value >= 'a' && value <= 'z')
            expected_upper[value] = static_cast<char>(value - ('a' - 'A'));
        if (value >= 'A' && value <= 'Z')
            expected_lower[value] = static_cast<char>(value + ('a' - 'A'));
    }
    std::cout << ((_pf_case_upper(input) == expected_upper
                   && _pf_case_lower(input) == expected_lower) ? "ok" : "bad");
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O0",
                          label="ASCII case mapping") == "ok"


@pytest.mark.parametrize("name", ["upper", "lower"])
def test_case_helper_is_kept_for_a_call_in_a_request_timeframe(name):
    # A request timeframe is rendered before the generator resets its per-run
    # flags; the helper must still be emitted when this is the script's only
    # call (the review's regression case: the old inline lambda built).
    cpp = transpile(
        HEADER
        + f'out = request.security(syminfo.tickerid, str.{name}(input.timeframe("D")), close)\n'
    )
    assert f"inline std::string _pf_case_{name}(" in cpp
    compile_cpp(cpp, label=f"case helper in a request timeframe ({name})")


def test_final_sigma_is_not_decided_across_an_invalid_byte():
    # A lone surrogate half (ED A0 80, left by cutting an emoji) next to a
    # capital sigma is neither cased nor case-ignorable: the scan stops there,
    # as for any other character that is neither (the final sigma after a
    # cased letter, and a non-final one before it).
    cpp = transpile(_case_source("lower", "a", series=False))
    driver = r'''
#include <iostream>
#include <string>
int main() {
    const std::string after{char(0xCE), char(0x91), char(0xCE), char(0xA3),
                            char(0xED), char(0xA0), char(0x80)};
    const std::string expected_after{char(0xCE), char(0xB1), char(0xCF), char(0x82),
                                     char(0xED), char(0xA0), char(0x80)};
    const std::string before{char(0xED), char(0xA0), char(0x80), char(0xCE), char(0xA3),
                             char(0xCE), char(0x91)};
    const std::string expected_before{char(0xED), char(0xA0), char(0x80), char(0xCF), char(0x83),
                                      char(0xCE), char(0xB1)};
    std::cout << ((_pf_case_lower(after) == expected_after
                   && _pf_case_lower(before) == expected_before) ? "ok" : "bad");
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O0",
                          label="sigma next to an invalid byte") == "ok"

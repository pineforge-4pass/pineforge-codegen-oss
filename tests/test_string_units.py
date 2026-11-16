"""str.length, str.pos and str.substring count and cut text in UTF-16 units.

TradingView measures Pine text in UTF-16 code units: a character outside the
Basic Multilingual Plane (an emoji) is two units and every other character is
one. The generated code keeps text as UTF-8, so a count of its bytes differs for
every character that is not plain ASCII.

Each row transpiles a small script, builds the emitted translation unit with the
engine headers and library, runs the strategy on three bars and reads the
script's own variables.

Row names say where the expected value comes from:

* ``measured-*``: the script text and the value TradingView returned for it
  (str.length, str.pos and str.substring on the strings "a" + one character +
  "b"; and str.substring at its edges on "abc" and on a, an emoji, b:
  TradingView refuses the two begin positions outside the text when it compiles
  a script whose arguments are constants, where the engine stops the run);
* ``derived-*``: arithmetic on the same unit (an emoji counts two), not a
  separate reading;
* ``design-*``: a choice the measurement does not decide. A cut between the two
  units of an emoji keeps each half as its own three-byte sequence, the form
  str.split gives its parts, so the length of the half is one unit;
* ``control-*``: a case in which bytes, code points and UTF-16 units agree.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu

HEADER = '//@version=6\nstrategy("string units")\n'

DRIVER = r'''
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

static void hex(const std::string& value) {
    for (unsigned char c : value) std::printf("%02x", c);
    std::printf("\n");
}

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

ACCENT = "\U000000e9"
CJK = "\U00004e2d"
EMOJI = "\U0001F600"
FEFF = "\U0000feff"


def _run(script: str, observe: str) -> str:
    cpp = transpile(HEADER + script)
    return run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe), opt="-O0", label="string units",
    )


NUMBER = 'std::cout << strategy.n << "\\n";'

LENGTH_ROWS = [
    pytest.param(ACCENT, 1, id="measured-accent"),
    pytest.param(CJK, 1, id="measured-cjk-character"),
    pytest.param(EMOJI, 2, id="measured-emoji-is-two-units"),
    pytest.param("a", 1, id="measured-ascii-letter"),
    pytest.param(f"a{FEFF}b", 3, id="measured-zero-width-no-break-space-between-letters"),
    pytest.param(f"a{EMOJI}b", 4, id="derived-emoji-between-two-letters"),
    pytest.param(EMOJI + EMOJI, 4, id="derived-two-emoji"),
    pytest.param(f"a{CJK}b", 3, id="derived-cjk-between-two-letters"),
    pytest.param("", 0, id="control-empty"),
    pytest.param("abc", 3, id="control-ascii-word"),
]


@pytest.mark.parametrize("text, expected", LENGTH_ROWS)
def test_length_counts_utf16_units(text, expected):
    script = f's = "{text}"\nn = str.length(s)\nplot(n)\n'
    assert _run(script, NUMBER) == f"{expected}\n"


POS_ROWS = [
    # The position of "b" in "a" + one character + "b".
    pytest.param(f"a{ACCENT}b", "b", 2, id="measured-after-an-accent"),
    pytest.param(f"a{CJK}b", "b", 2, id="measured-after-a-cjk-character"),
    pytest.param(f"a{EMOJI}b", "b", 3, id="measured-after-an-emoji"),
    pytest.param("aab", "b", 2, id="measured-after-an-ascii-letter"),
    pytest.param(f"{EMOJI}{EMOJI}b", "b", 4, id="derived-after-two-emoji"),
    pytest.param(f"a{EMOJI}b", EMOJI, 1, id="derived-the-emoji-itself"),
    pytest.param(f"a{EMOJI}b", "x", -1, id="control-not-found"),
    pytest.param(f"a{CJK}b", CJK, 1, id="control-the-bmp-character-itself"),
]


@pytest.mark.parametrize("text, needle, expected", POS_ROWS)
def test_pos_counts_utf16_units(text, needle, expected):
    script = f'p = str.pos("{text}", "{needle}")\nplot(p)\n'
    assert _run(script, 'std::cout << strategy.p << "\\n";') == f"{expected}\n"


TAIL_ROWS = [
    # The length of the substring that starts at index 2 of "a" + one character + "b".
    pytest.param(ACCENT, 1, id="measured-accent"),
    pytest.param(CJK, 1, id="measured-cjk-character"),
    pytest.param(EMOJI, 2, id="measured-emoji-leaves-a-half-and-the-letter"),
    pytest.param("a", 1, id="measured-ascii-letter"),
]


@pytest.mark.parametrize("character, expected", TAIL_ROWS)
def test_the_tail_of_a_substring_counts_utf16_units(character, expected):
    script = (f's = "a{character}b"\nt = str.substring(s, 2)\n'
              'n = str.length(t)\nplot(n)\n')
    assert _run(script, NUMBER) == f"{expected}\n"


HEAD_ROWS = [
    # 1 when the first two units of "a" + one character + "b" are "a" and the character.
    pytest.param(ACCENT, 1, id="measured-accent"),
    pytest.param(CJK, 1, id="measured-cjk-character"),
    pytest.param(EMOJI, 0, id="measured-emoji-is-cut-in-half"),
    pytest.param("a", 1, id="measured-ascii-letter"),
]


@pytest.mark.parametrize("character, expected", HEAD_ROWS)
def test_the_head_of_a_substring_counts_utf16_units(character, expected):
    script = (f's = "a{character}b"\n'
              f'v = str.substring(s, 0, 2) == "a{character}" ? 1 : 0\nplot(v)\n')
    assert _run(script, 'std::cout << strategy.v << "\\n";') == f"{expected}\n"


SUBSTRING_ROWS = [
    pytest.param(f"a{CJK}b", 1, 2, "e4b8ad", id="derived-one-cjk-character"),
    pytest.param(f"{CJK}{CJK}b", 1, 3, "e4b8ad62", id="derived-cjk-and-a-letter"),
    pytest.param(f"a{EMOJI}b", 1, 3, "f09f9880", id="derived-the-whole-emoji"),
    pytest.param(f"a{EMOJI}b", 3, 4, "62", id="derived-the-letter-after-the-emoji"),
    pytest.param(f"a{EMOJI}b", 0, 4, "61f09f988062", id="derived-everything"),
    pytest.param(f"a{EMOJI}b", 0, 2, "61eda0bd", id="design-cut-after-the-high-half"),
    pytest.param(f"a{ACCENT}b", 1, 2, "c3a9", id="derived-one-accent"),
    pytest.param("abc", 1, 2, "62", id="control-ascii-middle-letter"),
]


@pytest.mark.parametrize("text, begin, end, expected_hex", SUBSTRING_ROWS)
def test_a_substring_cuts_at_utf16_units(text, begin, end, expected_hex):
    script = (f't = str.substring("{text}", {begin}, {end})\n'
              'n = str.length(t)\nplot(n)\n')
    assert _run(script, "hex(strategy.t);") == f"{expected_hex}\n"


TO_THE_END_ROWS = [
    pytest.param(f"a{EMOJI}b", 3, "62", id="derived-from-the-letter-after-the-emoji"),
    pytest.param(f"a{EMOJI}b", 2, "edb88062", id="design-from-the-low-half"),
    pytest.param(f"a{EMOJI}b", 4, "", id="derived-from-the-end-is-empty"),
    pytest.param(f"a{CJK}b", 2, "62", id="derived-after-a-cjk-character"),
    pytest.param("abc", 1, "6263", id="control-ascii-tail"),
]


@pytest.mark.parametrize("text, begin, expected_hex", TO_THE_END_ROWS)
def test_a_substring_to_the_end_starts_at_a_utf16_unit(text, begin, expected_hex):
    script = (f't = str.substring("{text}", {begin})\n'
              'n = str.length(t)\nplot(n)\n')
    assert _run(script, "hex(strategy.t);") == f"{expected_hex}\n"


# str.substring at its edges, read on TradingView on "abc" and on a, an emoji, b
# (four UTF-16 units, six bytes): the expected value is the length of the result.
# The begin must lie in 0..length (a begin equal to the length gives the empty
# text); an end beyond the length is cut to the length.
EDGE_ROWS = [
    pytest.param("abc", 3, None, 0, id="measured-ascii-begin-at-length"),
    pytest.param("abc", 1, 9, 2, id="measured-ascii-end-beyond-length"),
    pytest.param("abc", 3, 3, 0, id="measured-ascii-begin-and-end-at-length"),
    pytest.param("abc", 3, 9, 0, id="measured-ascii-begin-at-length-end-beyond"),
    pytest.param(f"a{EMOJI}b", 4, None, 0, id="measured-emoji-begin-at-length"),
    pytest.param(f"a{EMOJI}b", 1, 9, 3, id="measured-emoji-end-beyond-length"),
    pytest.param(f"a{EMOJI}b", 4, 4, 0, id="measured-emoji-begin-and-end-at-length"),
    pytest.param(f"a{EMOJI}b", 4, 9, 0, id="measured-emoji-begin-at-length-end-beyond"),
    pytest.param(f"a{EMOJI}b", 2, None, 2, id="measured-emoji-begin-between-its-units"),
    pytest.param(f"a{EMOJI}b", 0, 2, 2, id="measured-emoji-end-between-its-units"),
    pytest.param(f"a{EMOJI}b", 2, 3, 1, id="measured-emoji-low-half"),
    pytest.param(f"a{EMOJI}b", 1, 2, 1, id="measured-emoji-high-half"),
]


@pytest.mark.parametrize("text, begin, end, expected", EDGE_ROWS)
def test_substring_edges_match_tradingview(text, begin, end, expected):
    arguments = f"{begin}" if end is None else f"{begin}, {end}"
    script = (f's = "{text}"\nt = str.substring(s, {arguments})\n'
              'n = str.length(t)\nplot(n)\n')
    assert _run(script, NUMBER) == f"{expected}\n"


EXPRESSION_ROWS = [
    pytest.param(
        'n = str.length(str.tostring(12345) + "x")\nplot(n)\n', NUMBER, "6\n",
        id="control-length-of-a-computed-string",
    ),
    pytest.param(
        'n = str.pos("a" + str.tostring(12345), "3")\nplot(n)\n', NUMBER, "3\n",
        id="control-position-in-a-computed-string",
    ),
    pytest.param(
        f's = "a{EMOJI}"\nt = s + "b"\nn = str.length(t)\nplot(n)\n', NUMBER, "4\n",
        id="derived-length-of-a-variable-built-from-an-emoji",
    ),
    pytest.param(
        f's = "a{EMOJI}b"\nt = str.substring(s, 1, 3)\nn = str.pos(s, t)\nplot(n)\n',
        NUMBER, "1\n", id="derived-the-position-of-a-substring-of-the-same-text",
    ),
]


@pytest.mark.parametrize("script, observe, expected", EXPRESSION_ROWS)
def test_the_arguments_may_be_any_string_expression(script, observe, expected):
    assert _run(script, observe) == expected


HELPER_DEFINITION = "inline int _pf_u16_length("

EMISSION_ROWS = [
    pytest.param('t = str.substring("abc", 1, 2)\nplot(close)\n', True,
                 id="str-substring-alone"),
    pytest.param('p = str.pos("abc", "b")\nplot(close)\n', True, id="str-pos-alone"),
    pytest.param('n = str.length("abc")\nplot(close)\n', True, id="str-length-alone"),
    pytest.param('t = str.upper("abc")\nplot(close)\n', False,
                 id="control-another-string-function"),
    pytest.param("plot(close)\n", False, id="control-no-string-function"),
    # A plot argument is not emitted, so the call emits nothing and needs no helper.
    pytest.param('plot(str.length("abc"))\n', False,
                 id="control-a-call-that-is-only-a-plot-argument"),
]


@pytest.mark.parametrize("script, emitted", EMISSION_ROWS)
def test_the_helpers_are_emitted_only_for_a_call_that_uses_them(script, emitted):
    cpp = transpile(HEADER + script)
    assert (HELPER_DEFINITION in cpp) is emitted


STOP_DRIVER = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    try { strategy.on_source_bar(bar); assert(false); }
    catch (const std::exception& error) { std::cout << error.what(); }
}
'''


def test_a_substring_past_the_end_stops_the_run_and_names_the_units():
    """The text a, an emoji, b is four units and six bytes: begin 5 is past the
    end in units (the stop names 5 and 4), where in bytes it was still inside."""
    cpp = transpile(HEADER + f't = str.substring("a{EMOJI}b", 5)\nplot(close)\n')
    stopped = run_emitted_tu(cpp, STOP_DRIVER, opt="-O2", label="substring past the end")
    assert "basic_string::substr" in stopped
    assert "(which is 5) > this->size() (which is 4)" in stopped


REFUSED_ROWS = [
    pytest.param(4, "(which is 4) > this->size() (which is 3)",
                 id="measured-ascii-begin-beyond-length-stops"),
    pytest.param(-1, "(which is 18446744073709551615) > this->size() (which is 3)",
                 id="measured-ascii-begin-negative-stops"),
]


@pytest.mark.parametrize("begin, detail", REFUSED_ROWS)
def test_a_begin_outside_the_text_stops_the_run(begin, detail):
    """TradingView refuses these two scripts when it compiles them (their
    arguments are constants); the engine stops the run, with the text
    std::string::substr gives. Both refuse; the moment is not compared."""
    cpp = transpile(HEADER + f't = str.substring("abc", {begin})\nplot(close)\n')
    stopped = run_emitted_tu(cpp, STOP_DRIVER, opt="-O2", label="substring begin outside")
    assert "basic_string::substr" in stopped
    assert detail in stopped

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
  "b"; and str.substring at its edges on "abc" and on a, an emoji, b).
  A constant negative begin has a separate compiler-refusal assertion below;
  the positive out-of-range constant still exercises the native runtime guard
  and does not establish compiler agreement;
* ``derived-*``: arithmetic on the same unit (an emoji counts two), not a
  separate reading;
* ``design-*``: a choice the measurement does not decide. A cut between the two
  units of an emoji keeps each half as its own three-byte sequence, the form
  str.split gives its parts, so the length of the half is one unit;
* ``control-*``: a case in which bytes, code points and UTF-16 units agree.

For issue #382 item 8, ``measured-*`` trim rows repeat the issue's four exact
examples. ``contract-derived-*`` rows compose only its supplied removed/kept
characters with other text, interior characters, empty text and runtime inputs;
they are not separate TradingView measurements or a full Unicode whitespace set.
"""

import json

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.diagnostic_codes import UNCATALOGUED
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
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
    // INPUTS
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


def _run(script: str, observe: str, *, opt: str = "-O0",
         inputs: dict[str, str] | None = None, setup: str = "") -> str:
    cpp = transpile(HEADER + script)
    input_setup = "\n".join(
        f"strategy.set_input({json.dumps(name)}, {json.dumps(value, ensure_ascii=False)});"
        for name, value in (inputs or {}).items()
    )
    driver = DRIVER.replace(
        "// INPUTS", input_setup + ("\n" + setup if setup else ""),
    ).replace("// OBSERVE", observe)
    return run_emitted_tu(
        cpp, driver, opt=opt, label="string units",
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
    # Issue #382 item 5: an absent match is na, including after non-ASCII text.
    pytest.param(f"a{EMOJI}b", "x", "na", id="control-not-found"),
    pytest.param(f"a{CJK}b", CJK, 1, id="control-the-bmp-character-itself"),
]


@pytest.mark.parametrize("text, needle, expected", POS_ROWS)
def test_pos_counts_utf16_units(text, needle, expected):
    script = (f'p = str.pos("{text}", "{needle}")\n'
              'missing = na(p)\nplot(p)\n')
    observe = (
        'if (strategy.missing) std::cout << "na\\n"; '
        'else std::cout << strategy.p << "\\n";'
    )
    assert _run(script, observe) == f"{expected}\n"


# Retain the original module selection and run these
# boundary/consumer rows at both optimisation levels. These are native lowering
# checks of the behavior described by the public string fixture.
NATIVE_OPTS = [
    pytest.param("-O0", id="O0"),
    pytest.param("-O2", id="O2"),
]

MISSING_POS_CONSUMERS = [
    pytest.param('nz(str.pos("abc", "x"), 0)', "0\n", id="nz"),
    pytest.param('str.tostring(str.pos("abc", "x"))', "NaN\n", id="tostring"),
]


@pytest.mark.parametrize("opt", NATIVE_OPTS)
@pytest.mark.parametrize("expression, expected", MISSING_POS_CONSUMERS)
def test_missing_pos_literal_consumers(expression, expected, opt):
    # Keep the reported public expressions intact, including direct nesting.
    script = f"observed = {expression}\nplot(close)\n"
    assert _run(script, 'std::cout << strategy.observed << "\\n";', opt=opt) == expected


RUNTIME_POS_ROWS = [
    pytest.param("abc", "x", True, 0, "NaN", id="missing-ascii"),
    pytest.param("seed42", "?", True, 0, "NaN", id="missing-distinct"),
    pytest.param("abc", "b", False, 1, "1", id="found-middle"),
    pytest.param("abc", "a", False, 0, "0", id="found-zero"),
    pytest.param("abc", "", False, 0, "0", id="empty-needle"),
    pytest.param("", "x", True, 0, "NaN", id="empty-haystack"),
    pytest.param("", "", False, 0, "0", id="both-empty"),
    pytest.param("ab", "abc", True, 0, "NaN", id="longer-needle"),
    pytest.param(f"a{EMOJI}b", "b", False, 3, "3", id="found-utf16"),
    pytest.param(f"{CJK}{EMOJI}tail", "l", False, 6, "6", id="found-distinct"),
]


@pytest.mark.parametrize("opt", NATIVE_OPTS)
@pytest.mark.parametrize("text, needle, missing, replaced, rendered", RUNTIME_POS_ROWS)
def test_pos_consumers_use_runtime_inputs(text, needle, missing, replaced, rendered, opt):
    # Every row emits the same Pine source. Only the native input overrides
    # change; expected positions/strings are never injected into that source.
    script = (
        'source_value = input.string("unused text", "Text")\n'
        'needle = input.string("unused needle", "Needle")\n'
        'p = str.pos(source_value, needle)\n'
        'missing = na(p)\n'
        'replaced = nz(p, 0)\n'
        'rendered = str.tostring(p)\n'
        'plot(p)\n'
    )
    observe = (
        'std::cout << strategy.missing << "|" << strategy.replaced '
        '<< "|" << strategy.rendered << "\\n";'
    )
    assert _run(script, observe, opt=opt, inputs={"Text": text, "Needle": needle}) == (
        f"{int(missing)}|{replaced}|{rendered}\n"
    )


@pytest.mark.parametrize("opt", NATIVE_OPTS)
def test_substring_after_absent_delimiter_completes(opt):
    # An unexpected run stop remains a failing native execution in DRIVER,
    # never an expected-stop assertion.
    script = (
        't = str.substring("abc", str.pos("abc", ":") + 1)\n'
        'missing = na(t)\n'
        'completed = true\n'
        'plot(close)\n'
    )
    observe = (
        'std::cout << strategy.completed << " " << strategy.missing << "\\n"; '
        'hex(strategy.t);'
    )
    assert _run(script, observe, opt=opt) == "1 0\n616263\n"


MISSING_START_ROWS = [
    pytest.param("abcdef", "int(na)", id="ascii-int-na"),
    pytest.param(f"a{EMOJI}b", "int(na) + 1", id="unicode-wide-na"),
]


@pytest.mark.parametrize("opt", NATIVE_OPTS)
@pytest.mark.parametrize("text, begin", MISSING_START_ROWS)
def test_substring_missing_start_returns_whole_text(text, begin, opt):
    script = (
        'source_value = input.string("unused text", "Text")\n'
        f't = str.substring(source_value, {begin})\n'
        'missing = na(t)\n'
        'completed = true\n'
        'plot(close)\n'
    )
    observe = (
        'std::cout << strategy.completed << " " << strategy.missing << "\\n"; '
        'hex(strategy.t);'
    )
    assert _run(script, observe, opt=opt, inputs={"Text": text}) == (
        f"1 0\n{text.encode('utf-8').hex()}\n"
    )


MISSING_END_ROWS = [
    pytest.param(
        't = str.substring("abc", 0, str.pos("abc", ":"))\n', {},
        id="literal-zero-begin",
    ),
    pytest.param(
        't = str.substring("abc", 1, str.pos("abc", ":"))\n', {},
        id="literal-nonzero-begin",
    ),
    pytest.param(
        'source_value = input.string("unused text", "Text")\n'
        'needle = input.string("unused needle", "Needle")\n'
        'begin = input.int(1, "Begin")\n'
        't = str.substring(source_value, begin, str.pos(source_value, needle))\n',
        {"Text": f"r{EMOJI}tail", "Needle": "!", "Begin": "2"},
        id="runtime-utf16-nonzero-begin",
    ),
]


@pytest.mark.parametrize("opt", NATIVE_OPTS)
@pytest.mark.parametrize("script, inputs", MISSING_END_ROWS)
def test_substring_missing_end_returns_string_na(script, inputs, opt):
    # The zero-begin row isolates the wrong value without signed overflow.
    # Check a missing end before subtracting begin. A stop or undefined
    # conversion cannot satisfy the value assertion.
    script += 'missing = na(t)\ncompleted = true\nplot(close)\n'
    # visit_call.py lowers Pine na(string) as .empty(); the engine has no
    # is_na(std::string) overload. Compare the actual result with its canonical
    # na<std::string>() too, without hard-coding empty bytes or a numeric NA.
    observe = (
        'std::cout << strategy.completed << " " << strategy.missing << " " '
        '<< (strategy.t == na<std::string>()) << "\\n";'
    )
    assert _run(script, observe, opt=opt, inputs=inputs) == "1 1 1\n"


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
    pytest.param('t = str.match("aéb", "a.b")\nplot(close)\n', True,
                 id="str-match-alone"),
    pytest.param('n = str.contains("abc", "b")\nplot(close)\n', True,
                 id="str-contains-alone"),
    pytest.param('n = str.startswith("abc", "a")\nplot(close)\n', True,
                 id="str-startswith-alone"),
    pytest.param('n = str.endswith("abc", "c")\nplot(close)\n', True,
                 id="str-endswith-alone"),
    pytest.param('s = "a" + "b"\nplot(close)\n', True, id="string-plus-alone"),
    pytest.param('s = "a"\ns += "b"\nplot(close)\n', True, id="string-append-alone"),
    pytest.param("n = 1\nn += 2\nplot(close)\n", False,
                 id="control-numeric-append"),
    pytest.param(
        'parts = array.from("a", "b")\n'
        'joined = array.join(parts, "")\nplot(close)\n',
        True, id="string-array-namespace-join-alone",
    ),
    pytest.param(
        'parts = array.from("a", "b")\njoined = parts.join()\nplot(close)\n',
        True, id="string-array-method-join-default-separator",
    ),
    pytest.param(
        'joined = array.from("a", "b").join("")\nplot(close)\n',
        True, id="string-array-temporary-join-alone",
    ),
    pytest.param(
        'collect_join(array<string> parts, array<string> results) =>\n'
        '    array.push(results, parts.join(""))\n'
        '    array.size(parts)\n'
        'results = array.new_string(0)\n'
        'counted = collect_join(array.from("a", "b"), results)\nplot(close)\n',
        True, id="string-array-forwarded-join-alone",
    ),
    pytest.param('t = str.upper("abc")\nplot(close)\n', False,
                 id="control-another-string-function"),
    pytest.param("plot(close)\n", False, id="control-no-string-function"),
    # A plot argument is not emitted, so the call emits nothing and needs no helper.
    pytest.param('plot(str.length("abc"))\n', False,
                 id="control-a-call-that-is-only-a-plot-argument"),
    pytest.param('plot(str.match("aéb", "a.b"))\n', False,
                 id="control-a-match-that-is-only-a-plot-argument"),
]


@pytest.mark.parametrize("script, emitted", EMISSION_ROWS)
def test_the_helpers_are_emitted_only_for_a_call_that_uses_them(script, emitted):
    cpp = transpile(HEADER + script)
    assert cpp.count(HELPER_DEFINITION) == int(emitted)


@pytest.mark.parametrize("operation", ("pos", "contains", "startswith", "endswith"))
@pytest.mark.parametrize("named", (False, True), ids=("positional", "named"))
def test_search_arguments_bind_once_in_parameter_order(operation, named):
    arguments = 'read_value("abc", calls, 1), read_value("b", calls, 2)'
    if named:
        arguments = 'str=read_value("b", calls, 2), source=read_value("abc", calls, 1)'
    cpp = transpile(
        HEADER
        + "read_value(string payload, array<int> calls, int tag) =>\n"
        + "    array.push(calls, tag)\n    payload\n"
        + "calls = array.new_int(0)\n"
        + f"observed = str.{operation}({arguments})\nplot(close)\n"
    )
    line = next(line for line in cpp.splitlines() if line.strip().startswith("observed ="))
    source = 'const std::string _pf_search_source = (read_value(std::string("abc"), calls, 1));'
    needle = 'const std::string _pf_search_needle = (read_value(std::string("b"), calls, 2));'
    searched = f"return _pf_u16_{operation}(_pf_search_source, _pf_search_needle);"
    assert line.count(source) == line.count(needle) == line.count(searched) == 1
    assert line.index(source) < line.index(needle) < line.index(searched)
    assert line.count("read_value(") == 2


@pytest.mark.parametrize("persistent, history", ((False, False), (True, False), (False, True)))
@pytest.mark.parametrize("string", (False, True), ids=("numeric", "string"))
@pytest.mark.parametrize("parameters", (False, True), ids=("literals", "parameters"))
def test_requested_compound_assignment_uses_its_storage_family(
    persistent, history, string, parameters,
):
    from tests.test_security_series_timeframe import requested_body

    kind, first, last = ("string", '"a"', '"b"') if string else ("float", "2.5", "1.25")
    signature = f"{kind} first, {kind} last" if parameters else ""
    initial, appended = ("first", "last") if parameters else (first, last)
    arguments = f"{first}, {last}" if parameters else ""
    cpp = transpile(
        HEADER + f"combine({signature}) =>\n"
        + f"    {'var ' if persistent else ''}{kind} accumulated = {initial}\n"
        + f"    accumulated += {appended}\n"
        + ("    previous = accumulated[1]\n" if history else "")
        + "    accumulated\n"
        + f'joined = request.security(syminfo.tickerid, "60", combine({arguments}))\n'
        + "plot(close)\n"
    )
    body = requested_body(cpp, 0)
    assert ("_pf_u16_concat(" in body) is string
    assert (HELPER_DEFINITION in cpp) is string
    if string:
        assert 'std::string("b")' in body
        # The chart helper is emitted too, even when only a request calls it.
        # Both bodies must keep the persistent/local string out of arithmetic.
        assert "_pf_u16_concat(std::to_string(" not in cpp
        for line in body.splitlines():
            if "_pf_u16_concat(" in line:
                assert "std::to_string(" not in line
                assert "is_na(" not in line
                assert "(double)" not in line
    else:
        assert "1.25" in body


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


RUNTIME_BEGIN_ROWS = [
    pytest.param(4, "(which is 4) > this->size() (which is 3)",
                 id="measured-ascii-begin-beyond-length-stops"),
    pytest.param('input.int(-1, "Begin")',
                 "(which is 18446744073709551615) > this->size() (which is 3)",
                 id="dynamic-ascii-begin-negative-stops"),
]


@pytest.mark.parametrize("begin, detail", RUNTIME_BEGIN_ROWS)
def test_a_begin_outside_the_text_stops_the_run(begin, detail):
    """Pin the existing native boundary guard and its diagnostic detail.

    A negative input remains a runtime value. The separate constant-argument
    test below checks the compiler refusal; a runtime stop cannot satisfy it.
    The positive out-of-range constant retains its existing runtime behavior.
    """
    cpp = transpile(HEADER + f't = str.substring("abc", {begin})\nplot(close)\n')
    stopped = run_emitted_tu(cpp, STOP_DRIVER, opt="-O2", label="substring begin outside")
    assert "basic_string::substr" in stopped
    assert detail in stopped


SUBSTRING_CALLS = [
    pytest.param('result_value = str.substring("abc", {begin})', id="positional"),
    pytest.param('result_value = str.substring("abc", {begin}, 3)', id="positional-end"),
    pytest.param('result_value = str.substring(source="abc", begin_pos={begin})',
                 id="named"),
    pytest.param('result_value = str.substring(end_pos=3, begin_pos={begin}, source="abc")',
                 id="named-reordered"),
    pytest.param('result_value = str.substring("abc", end_pos=3, begin_pos={begin})',
                 id="mixed-reordered"),
]


@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("call_template", SUBSTRING_CALLS)
@pytest.mark.parametrize("declarations, begin, first_token", [
    pytest.param("", "-1", "-", id="literal"),
    pytest.param("", "-(2 + 1)", "-", id="negated-expression"),
    pytest.param("", "1 - 4", "1", id="folded-subtraction"),
    pytest.param("", "-8 / 2", "-", id="folded-exact-division"),
    pytest.param("", "-8 % 3", "-", id="folded-remainder"),
    pytest.param("begin = -2\n", "begin", "begin", id="inferred-constant"),
    pytest.param("int start = -3\nbegin = start\n", "begin", "begin",
                 id="typed-alias"),
    pytest.param("const int origin = 2\nconst int begin = origin - 5\n",
                 "begin", "begin", id="const-alias-chain"),
])
def test_constant_negative_substring_begin_is_refused(
    declarations, begin, first_token, call_template, check_support,
):
    # TradingView CE10276 refuses the preserved constant-negative source at
    # its begin argument. These are unit cases for the general constant rule,
    # not additional TradingView measurements or a trade-value oracle.
    source = HEADER + declarations + call_template.format(begin=begin) + "\nplot(close)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="substring.pine", check_support=check_support)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == (
        "str.substring() requires 'begin_pos' to be greater than or equal to 0."
    )
    column = call_template.index("{begin}") + 1
    assert diagnostic.location == SourceLocation(
        "substring.pine", 3 + declarations.count("\n"),
        column, column + len(first_token),
    )
    # Allocation belongs to the catalog generator. Require registration without
    # assigning an unreserved code in this test or in the diagnostic emitter.
    assert diagnostic.code not in UNCATALOGUED.values()
    assert diagnostic.args == {}


@pytest.mark.parametrize("body", [
    pytest.param('unused() => str.substring("abc", -1)\nplot(close)\n',
                 id="unused-body"),
    pytest.param('plot(close, title=str.substring("abc", -1))\n',
                 id="visual-keyword"),
])
def test_constant_negative_substring_is_checked_before_ignored_calls_are_lowered(body):
    source = HEADER + body
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="substring.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == (
        "str.substring() requires 'begin_pos' to be greater than or equal to 0."
    )
    column = body.splitlines()[0].index("-1") + 1
    assert diagnostic.location == SourceLocation("substring.pine", 3, column, column + 1)


def test_direct_analyzer_checks_constant_negative_substring_arguments():
    source = HEADER + 'result_value = str.substring("abc", -1)\n'
    program = Parser(
        Lexer(source, filename="substring.pine").tokenize(),
        source=source, filename="substring.pine",
    ).parse()
    with pytest.raises(CompileError) as caught:
        Analyzer(program, filename="substring.pine").analyze()
    diagnostic, = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == (
        "str.substring() requires 'begin_pos' to be greater than or equal to 0."
    )
    column = source.splitlines()[2].index("-1") + 1
    assert diagnostic.location == SourceLocation("substring.pine", 3, column, column + 1)


@pytest.mark.parametrize("declarations, begin", [
    pytest.param('begin = input.int(-1, "Begin")\n', "begin", id="input"),
    pytest.param("begin = bar_index - 1\n", "begin", id="series"),
    pytest.param("begin = -1\nbegin := 0\n", "begin", id="reassigned"),
    pytest.param("var int begin = -1\n", "begin", id="persistent"),
    pytest.param("int begin = na\n", "begin", id="missing-alias"),
    pytest.param("", "int(na) + 1", id="missing-expression"),
    pytest.param("", "0", id="zero"),
    pytest.param("", "2", id="positive"),
])
def test_substring_begin_without_negative_constant_proof_keeps_its_lowering(
    declarations, begin,
):
    # Acceptance and emitted runtime checking only; native value/stop assertions
    # remain in the existing missing, edge and runtime-boundary tests.
    cpp = transpile(
        HEADER + declarations
        + f'result_value = str.substring(end_pos=3, source="abc", begin_pos={begin})\n'
        + "plot(close)\n"
    )
    assert '_PF_STRING_STOP("substring_out_of_range"' in cpp


def test_substring_constant_proof_respects_parameter_bindings_and_authored_overloads():
    source = HEADER + (
        "const int begin = -2\n"
        'cut(int begin) => str.substring("abc", begin)\n'
        "substring(int begin) => begin + 3\n"
        "method substring(int self, int begin) => self + begin\n"
        "base = 4\n"
        "result_value = cut(1)\n"
        "n = substring(-2) + base.substring(-3)\n"
        "plot(n)\n"
    )
    cpp = transpile(source)
    assert '_PF_STRING_STOP("substring_out_of_range"' in cpp


def test_substring_negative_end_retains_runtime_validation():
    # This change has no compiler evidence for an end-domain refusal.
    cpp = transpile(HEADER + 'result_value = str.substring("abc", 0, -1)\nplot(close)\n')
    assert "str.substring: end_pos is less than begin_pos" in cpp


@pytest.mark.parametrize("opt", NATIVE_OPTS)
def test_substring_finite_end_before_start_stops_the_run(opt):
    # Issue #382 item 9: a finite end before begin stops the run.
    # Retain actual native values and errors in stdout for diagnosis.
    script = (
        'j = input.int(1, "End")\n'
        'completed = false\n'
        't = str.substring("abc", 2, j)\n'
        'completed := true\n'
        'plot(close)\n'
    )
    cpp = transpile(HEADER + script)
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    strategy.set_input("End", "1");
    Bar bars[3]{
        {1.0, 1.0, 1.0, 1.0, 1.0, 1704067200000LL},
        {1.0, 1.0, 1.0, 1.0, 1.0, 1704067260000LL},
        {1.0, 1.0, 1.0, 1.0, 1.0, 1704067320000LL},
    };
    strategy.run(bars, 3, "1", "1");
    std::cout << "j=" << strategy.j << "\n"
              << "completed=" << strategy.completed << "\n"
              << "value=" << strategy.t << "\n"
              << "error=" << strategy.last_error() << "\n";
    return 0;
}
'''
    # Compile/link failures and unexpected process exits propagate unchanged.
    # Only the native run's own last_error() can satisfy the stop assertion.
    observed = run_emitted_tu(
        cpp, driver, opt=opt, label="substring finite end before start",
    )
    print(f"native stdout at {opt}:\n{observed}", end="")
    position, completed, value, error = observed.split("\n", 3)
    assert position == "j=1", observed
    assert value.startswith("value="), observed
    assert error.startswith("error="), observed
    assert "end_pos is less than begin_pos" in error, observed
    assert completed == "completed=0", observed


def _assert_trim_cases(cases: list[tuple[str, str]], input_shape: str) -> None:
    """Observe both the actual Pine source value and its trim result as bytes."""
    script = []
    setup = []
    observe = []
    expected = []
    for index, (text, trimmed) in enumerate(cases):
        source = f"trim_source_{index}"
        result = f"trim_result_{index}"
        if input_shape == "runtime-input":
            title = f"Trim text {index}"
            # A distinct default makes an ignored override observable. Counted
            # UTF-8 bytes keep NUL intact without exercising the known input-
            # default truncation defect from item 6.
            script.append(f'{source} = input.string("unused default", "{title}")\n')
            encoded = text.encode("utf-8")
            octets = "".join(f"\\{byte:03o}" for byte in encoded)
            setup.append(
                f'strategy.set_input("{title}", '
                f'std::string("{octets}", {len(encoded)}));'
            )
        else:
            assert input_shape == "literal"
            # Pine supports \n and \t; \r spells the letter r. Keep a raw CR
            # inside the quotes, where the lexer preserves it as string data.
            literal = (text.replace("\\", "\\\\").replace('"', '\\"')
                       .replace("\n", "\\n").replace("\t", "\\t"))
            script.append(f'{source} = "{literal}"\n')
        script.append(f"{result} = str.trim({source})\n")
        observe.append(f"hex(strategy.{source}); hex(strategy.{result});")
        expected.extend((text.encode("utf-8").hex() + "\n",
                         trimmed.encode("utf-8").hex() + "\n"))
    script.append("plot(close)\n")
    # hex() iterates std::string's bytes, including NUL. Only ASCII reaches
    # stdout, so invalid UTF-8 cannot be hidden by decoding or replacement.
    # Compare every line, including empty ones and the final newline.
    assert _run("".join(script), "\n".join(observe),
                setup="\n".join(setup)) == "".join(expected)


TRIM_MEASURED_ROWS = [
    pytest.param("\u00a0a\u00a0", "a", id="measured-nbsp-both-ends"),
    pytest.param("\u2003a\u2003", "a", id="measured-em-space-both-ends"),
    pytest.param("\ufeffa", "a", id="measured-feff-left-end"),
    pytest.param("\va\f", "a", id="measured-vertical-tab-and-form-feed"),
]


@pytest.mark.parametrize("text, expected", TRIM_MEASURED_ROWS)
def test_trim_measured_end_whitespace(text, expected):
    _assert_trim_cases([(text, expected)], "literal")


# The issue's explicit classification, not Python's isspace()/strip() or a
# guessed Unicode category. The test shapes below are contract compositions.
TRIM_CHARACTER_ROWS = [
    pytest.param("\u00a0", True, id="removes-u00a0-nbsp"),
    pytest.param("\u2003", True, id="removes-u2003-em-space"),
    pytest.param("\u3000", True, id="removes-u3000-ideographic-space"),
    pytest.param("\u1680", True, id="removes-u1680-ogham-space-mark"),
    pytest.param("\u2028", True, id="removes-u2028-line-separator"),
    pytest.param("\ufeff", True, id="removes-ufeff-zero-width-no-break-space"),
    pytest.param("\v", True, id="removes-u000b-vertical-tab"),
    pytest.param("\f", True, id="removes-u000c-form-feed"),
    pytest.param(" ", True, id="removes-u0020-space"),
    pytest.param("\t", True, id="removes-u0009-tab"),
    pytest.param("\n", True, id="removes-u000a-line-feed"),
    pytest.param("\r", True, id="removes-u000d-carriage-return"),
    pytest.param("\u0085", False, id="keeps-u0085-next-line"),
    pytest.param("\u001f", False, id="keeps-u001f-unit-separator"),
    pytest.param("\u200b", False, id="keeps-u200b-zero-width-space"),
    pytest.param("\u180e", False, id="keeps-u180e-mongolian-vowel-separator"),
]

TRIM_INPUT_SHAPES = [
    pytest.param("literal", id="contract-derived-literal"),
    pytest.param("runtime-input", id="contract-derived-runtime-input"),
]


@pytest.mark.parametrize("input_shape", TRIM_INPUT_SHAPES)
@pytest.mark.parametrize("character, remove_at_ends", TRIM_CHARACTER_ROWS)
def test_trim_contract_characters_at_ends_and_inside(character, remove_at_ends, input_shape):
    cases = []
    # Two different interiors, one with a NUL followed by an emoji and more
    # text: neither a constant "a" nor a C-string prefix can satisfy all rows.
    for left, right in (("left", "right"), (f"{CJK}\0", f"{EMOJI}tail")):
        body = left + right
        interior = left + character + right
        for prefix, suffix in ((character * 2, ""), ("", character * 3),
                               (character * 2, character * 3)):
            text = prefix + body + suffix
            cases.append((text, body if remove_at_ends else text))
        cases.append((interior, interior))
        text = character + interior + character
        cases.append((text, interior if remove_at_ends else text))
        text = " \t" + character + body + character + "\n\r"
        cases.append((text, body if remove_at_ends else character + body + character))
    blank_only = character * 3
    cases.append((blank_only, "" if remove_at_ends else blank_only))
    _assert_trim_cases(cases, input_shape)


@pytest.mark.parametrize("input_shape", TRIM_INPUT_SHAPES)
def test_trim_contract_mixed_boundaries_and_empty(input_shape):
    removed = "\u00a0\u2003\u3000\u1680\u2028\ufeff\v\f \t\n\r"
    kept = "\u0085\u001f\u200b\u180e"
    body = f"head{removed}tail{CJK}\0{EMOJI}"
    cases = [
        ("", ""),
        ("plain words", "plain words"),
        (removed, ""),
        (removed + body + removed, body),
        (removed + kept + removed, kept),
    ]
    for boundary in kept:
        # A kept character stops trimming: removable characters behind it are
        # interior text and must survive along with every byte after the NUL.
        interior = boundary + removed + body + removed + boundary
        cases.append((removed + interior + removed, interior))
    _assert_trim_cases(cases, input_shape)

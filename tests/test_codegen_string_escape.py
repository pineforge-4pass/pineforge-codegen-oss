"""Regression: C++ string-literal escaping.

Pine strings may contain characters that are special inside a C++ string
literal — double quotes (common in JSON alert/webhook templates), backslashes,
and newlines. These must be escaped when emitted, or the generated C++ fails to
compile ("invalid suffix on literal" / unterminated string).
"""

from pineforge_codegen import transpile


def _cpp(body: str) -> str:
    return transpile('//@version=6\nstrategy("t")\n' + body + "\n")


def test_string_with_embedded_double_quotes_is_escaped():
    cpp = _cpp("msg = '{\"type\":\"bot\",\"id\":\"42\"}'\nplot(str.length(msg))")
    # The raw inner quotes must be backslash-escaped in the C++ literal.
    assert r'\"type\"' in cpp
    assert 'std::string("{"type"' not in cpp  # the broken (unescaped) form


def test_string_with_backslash_is_escaped():
    cpp = _cpp("p = 'a\\\\b'\nplot(str.length(p))")
    assert "\\\\" in cpp


def test_input_title_with_quotes_and_backslashes_is_escaped():
    # Every getter reading the input -- the member, the TA reset and the two
    # precalculate() constructions of the EMA, a source input and its
    # precalculate() replay, an inline call -- keys it by a C++ literal of the
    # title (the E2E compiles, runs and overrides these).
    cpp = _cpp('len = input.int(9, "He said \\"fast\\" \\\\ C:\\\\bars")\n'
               "src = input.source(close, 'px \"q\"')\n"
               'x = ta.ema(src, len) + input.float(0.0, "off \\"pts\\"")\n'
               "plot(x)")
    assert cpp.count(r'get_input_int("He said \"fast\" \\ C:\\bars", 9)') == 4
    assert r'get_input_source("px \"q\"", _src_close_)' in cpp
    assert r'get_input_double("off \"pts\"", 0.0)' in cpp
    assert '"He said "fast"' not in cpp  # the broken (unescaped) form


def test_string_constant_inlined_at_its_use_is_escaped():
    cpp = _cpp('Q = "say \\"hi\\""\n'
               'if str.length(Q) > 3\n    strategy.entry("L", strategy.long)')
    assert 'std::string("say "hi"")' not in cpp
    assert cpp.count(r'std::string("say \"hi\"")') >= 2  # the member and its use

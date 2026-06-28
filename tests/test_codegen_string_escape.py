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

"""Issue #230: code line endings must not change output or source locations."""

import pytest

from pineforge_codegen import (
    transpile, transpile_full, transpile_with_request_inventory,
)
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.lexer import Lexer, TokenType


REPORTER = "//@version=6\nstrategy('Newlines')\nplot(close)\n"
REALISTIC = '''//@version=6
strategy("Newline strategy", overlay=true)
threshold = input.float(0.0, "Threshold")
above_midpoint(float price) =>
    midpoint = (high + low) / 2
    price > midpoint
// John's "threshold" comment must end before the next statement.
change = close -
      open
if above_midpoint(close) and change > threshold
    strategy.entry("Long", strategy.long)
plot(change, title="Bar change")
// @pf-trace change=change
'''
TRANSPILERS = [transpile, transpile_full, transpile_with_request_inventory]
LINE_ENDINGS = [pytest.param("\r\n", id="crlf"), pytest.param("\r", id="cr"),
                pytest.param("mixed", id="mixed")]


def _with_line_endings(source, line_ending):
    if line_ending != "mixed":
        return source.replace("\n", line_ending)
    endings = ("\n", "\r\n", "\r")
    lines = source.split("\n")
    return "".join(line + endings[index % len(endings)]
                   for index, line in enumerate(lines[:-1])) + lines[-1]


def _string_source(quote):
    triple = quote * 3
    return (REPORTER +
            f'multiline = input.string({triple}abc\n    def\n{triple}, "Multiline")\n'
            f'wrapped = input.string({quote}abc\n    def{quote}, "Wrapped")\n'
            "plot(str.length(multiline) + str.length(wrapped))\n")


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("line_ending", LINE_ENDINGS)
@pytest.mark.parametrize("source", [pytest.param(REPORTER, id="reporter"),
                                   pytest.param(REALISTIC, id="realistic")])
def test_line_endings_emit_identical_cpp(translate, line_ending, source):
    expected = translate(source)
    actual = translate(_with_line_endings(source, line_ending))
    # Full/inventory results also compare metadata and diagnostics; their cpp
    # strings, like transpile's return value, must match byte for byte.
    assert actual == expected


@pytest.mark.parametrize("line_ending", LINE_ENDINGS)
def test_lexer_tokens_and_locations_match_lf(line_ending):
    assert Lexer(_with_line_endings(REALISTIC, line_ending)).tokenize() == (
        Lexer(REALISTIC).tokenize()
    )


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("quote", ["'", '"'])
def test_string_crlf_emit_identical_cpp(translate, quote):
    source = _string_source(quote)
    expected = translate(source)
    assert translate(_with_line_endings(source, "\r\n")) == expected


@pytest.mark.parametrize("quote", ["'", '"'])
def test_string_crlf_tokens_and_locations_match_lf(quote):
    source = _string_source(quote)
    expected = Lexer(source).tokenize()
    values = [token.value for token in expected if token.type == TokenType.STRING]
    assert "abc def" in values
    assert "abc\n    def\n" in values
    assert Lexer(_with_line_endings(source, "\r\n")).tokenize() == expected


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("quote", ["'", '"', "'''", '"""'])
def test_escaped_string_crlf_matches_lf(translate, quote):
    source = (REPORTER +
              f'text = input.string({quote}abc\\\n    def{quote}, "Text")\n'
              "plot(str.length(text))\n")
    expected = Lexer(source).tokenize()
    assert any(token.type == TokenType.STRING and token.value == "abc\n    def"
               for token in expected)
    actual_source = _with_line_endings(source, "\r\n")
    assert Lexer(actual_source).tokenize() == expected
    expected_output = translate(source)
    assert translate(actual_source) == expected_output


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("line_ending", LINE_ENDINGS)
def test_line_three_syntax_error_has_identical_diagnostics(translate, line_ending):
    source = "//@version=6\nstrategy('Newlines')\nx = (1 + )\n"
    with pytest.raises(CompileError) as lf_error:
        translate(source, filename="newlines.pine")
    with pytest.raises(CompileError) as other_error:
        translate(_with_line_endings(source, line_ending), filename="newlines.pine")
    (diagnostic,) = lf_error.value.diagnostics
    assert diagnostic.phase == Phase.PARSER
    assert (diagnostic.location.line, diagnostic.location.col) == (3, 10)
    assert other_error.value.diagnostics == lf_error.value.diagnostics
    assert str(other_error.value) == str(lf_error.value)
    assert other_error.value.format(_with_line_endings(source, line_ending)) == (
        lf_error.value.format(source)
    )


@pytest.mark.parametrize("translate", [Lexer, *TRANSPILERS])
@pytest.mark.parametrize("quote", ["'", '"'])
def test_unterminated_string_crlf_has_identical_diagnostics(translate, quote):
    # CRLF used to make a whitespace-only line look like a wrapped line,
    # swallowing the invalid character. Also cover unindented and EOF recovery.
    source = (REPORTER + f"first = {quote}abc\n"
              "    \n"
              "    @\n"
              "x = close\n"
              f"second = {quote}def\n"
              "x = close\n"
              f"third = {quote}ghi\n")
    with pytest.raises(CompileError) as lf_error:
        result = translate(source, filename="newlines.pine")
        if translate is Lexer:
            result.tokenize()
    with pytest.raises(CompileError) as other_error:
        result = translate(_with_line_endings(source, "\r\n"),
                           filename="newlines.pine")
        if translate is Lexer:
            result.tokenize()
    assert all(diagnostic.phase == Phase.LEXER
               for diagnostic in lf_error.value.diagnostics)
    assert other_error.value.diagnostics == lf_error.value.diagnostics
    assert str(other_error.value) == str(lf_error.value)


@pytest.mark.parametrize("line_ending", LINE_ENDINGS)
@pytest.mark.parametrize("quote", ["'", '"', "'''", '"""'])
@pytest.mark.parametrize("content, value", [("raw\rvalue", "raw\rvalue"),
                                           (r"raw\rvalue", "rawrvalue")])
def test_carriage_return_string_contents_are_preserved(line_ending, quote, content, value):
    # A raw CR inside quotes is currently data; the two characters backslash-r
    # are an unsupported Pine escape that drops the backslash. Preserve both.
    source = (REPORTER + f'text = input.string({quote}{content}{quote}, "Text")\n'
              'plot(str.length(text))\n')
    expected = transpile_full(source)
    assert expected["inputs"][0]["default"] == value
    actual_source = _with_line_endings(source, line_ending)
    actual = transpile_full(actual_source)
    assert actual == expected
    assert any(token.type == TokenType.STRING and token.value == value
               for token in Lexer(actual_source).tokenize())


def test_source_size_is_checked_before_line_endings_shrink(monkeypatch):
    from pineforge_codegen import limits

    source = REPORTER.replace("\n", "\r\n")
    monkeypatch.setattr(limits, "MAX_SOURCE_CHARS", len(source) - 1)
    with pytest.raises(CompileError, match="Source size exceeds"):
        transpile(source)
    with pytest.raises(CompileError, match="Source size exceeds"):
        Lexer(source).tokenize()
    monkeypatch.setattr(limits, "MAX_SOURCE_CHARS", len(source))
    assert transpile(source) == transpile(REPORTER)


def test_pragma_extraction_keeps_source_size_recovery(monkeypatch):
    from pineforge_codegen import limits
    from pineforge_codegen.pragmas import extract_pf_trace_pragmas

    source = REPORTER + "// @pf-trace price=close\n"
    expected = extract_pf_trace_pragmas(source)
    monkeypatch.setattr(limits, "MAX_SOURCE_CHARS", len(source) - 1)
    assert extract_pf_trace_pragmas(source) == expected


@pytest.mark.parametrize("translate", TRANSPILERS)
def test_double_crlf_after_code_matches_lf(translate):
    source = "//@version=6\nstrategy('Newlines')\r\r\nplot(bar_index)\n"
    lf_source = source.replace("\r\r\n", "\n\n")
    expected_tokens = Lexer(lf_source).tokenize()
    assert Lexer(source).tokenize() == expected_tokens
    expected_output = translate(lf_source)
    assert translate(source) == expected_output


@pytest.mark.parametrize("translate", TRANSPILERS)
def test_double_crlf_after_comment_matches_lf(translate):
    source = ("//@version=6\n"
              '// John\'s "Newlines" comment\r\r\n'
              "strategy('Newlines')\nplot(bar_index)\n")
    lf_source = source.replace("\r\r\n", "\n\n")
    expected_tokens = Lexer(lf_source).tokenize()
    assert Lexer(source).tokenize() == expected_tokens
    expected_output = translate(lf_source)
    assert translate(source) == expected_output


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("first_line", [
    pytest.param("strategy('Newlines')", id="code"),
    pytest.param('// John\'s "Newlines" comment', id="comment"),
])
def test_double_crlf_line_three_diagnostics_match_lf(translate, first_line):
    source = "//@version=6\n" + first_line + "\r\r\nx = (1 + )\n"
    lf_source = source.replace("\r\r\n", "\n\n")
    with pytest.raises(CompileError) as lf_error:
        translate(lf_source, filename="newlines.pine")
    with pytest.raises(CompileError) as other_error:
        translate(source, filename="newlines.pine")
    assert other_error.value.diagnostics == lf_error.value.diagnostics
    assert str(other_error.value) == str(lf_error.value)
    assert other_error.value.format(source) == lf_error.value.format(lf_source)


@pytest.mark.parametrize("translate", TRANSPILERS)
@pytest.mark.parametrize("quote", ["'", '"', "'''", '"""'])
def test_double_crlf_string_contents_match_lf_pieces(translate, quote):
    indent = "    " if len(quote) == 1 else ""
    pieces = ("x\r", "\r\n", indent + "y")
    lf_pieces = tuple(piece.replace("\r\n", "\n") for piece in pieces)
    content = "".join(pieces)
    lf_content = "".join(lf_pieces)
    # Include a code-line boundary too: its blank line must survive before
    # either kind of string, without changing the lone CR held as string data.
    prefix = REPORTER.replace("plot(close)\n", "plot(close)\r\r\n")
    lf_prefix = prefix.replace("\r\r\n", "\n\n")
    source = (prefix +
              f'text = input.string({quote}{content}{quote}, "Text")\n'
              "plot(str.length(text))\n")
    lf_source = (lf_prefix +
                 f'text = input.string({quote}{lf_content}{quote}, "Text")\n'
                 "plot(str.length(text))\n")
    lexer = Lexer(source)
    assert lexer.source == lf_source
    # Read the LF pieces separately so the preserved data CR cannot combine
    # with the folded LF into a fresh CRLF on a second normalization pass.
    expected_value = "".join(
        token.value
        for piece in (lf_pieces[0], "".join(lf_pieces[1:]))
        for token in Lexer(f"{quote}{piece}{quote}").tokenize()
        if token.type == TokenType.STRING
    )
    assert any(token.type == TokenType.STRING and token.value == expected_value
               for token in lexer.tokenize())
    # Spell value LFs as Pine escapes in the equivalent LF reference, keeping
    # its later statement on the same line with an extra physical blank line.
    reference_content = expected_value.replace("\n", r"\n")
    reference_source = (lf_prefix +
                        f'text = input.string({quote}{reference_content}{quote}, "Text")\n'
                        "\nplot(str.length(text))\n")
    expected_output = translate(reference_source)
    assert translate(source) == expected_output

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

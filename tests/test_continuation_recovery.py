"""Continuation requires an operator at the actual end of the source line."""

import pytest

from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer, TokenType


@pytest.mark.parametrize(
    "header",
    [
        "f(x) =>",  # Unchanged pin: the arrow opens a four-space callable body.
        "method f(float self) =>",  # Unchanged pin: a method arrow also opens a block.
        "if true",  # Unchanged pin: the completed condition opens a block.
        "else",  # Unchanged pin: else is structural, with no pending operand.
        "for i = 0 to 1",  # Unchanged pin: the loop bound completes the header.
        "for x in values",  # Unchanged pin: the collection completes the header.
        "while false",  # Unchanged pin: the completed condition opens a block.
        "switch",  # Unchanged pin: selector-free switch opens its case block.
        "switch x",  # Unchanged pin: the selector completes the switch header.
        "type Cell",  # Unchanged pin: the type name completes the header.
        "enum Size",  # Unchanged pin: the enum name completes the header.
    ],
)
def test_structural_header_keeps_its_block_boundary(header: str) -> None:
    tokens = Lexer(header + "\n    x\n").tokenize()
    # A completed header emits NEWLINE, then INDENT before the body's x.
    body_index = next(i for i, token in enumerate(tokens)
                      if token.type == TokenType.IDENT and token.line == 2)
    assert [token.type for token in tokens[body_index - 2:body_index]] == [
        TokenType.NEWLINE, TokenType.INDENT,
    ]


def test_non_ascii_callable_body_keeps_only_character_diagnostics() -> None:
    source = ("//@version=6\n"
              "\u03b1(float \u03b2) =>\n"
              "    var float \u03b3 = \u03b2\n"
              "    \u03b3\n")
    with pytest.raises(CompileError) as caught:
        Lexer(source).tokenize()
    # Five rejected identifier characters; the consumed beta after '=' is no operator.
    assert [(d.code, d.message, d.location.line, d.location.col, d.location.end_col)
            for d in caught.value.diagnostics] == [
        ("PF-E0044", "no viable alternative at character '\u03b1'", 2, 1, 2),
        ("PF-E0044", "no viable alternative at character '\u03b2'", 2, 9, 10),
        ("PF-E0044", "no viable alternative at character '\u03b3'", 3, 15, 16),
        ("PF-E0044", "no viable alternative at character '\u03b2'", 3, 19, 20),
        ("PF-E0044", "no viable alternative at character '\u03b3'", 4, 5, 6),
    ]


@pytest.mark.parametrize(
    "prefix, line, col",
    [
        ("value = ", 2, 9),  # '=' ends at 2:8; the invalid lexeme follows it at 2:9.
        ("value = (\n    1) + ", 3, 10),  # The parenthesis-closing scan's '+' ends at 3:9.
        ("value = 1 +\n ", 3, 2),  # An error-only wrapped line must not reuse 2:12's '+'.
    ],
)
@pytest.mark.parametrize(
    "suffix, message",
    [
        ("\u03b1", "no viable alternative at character '\u03b1'"),  # Identifier recovery emits no token.
        ("\u0662", "no viable alternative at character '\u0662'"),  # Non-ASCII digit recovery emits no token.
        ("$", "Unexpected character: '$'"),  # Unknown-character recovery emits no token.
        ("#", "Invalid color literal: expected hex digits after '#'"),  # An empty color emits no token.
    ],
)
def test_consumed_error_is_not_a_trailing_operator(
    prefix: str, line: int, col: int, suffix: str, message: str,
) -> None:
    with pytest.raises(CompileError) as caught:
        Lexer("//@version=6\n" + prefix + suffix + "\n    next = 1\n").tokenize()
    # The sole invalid character is consumed at (line, col), after the earlier operator.
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.message == message
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.level == Level.ERROR
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + 1)


def test_error_before_a_real_trailing_operator_preserves_the_refusal() -> None:
    with pytest.raises(CompileError) as caught:
        Lexer("//@version=6\nvalue = \u03b1 +\n    2\n").tokenize()
    # Unchanged pin: alpha is at 2:9, but '+' really ends this line at exclusive 2:12.
    assert [(d.code, d.location.line, d.location.col, d.location.end_col)
            for d in caught.value.diagnostics] == [
        ("PF-E0044", 2, 9, 10),
        ("PF-E0038", 2, 12, 13),
    ]

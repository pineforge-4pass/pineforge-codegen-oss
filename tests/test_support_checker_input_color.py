"""Phase C: input.color defval must be a color constant / color.new(...) / color.rgb(...).

The engine has no get_input_color helper; codegen routes input.color through
get_input_int with the defval emitted as a packed RGBA int. That is only safe
when the defval is itself a color constant or builder (color.red, color.new,
color.rgb). Any other expression (numeric literal, arbitrary variable,
ternary, etc.) would silently produce a wrong-colored UI or, worse, an
ambiguous int that the runtime would parse as a color index.
"""
import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


def _check(src: str) -> None:
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    SupportChecker(ast).check_or_raise()


def test_input_color_with_const_allowed():
    """`color.red` is a constant — accepted."""
    src = '''//@version=6
strategy("t")
c = input.color(color.red, "Bullish")
plot(close, color=c)
'''
    _check(src)  # should NOT raise


def test_input_color_with_color_new_allowed():
    """`color.new(color.red, 50)` is a builder — accepted."""
    src = '''//@version=6
strategy("t")
c = input.color(color.new(color.red, 50), "Bullish")
plot(close, color=c)
'''
    _check(src)


def test_input_color_with_color_rgb_allowed():
    """`color.rgb(255, 0, 0)` is a builder — accepted."""
    src = '''//@version=6
strategy("t")
c = input.color(color.rgb(255, 0, 0), "Bullish")
plot(close, color=c)
'''
    _check(src)


def test_input_color_with_int_literal_rejected():
    """A raw int literal is NOT a color expression — codegen would silently
    produce a packed-int that doesn't represent the user's intent. Reject."""
    src = '''//@version=6
strategy("t")
c = input.color(123456, "Bullish")
plot(close, color=c)
'''
    with pytest.raises(CompileError, match=r"input\.color"):
        _check(src)


def test_input_color_with_arbitrary_identifier_rejected():
    """An arbitrary variable isn't guaranteed to be a packed color; reject."""
    src = '''//@version=6
strategy("t")
mystery = 42
c = input.color(mystery, "Bullish")
plot(close, color=c)
'''
    with pytest.raises(CompileError, match=r"input\.color"):
        _check(src)

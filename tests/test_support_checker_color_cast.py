"""Phase B: bare color() cast must be rejected; codegen would emit invalid C++."""
import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


def test_bare_color_cast_warns_not_rejected():
    # Bare color(...) cast is cosmetic (no backtest-logic effect): warned
    # (no-op), not rejected; codegen emits a default color.
    src = '''//@version=6
strategy("t")
x = color(close)
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    SupportChecker(ast).check_or_raise()  # must not raise
    diags = SupportChecker(ast).check()
    assert any("color" in d.message for d in diags)

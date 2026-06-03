"""Phase B: chart.left/right_visible_bar_time must be rejected (no batch-mode meaning)."""
import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


@pytest.mark.parametrize("member", ["left_visible_bar_time", "right_visible_bar_time"])
def test_chart_visible_bar_time_rejected(member):
    src = f'''//@version=6
strategy("t")
t = chart.{member}
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    with pytest.raises(CompileError, match=member):
        SupportChecker(ast).check_or_raise()

"""Phase B: timeframe.from_seconds must be rejected, not silently emit 'false'."""
import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


def _check(src: str) -> None:
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    SupportChecker(ast).check_or_raise()


def test_timeframe_from_seconds_rejected():
    src = '''//@version=6
strategy("t")
tf = timeframe.from_seconds(900)
'''
    with pytest.raises(CompileError, match="timeframe.from_seconds"):
        _check(src)

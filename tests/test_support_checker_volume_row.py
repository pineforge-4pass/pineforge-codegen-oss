"""Phase B: volume_row.* namespace must be rejected."""
import pytest
from tunaxis_codegen.lexer import Lexer
from tunaxis_codegen.parser import Parser
from tunaxis_codegen.support_checker import SupportChecker
from tunaxis_codegen.errors import CompileError


@pytest.mark.parametrize("call", [
    "volume_row.new(0.0, 1.0)",
    "volume_row.get_volume(vr)",
])
def test_volume_row_calls_rejected(call):
    src = f'''//@version=6
strategy("t")
vr = {call}
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    with pytest.raises(CompileError, match="volume_row"):
        SupportChecker(ast).check_or_raise()

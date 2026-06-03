"""Phase B: dividends.* / earnings.* / splits.* must be rejected (no fundamental data source)."""
import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


@pytest.mark.parametrize("ref", [
    "dividends.gross",
    "dividends.net",
    "dividends.future_amount",
    "earnings.future_eps",
    "earnings.eps",
    "earnings.revenue",
    "splits.numerator",
    "splits.denominator",
])
def test_fundamental_data_refs_rejected(ref):
    src = f'''//@version=6
strategy("t")
x = {ref}
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    ns = ref.split(".")[0]
    with pytest.raises(CompileError, match=ns):
        SupportChecker(ast).check_or_raise()

"""Phase B: footprint.* namespace must be rejected (no codegen support)."""
import pytest
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import CompileError


def _check(src: str) -> None:
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    SupportChecker(ast).check_or_raise()


@pytest.mark.parametrize("call", [
    "footprint.new(10)",
    "footprint.get_total_volume(fp)",
    "footprint.get_imbalance_ratio(fp, 0)",
])
def test_footprint_calls_rejected(call):
    src = f'''//@version=6
strategy("t")
fp = {call}
'''
    with pytest.raises(CompileError, match="footprint"):
        _check(src)

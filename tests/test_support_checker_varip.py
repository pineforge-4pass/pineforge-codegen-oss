"""Phase C: varip declarations must be rejected, not warned.

PineForge runs a bar-close batch engine — there are NO intrabar ticks.
Pine's ``varip`` keyword is specified to mutate per tick within a bar; the
PineForge codegen silently demotes it to ``var`` (bar-close-only update),
producing wrong state accumulation when scripts rely on tick-level updates.

Previously the support checker emitted a warning and let the codegen
proceed. That made the silent-wrong-result bug invisible. This module
pins the new contract: any ``varip`` declaration raises CompileError.
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


def test_varip_int_rejected():
    src = '''//@version=6
strategy("t")
varip int tick_counter = 0
tick_counter := tick_counter + 1
'''
    with pytest.raises(CompileError, match=r"varip"):
        _check(src)


def test_varip_float_rejected():
    src = '''//@version=6
strategy("t")
varip float acc = 0.0
acc := acc + close
'''
    with pytest.raises(CompileError, match=r"varip"):
        _check(src)


def test_var_int_still_allowed():
    """Sanity: ``var`` (the bar-close-only variant) must still compile."""
    src = '''//@version=6
strategy("t")
var int counter = 0
counter := counter + 1
'''
    _check(src)  # should NOT raise

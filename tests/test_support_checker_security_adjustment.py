"""Phase C: request.security data-adjustment kwargs must reject non-default values.

The kwargs ``backadjustment``, ``settlement_as_close`` and ``adjustment`` are
accepted by the support checker for syntactic compatibility with Pine
scripts targeting futures / continuous-contract data — but the underlying
engine uses a fixed unadjusted data source and the codegen drops the
emitted constants entirely. Scripts that pass an active value (e.g.
``backadjustment.on``) silently produce a different price series from
TradingView with no warning.

This module pins the new contract:

* Only no-op values are accepted: ``backadjustment.off`` / ``.inherit``,
  ``settlement_as_close.off`` / ``.inherit``, ``adjustment.none`` /
  ``.inherit``.
* Any active value (``.on``, ``adjustment.dividends``, ``.splits``, ...)
  must raise CompileError.
* Non-constant expressions must also raise — the codegen has no way to
  honor them.
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


def test_security_backadjustment_on_rejected():
    src = '''//@version=6
strategy("t")
x = request.security(syminfo.tickerid, "D", close, backadjustment=backadjustment.on)
'''
    with pytest.raises(CompileError, match="backadjustment"):
        _check(src)


def test_security_backadjustment_off_allowed():
    """backadjustment.off is the engine's de-facto behavior — accept."""
    src = '''//@version=6
strategy("t")
x = request.security(syminfo.tickerid, "D", close, backadjustment=backadjustment.off)
'''
    _check(src)  # should NOT raise


def test_security_settlement_as_close_on_rejected():
    src = '''//@version=6
strategy("t")
x = request.security(syminfo.tickerid, "D", close, settlement_as_close=settlement_as_close.on)
'''
    with pytest.raises(CompileError, match="settlement_as_close"):
        _check(src)


def test_security_adjustment_none_allowed():
    src = '''//@version=6
strategy("t")
x = request.security(syminfo.tickerid, "D", close, adjustment=adjustment.none)
'''
    _check(src)  # should NOT raise


def test_security_adjustment_splits_rejected():
    src = '''//@version=6
strategy("t")
x = request.security(syminfo.tickerid, "D", close, adjustment=adjustment.splits)
'''
    with pytest.raises(CompileError, match="adjustment"):
        _check(src)

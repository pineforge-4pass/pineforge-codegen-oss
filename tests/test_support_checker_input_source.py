"""input.source defval must be a native chart series.

PineForge restricts input.source strictly to native OHLCV series
(open/high/low/close/volume/hl2/hlc3/ohlc4/hlcc4). The engine's runtime
override (get_input_source) can only resolve to those base series; a user
series / computed expression / indicator output has no resolvable backing
series, so the support checker hard-rejects it rather than letting codegen
silently bind to the close fallback.
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


def test_input_source_close_allowed():
    src = '''//@version=6
strategy("t")
s = input.source(close, "Source")
plot(s)
'''
    _check(src)  # should NOT raise


def test_input_source_hl2_allowed():
    src = '''//@version=6
strategy("t")
s = input.source(hl2, "Source")
plot(s)
'''
    _check(src)


def test_input_source_user_series_rejected():
    """A user-defined series is not a native source — reject."""
    src = '''//@version=6
strategy("t")
myseries = close * 2
s = input.source(myseries, "Source")
plot(s)
'''
    with pytest.raises(CompileError, match=r"input\.source"):
        _check(src)


def test_input_source_expression_rejected():
    """A computed expression defval is not a native source — reject."""
    src = '''//@version=6
strategy("t")
s = input.source(close + open, "Source")
plot(s)
'''
    with pytest.raises(CompileError, match=r"input\.source"):
        _check(src)


def test_input_source_indicator_rejected():
    """An indicator output defval is not a native source — reject."""
    src = '''//@version=6
strategy("t")
s = input.source(ta.sma(close, 14), "Source")
plot(s)
'''
    with pytest.raises(CompileError, match=r"input\.source"):
        _check(src)

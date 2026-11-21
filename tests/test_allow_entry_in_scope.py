"""TradingView CE10188: allow_entry_in requires global scope."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


HEADER = '//@version=6\nstrategy("Local risk rule")\n'
CALL = "strategy.risk.allow_entry_in"


def _assert_scope_error(error, line, col):
    diagnostic, = error.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == "PF-E2097"
    assert diagnostic.args == {"function_name": CALL}
    assert diagnostic.message == f"Cannot use '{CALL}' in local scope."
    assert diagnostic.location.file == "local-risk.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize("check_support", [True, False])
def test_allow_entry_in_function_reports_the_call_location(check_support):
    source = HEADER + """risk() =>
    strategy.risk.allow_entry_in(strategy.direction.long)
risk()
if close > open
    strategy.entry("L", strategy.long)
"""
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="local-risk.pine", check_support=check_support)
    _assert_scope_error(caught.value, 4, 5)


@pytest.mark.parametrize("block,line,col", [
    (f"if close > open\n    {CALL}(value = strategy.direction.short)\n", 4, 5),
    (f"for i = 1 to 2\n    {CALL}(strategy.direction.all)\n", 4, 5),
    (f"switch\n    close > open =>\n        {CALL}(strategy.direction.long)\n", 5, 9),
], ids=["if-named-argument", "loop", "switch-arm"])
def test_allow_entry_in_is_refused_in_a_local_block(block, line, col):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + block, filename="local-risk.pine")
    _assert_scope_error(caught.value, line, col)


def test_direct_analyzer_refuses_allow_entry_in_in_an_unused_method():
    source = (
        HEADER + "method restrict(float self) =>\n"
        f"    {CALL}(strategy.direction.short)\n"
        "    self\n"
    )
    ast = Parser(
        Lexer(source).tokenize(), source=source, filename="local-risk.pine",
    ).parse()
    with pytest.raises(CompileError) as caught:
        Analyzer(ast, filename="local-risk.pine").analyze()
    _assert_scope_error(caught.value, 4, 5)

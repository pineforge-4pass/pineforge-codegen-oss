"""varip declarations pass the support checker.

Phase C refused ``varip`` because a batch backtest has no intrabar ticks and
lowering it as ``var`` looked like a silent demotion. TradingView's own tapes
show a historical bar executes the script once, so ``varip`` and ``var``
agree there, except that a calc_on_order_fills recalculation rolls ``var``
back and leaves ``varip`` alone. Codegen now lowers ``varip`` as ``var`` and
keeps it out of that rollback (tests/test_e2e_varip_coof.py replays the
tapes), so the checker accepts it without an error or a warning.
"""
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker


def _diagnostics(src: str) -> list:
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    return SupportChecker(ast).check()


def test_varip_int_accepted():
    src = '''//@version=6
strategy("t")
varip int tick_counter = 0
tick_counter := tick_counter + 1
'''
    assert not [d for d in _diagnostics(src) if "varip" in d.message]


def test_varip_float_accepted():
    src = '''//@version=6
strategy("t")
varip float acc = 0.0
acc := acc + close
'''
    assert not [d for d in _diagnostics(src) if "varip" in d.message]


def test_var_int_still_allowed():
    """Sanity: ``var`` must still pass."""
    src = '''//@version=6
strategy("t")
var int counter = 0
counter := counter + 1
'''
    assert not _diagnostics(src)

"""Strategy string slots use the same missing value as an omitted string."""

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("call,control,callee", [
    ("strategy.exit('Exit', na, profit=30000, loss=30000)",
     "strategy.exit('Exit', profit=30000, loss=30000)", "strategy_exit"),
    ("strategy.exit('Exit', comment=na)",
     "strategy.exit('Exit')", "strategy_exit_cancel_bracket"),
    ("strategy.entry(na, strategy.long, comment=na, oca_name=na)",
     "strategy.entry('', strategy.long, comment='', oca_name='')", "strategy_entry"),
    ("strategy.order(na, strategy.long, qty=1, oca_name=na)",
     "strategy.order('', strategy.long, qty=1, oca_name='')", "strategy_order"),
    ("strategy.close(na, comment=na)",
     "strategy.close('', comment='')", "strategy_close"),
    ("strategy.close_all(comment=na)",
     "strategy.close_all()", "strategy_close"),
    ("strategy.cancel(na)", "strategy.cancel('')", "strategy_cancel"),
])
def test_na_strategy_string_slots_match_empty_or_omitted(call, control, callee):
    def emitted(statement):
        cpp = transpile(
            '//@version=6\nstrategy("Missing strings")\n' + statement + '\n')
        return next(line.strip() for line in cpp.splitlines()
                    if line.strip().startswith(callee + "("))

    # Literal empty strings normally include std::string; an omitted slot
    # uses "". Both denote the same string in the host API.
    normalize = lambda text: text.replace('std::string("")', '""')
    assert normalize(emitted(call)) == normalize(emitted(control))

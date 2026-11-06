"""Strategy string slots use the same missing value as an omitted string."""

import json
import re

import pytest

from pineforge_codegen import transpile
from tests.test_order_shapes import RECEIPT_PATTERN, SOURCE


@pytest.mark.parametrize("call,control,callee", [
    ("strategy.exit('Exit', na, profit=30000, loss=30000)",
     "strategy.exit('Exit', profit=30000, loss=30000)", "strategy_exit"),
    ("strategy.exit('Exit', profit=30000, comment=na)",
     "strategy.exit('Exit', profit=30000)", "strategy_exit"),
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


# An order id that lowers to the constant empty string is not a computed id, so the id-sites
# receipt must not count it as a fresh id per bar; a computed id still is one.
@pytest.mark.parametrize(("expression", "fresh"), [
    ("na", 0), ('""', 0), ('"L"', 0), ('close > open ? "A" : "B"', 1),
])
def test_missing_order_id_is_not_counted_as_a_fresh_computed_id(expression, fresh):
    cpp = transpile(SOURCE + f"strategy.entry({expression}, strategy.long)\n")
    documents = re.findall(RECEIPT_PATTERN, cpp)
    # Capabilities, confirmed bar, order shapes, then the id-sites receipt.
    assert json.loads(json.loads(documents[3]))["strategy"]["ids"] == {"freshPerBar": fresh}

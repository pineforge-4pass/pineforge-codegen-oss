"""A tuple literal is refused where TradingView refuses it.

TradingView takes a tuple literal only as a function's or an if/switch arm's
value and as a request.security expression. ``lab tv --no-note`` exports of
the three probes in ``fixtures/open_items_tv`` fail at TradingView's compiler:

- ``tuple_literal_decl``: ``[a, b] = [close, open]`` is "Syntax error at input
  '['" (CE10156) at line 5, column 10;
- ``tuple_literal_var``: ``t = [close, open]``, the same error at line 5,
  column 5;
- ``tuple_literal_ternary``: ``[a, b] = close > open ? [close, open] :
  [open, close]`` is "Ternary operations cannot return tuples" (line 5).

PineForge dropped both tuple declarations ("/* unsupported tuple
assignment */", a and b left at 0.0) and emitted the plain declaration as a
tuple assigned to a double, which failed the C++ compile. The support
checker now refuses each, located where TradingView's error is. None of the
989 population, 536 scrapper or 643 public sources has one of the shapes, so
no script that runs today is refused.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._tv_tapes import FIXTURES


# probe -> (TradingView's error line, column or None when its log cut it off,
# the refusal's text)
REFUSED = {
    "tuple_literal_decl": (5, 10, "Syntax error at input '[', CE10156"),
    "tuple_literal_var": (5, 5, "Syntax error at input '[', CE10156"),
    "tuple_literal_ternary": (5, None, "Ternary operations cannot return tuples"),
}


@pytest.mark.parametrize("probe", sorted(REFUSED))
def test_the_tradingview_refusal_is_located_where_tradingview_puts_it(probe):
    line, col, text = REFUSED[probe]
    with pytest.raises(CompileError) as info:
        transpile((FIXTURES / f"{probe}.pine").read_text())
    errors = [d for d in info.value.diagnostics if text in d.message]
    assert len(errors) == 1, [d.message for d in info.value.diagnostics]
    assert errors[0].location.line == line
    if col is not None:
        assert errors[0].location.col == col


HEAD = '//@version=6\nstrategy("tuple values", overlay = true)\n'
TAIL = "\nif a > b\n    strategy.entry(\"L\", strategy.long)\n"
# Every tuple-literal position TradingView accepts still transpiles.
ACCEPTED = {
    "function_value": "pair(float x) => [x, x * 2]\n[a, b] = pair(close)",
    "function_if_arm": ("pair(float x) =>\n    if x > 0\n        [x, x * 2]\n"
                        "    else\n        [x * 2, x]\n[a, b] = pair(close)"),
    "if_arm": "[a, b] = if close > open\n    [close, open]\nelse\n    [open, close]",
    "switch_arm": "[a, b] = switch\n    close > open => [close, open]\n    => [open, close]",
    "security_expression": "[a, b] = request.security(syminfo.tickerid, \"60\", [close, open])",
    "method_value": "method pair(float x) => [x, x * 2]\n[a, b] = close.pair()",
}


@pytest.mark.parametrize("shape", sorted(ACCEPTED))
def test_every_tuple_position_tradingview_accepts_still_transpiles(shape):
    assert "unsupported tuple assignment" not in transpile(HEAD + ACCEPTED[shape] + TAIL)

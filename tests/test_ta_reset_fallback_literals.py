"""The legacy renderer of a runtime TA length leaves string literals alone.

A TA length that depends on an input is rendered by lowering its text through
the expression visitor; only when that lowering fails does a token-substitution
renderer run. That renderer must treat the text of a string literal as text,
not as identifiers: a literal that spells the name of an input variable or of a
known string constant must come out as the same literal. The visitor path
succeeds for every script found so far, so the rows below make it fail on
purpose to exercise the renderer.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen

_HEADER = (
    '//@version=6\nstrategy("fallback literals")\n'
    'mode = input.string("a", "Mode")\n'
    'fast = "QQQ"\n'
)
_TRADE = 'if close > open\n    strategy.entry("L", strategy.long)\n'

# Each row: the TA length expression and the literal comparisons that must
# survive in the generated C++ exactly as written.
_ROWS = [
    # The literal spells a known string constant (fast = "QQQ"); the text after
    # the comparison operator must still be the three characters f, a, s, t.
    ('mode == "fast" ? 5 : 10', ['== "fast"']),
    # The literal spells the input variable itself.
    ('mode == "mode" ? 5 : 10', ['== "mode"']),
    # Both in one expression, one nested under the other.
    ('mode == "fast" ? 5 : (mode == "mode" ? 3 : 7)', ['== "fast"', '== "mode"']),
]


@pytest.mark.parametrize("length,literals", _ROWS)
@pytest.mark.parametrize("placement", ["assigned", "inline"])
def test_forced_fallback_keeps_string_literals(monkeypatch, length, literals, placement):
    # Make the visitor path report failure for every length, so the renderer
    # under test is the one that produces the C++.
    monkeypatch.setattr(CodeGen, "_lower_reset_expr_via_visitor",
                        lambda self, expanded: None)
    if placement == "assigned":
        body = f"len = {length}\nx = ta.sma(close, len)\n"
    else:
        body = f"x = ta.sma(close, {length})\n"
    cpp = transpile(_HEADER + body + _TRADE)
    for literal in literals:
        # The literal comes out as the same literal.
        assert literal in cpp, literal
    # A literal that had been rewritten would show the constant's value or an
    # input getter call, with its own opening parenthesis and quote, right
    # after the literal's opening quote. (A bare quoted method name such as
    # "get_input_string" in a receipt is not that pattern.)
    assert '"std::string(' not in cpp
    assert not re.search(r'"get_input_\w+\("', cpp)

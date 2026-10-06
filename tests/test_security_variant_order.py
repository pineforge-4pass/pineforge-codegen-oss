"""A request.security TA site's variants are numbered in source order.

A TA site a payload reaches through several helper bindings gets one
requested-context variant per binding (``_sec0__ta_sma_1_v0``, ``_v1``,
...). Their signatures name each bound node by its ``id()``, and the
variants were numbered in ``sorted(..., key=repr)`` order of those
signatures (codegen/base.py): memory-address order, so the same script
could number its variants differently from one process or platform to the
next (CPython against Pyodide's wasm32 addresses; lane CG-SECURITY-2's
pre-existing defects). ``_security_variant_order_key`` numbers them by
where each binding's value is written, and the TU no longer depends on the
node ids. The test maps every signature's ids through a bijection that
reverses their order and requires the same C++.
"""

from __future__ import annotations

import pytest

import pineforge_codegen.codegen.security as security_module
from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import Identifier
from pineforge_codegen.errors import SourceLocation

SOURCES = {
    "arguments": ('//@version=6\nstrategy("variants")\n'
                  "f(float src, int n) => ta.sma(src, n)\n"
                  'v = request.security(syminfo.tickerid, "60", f(close, 5) + f(open, 7) + f(high, 9))\n'
                  "if v > 0\n    strategy.entry(\"L\", strategy.long)\n"),
    "nested": ('//@version=6\nstrategy("nested variants")\n'
               "f(float src) => ta.ema(src, 4)\n"
               "g(float x) => f(x) + f(x * 2)\n"
               'v = request.security(syminfo.tickerid, "60", g(close) - g(open))\n'
               "if v > 0\n    strategy.entry(\"L\", strategy.long)\n"),
}


def _reversed_ids(value):
    """A signature with every node id mapped through ``2**64 - id``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return 2**64 - value
    if isinstance(value, tuple):
        return tuple(_reversed_ids(v) for v in value)
    return value


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_variant_numbering_does_not_follow_node_ids(name, monkeypatch):
    source = SOURCES[name]
    natural = transpile(source)
    assert "_v1" in natural
    emitter = security_module.SecurityEmitter
    signature = emitter._security_binding_stack_signature
    monkeypatch.setattr(emitter, "_security_binding_stack_signature",
                        lambda self, stack: _reversed_ids(signature(self, stack)))
    assert transpile(source) == natural


def test_variants_follow_the_binding_positions():
    cpp = transpile(SOURCES["arguments"])
    body = cpp[cpp.index("void _eval_security_0"):]
    for i, field in enumerate(("close", "open", "high")):
        assert f"_sec0__ta_sma_1_v{i}.compute(bar.{field})" in body, (i, field)


def test_variant_position_ties_preserve_traversal_order():
    emitter = security_module.SecurityEmitter()
    location = SourceLocation(file="<input>", line=3, col=5, end_col=10)
    bindings = [
        ((("src", 9),), ({"src": Identifier(name="close", loc=location)},)),
        ((("src", 1),), ({"src": Identifier(name="open", loc=location)},)),
    ]
    ordered = sorted(
        bindings, key=lambda item: emitter._security_variant_order_key(*item),
    )
    assert ordered == bindings
    reversed_bindings = [
        (_reversed_ids(signature), stack) for signature, stack in bindings
    ]
    assert sorted(
        reversed_bindings,
        key=lambda item: emitter._security_variant_order_key(*item),
    ) == reversed_bindings


def test_expression_history_clear_order_preserves_registration_order():
    emitter = security_module.SecurityEmitter()
    emitter._security_expr_hist_by_node = {
        (1, 9): {"name": "_sec1_expr_hist_0"},
        (0, 5): {"name": "_sec0_expr_hist_0"},
        (1, 1): {"name": "_sec1_expr_hist_1"},
    }
    assert emitter._security_expr_hist_series_names(1) == [
        "_sec1_expr_hist_0", "_sec1_expr_hist_1",
    ]


def test_variant_local_snapshots_keep_their_stable_order():
    emitter = security_module.SecurityEmitter()
    argument = {"src": Identifier(name="close")}
    locals_frame = {"macd_val": Identifier(name="close"),
                    "macd_sig": Identifier(name="open")}
    stack = (argument, locals_frame)
    signatures = [
        ((("src", 9),), ()),
        ((("src", 9),), ("macd_val",)),
        ((("src", 9),), ("macd_sig", "macd_val")),
    ]
    assert sorted(
        signatures,
        key=lambda signature: emitter._security_variant_order_key(signature, stack),
    ) == list(reversed(signatures))

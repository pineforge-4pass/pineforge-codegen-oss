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

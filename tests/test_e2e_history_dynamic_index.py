"""A history read whose index is a runtime float compiles, and truncates.

``x[k]`` with a double-valued ``k`` narrows the offset through the
na-preserving lambda ``[&](){ ... }()`` (``helpers.na_preserving_int_cast``).
The history emitters of a call result, a precalculated ``ta.highest`` /
``ta.lowest``, a hoisted lazy-edge TA site and an operator expression spelled
it straight after the subscript's ``[`` (``codegen/visit_expr.py``), so the
C++ read ``_hist_call_1[[&](){ ... }()]``: C++ parses ``[[`` as an attribute
and the TU did not compile. cs-lev-tradleware-gaussian-channel-stochrsi-eth's
``ta.tr(true)[lag]`` with ``lag = (per - 1) / (2 * N)`` was one (lane W2's
open findings). ``_history_offset_cpp`` now parenthesizes a lambda offset.

TradingView truncates a fractional index toward zero: its tape of
``fixtures/open_items_tv/dyn_hist_index`` (lab tv --no-note) spells every
read at 5.5 or 2.9 beside the reads at the integers on either side, and each
equals the lower one. The replay compares every exit comment on the corpus
ETH 15m feed from TradingView's first bar.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


HEAD = ('//@version=6\nstrategy("dynamic history index", overlay = true)\n'
        'lag = input.int(45, "per") / 8\n'
        "f(float v) => v * 2\n")
# One read per emitter whose offset used to follow the subscript's "[";
# each binds the variable V.
SHAPES = {
    "call_result": "V = ta.tr(true)[lag]",
    "user_call_result": "V = f(close)[lag]",
    "precalc_highest": "V = ta.highest(high, 3)[lag]",
    "operator_expression": "V = (close - open)[lag]",
    "lazy_and_hoisted": "V = close > 0 and ta.sma(close, 3)[lag] > 0 ? 1.0 : 0.0",
    "ternary_arm_hoisted": "V = close > 0 ? ta.sma(close, 3)[lag] : na",
}


def _script(shapes: list[str]) -> str:
    body = "\n".join(SHAPES[s].replace("V", f"v_{s}", 1) for s in shapes)
    reads = " + ".join(f"nz(v_{s})" for s in shapes)
    return f"{HEAD}{body}\nif {reads} > 0\n    strategy.entry(\"L\", strategy.long)\n"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_history_offset_opens_an_attribute(shape):
    cpp = transpile(_script([shape]))
    assert "[[" not in cpp
    assert "[([&]()" in cpp


def test_every_history_emitter_compiles():
    compile_env.compile_cpp(transpile(_script(sorted(SHAPES))),
                            label="dynamic history index")


def test_the_dynamic_index_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    feed = window_feed(engine, tmp_path)
    runs = execute_all(engine, feed, tmp_path, {
        "dyn_hist_index": Build((FIXTURES / "dyn_hist_index.pine").read_text())})
    ok(runs, "dyn_hist_index")
    trades = tape("dyn_hist_index")
    assert len(trades) == 7
    misses = exit_misses(engine, tmp_path / "dyn_hist_index", feed, trades)
    assert not misses, misses[:3]

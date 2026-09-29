"""``ta.change`` / ``ta.mom`` / ``ta.roc`` in an if block read their held
source history (lane TAIL-E, item 4).

A call below a lazy operand (``cond ? ta.roc(close, 3) : na``) reads the
call's own ``source[length]`` history, written on the bars it executes and
held on the bars it skips (``tests/test_lazy_source_clock.py``). A call in an
if block runs only on the bars its block runs too, and TradingView reads it
the same way; the codegen kept the executions' own ring there, so
``ta.roc(close, 3)`` in a nested if read na until its fourth execution and
then the close three executions back. The stateful ROC oracles
(pf-probe-quant-stateful-roc-*) booked admission trades TradingView did not:
TradingView's reached ROC there reads the close of the previous reach, 16
bars back, the one the held history holds three bars back. Calls in top-level
if blocks -- an else-if's and a nested if's included -- now take the hold-last
clock; loop and switch bodies keep their lowering.

TradingView's tape of ``te_lazy_if_source`` (``fixtures/tail_e_tv``) spells
the last value of each call on every bar.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches, source, tape_rows,
)

NAME = "te_lazy_if_source"


def test_block_calls_take_the_hold_last_clock():
    cpp = transpile(source(NAME))
    for n in (1, 2, 3, 4):
        assert f"_pf_lazy_src_clock_{n}." in cpp, n
    loop = transpile('//@version=6\nstrategy("l")\nvar float r = na\n'
                     'for i = 0 to 2\n    r := ta.roc(close, 3)\n'
                     'if r > 0\n    strategy.entry("L", strategy.long)\n')
    assert "_pf_lazy_src_clock_" not in loop


def test_the_lazy_if_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows(f"{NAME}_eth15")
    assert len(tape) == 384
    chart = feed(engine, tmp_path, START_MS + 4 * DAY_MS + BAR_MS)
    rows, _ = engine_rows(engine, build(tmp_path, NAME, source(NAME)), chart)
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])

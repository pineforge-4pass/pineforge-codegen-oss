"""``for`` and ``while`` loops in a ``request.security`` helper (lane TAIL-E,
item 1).

A multi-statement helper with control flow was refused ("request.security
does not support multi-statement helpers with control flow"): the TMO shape
of hary349veo3-tmo-dual-timeframe-strategy-hary -- a helper scoring the
requested close against its last n opens in a ``for`` loop, smoothing the
score through a helper whose if/else chain picks the TA call -- never
transpiled. The linear helper lowering now runs the loop on the requested
bar: the counter is a local, ``o[i]`` reads the requested bar's open ``i``
requested bars back, ``for`` keeps Pine's direction, step and ``to`` refresh,
and ``while`` / ``break`` / ``continue`` lower as themselves. A loop body holds
plain locals only: the evaluator computes each TA call once per requested bar
at its place in the helper, pushes each history-read local once, and keeps a
``var`` local across requested bars, so a TA call, a ``var``, a local read
with history or a user function call in a loop is refused by name.

TradingView's tapes of ``te_sec_loop_helper`` (the hour with lookahead off
and on, the chart's own 15 minutes, a ``while`` loop that breaks) and
``te_sec_loop_ltf`` (5 and 3 minutes, under the chart) spell every value on
every bar (``fixtures/tail_e_tv``). They replay with the verifier's two run
flags for what the loop does not touch: the historical lookahead projection
(TradingView's lookahead_on reads the requested bar's final value) and the
range-start warmup (TradingView's ta.ema seeds from the SMA of its first
values); without them only those fields part.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, aux_1m_feed, build, engine_rows, feed, mismatches, source,
    tape_rows,
)

FLAGS = {"historical_security_lookahead_projection": 1, "security_range_start_na_warmup": 1}
END_MS = START_MS + 7 * DAY_MS + BAR_MS


def _loop_values(*keep: int):
    """The loop's own values of a row: tally()'s raw scores and streak(),
    those the run flags do not reach."""
    def pick(signal: str) -> str:
        fields = (signal or "").split("|")
        return "|".join(fields[k] for k in keep if k < len(fields))
    return pick


def test_the_loop_helper_lowers_in_the_requested_context():
    cpp = transpile(source("te_sec_loop_helper"))
    evaluator = cpp[cpp.index("void _eval_security_0("):cpp.index("void _eval_security_1(")]
    assert "for (int _sec0_tally_" in evaluator
    assert "_sec0_hist_open[_hidx - 1]" in evaluator
    streak = cpp[cpp.index("void _eval_security_3("):cpp.index("void evaluate_security(")]
    assert "while (" in streak and "break;" in streak


def test_the_hour_and_chart_timeframe_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows("te_sec_loop_helper_eth15")
    assert len(tape) == 672
    chart = feed(engine, tmp_path, END_MS)
    workdir = build(tmp_path, "loop", source("te_sec_loop_helper"))
    rows, _ = engine_rows(engine, workdir, chart, syminfo_metadata=FLAGS)
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])
    # Without the flags the loop's own values still replay.
    plain, _ = engine_rows(engine, workdir, chart)
    missed = mismatches(tape, plain, drop=_loop_values(0, 6, 9))
    assert not missed, "\n".join(missed[:5])


def test_the_lower_timeframe_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows("te_sec_loop_ltf_eth15")
    assert len(tape) == 672
    chart = feed(engine, tmp_path, END_MS)
    aux = aux_1m_feed(engine, tmp_path, END_MS)
    workdir = build(tmp_path, "ltf", source("te_sec_loop_ltf"))
    lower = dict(input_tf="15", script_tf="15", aux_security_ohlcv_csv=aux,
                 aux_security_input_tf="1")
    rows, _ = engine_rows(engine, workdir, chart, syminfo_metadata=FLAGS, **lower)
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])
    plain, _ = engine_rows(engine, workdir, chart, **lower)
    missed = mismatches(tape, plain, drop=_loop_values(0, 3, 6, 9))
    assert not missed, "\n".join(missed[:5])


HELPER = '''//@version=6
strategy("loop refusals")
f(float c, int n) =>
    s = 0.0
    for i = 0 to n
{body}
    s
x = request.security(syminfo.tickerid, "60", f(close, 3))
if x > 0
    strategy.entry("L", strategy.long)
'''


@pytest.mark.parametrize("body, what", [
    ("        s := s + ta.sma(c, 3)", "a TA call"),
    ("        var float k = 0.0\n        s := s + k", "a var declaration"),
    ("        s := s + g(c)", "a user function call"),
])
def test_what_a_loop_body_cannot_repeat_is_refused(body, what):
    src = HELPER.replace("{body}", body)
    if "g(c)" in body:
        src = src.replace("f(float c, int n) =>", "g(float v) => v * 2\nf(float c, int n) =>")
    with pytest.raises(CompileError) as err:
        transpile(src)
    assert any(f"request.security helper loops cannot hold {what}" in d.message
               for d in err.value.diagnostics), [d.message for d in err.value.diagnostics]


def test_a_for_in_loop_stays_refused():
    src = HELPER.replace("    for i = 0 to n\n{body}\n",
                         "    for v in array.from(1.0, 2.0)\n        s := s + v\n")
    with pytest.raises(CompileError, match="control flow"):
        transpile(src)

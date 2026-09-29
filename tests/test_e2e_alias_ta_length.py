"""A TA length read through a plain alias of an input-derived scalar.

``len = prod`` over ``prod = a * b`` holds the product of the two inputs on
every bar. The known-value pass folded the alias to its literal and marked it
input-backed, but recorded no expression for it, so the ``_ta_initialized_``
reset (which rebuilds every input-sized indicator from the inputs' getters
before the first bar's body runs) spelled the alias's own member:
``ta::EMA(len)`` read ``len`` before the body assigned it, 0 on the first bar,
and ``ta.ema(close, len)`` ran as an EMA of length 0 for the whole run (the
two mm-mitsuya norn-weave scripts: ``emaLen = calcEmaLen`` over
``calcEmaLen = swingLen * emaRatio``, so their EMA trend filter was wrong on
every bar).

An alias of a derived stable scalar is now recorded as a derived expression of
the name it copies, so the reset re-expands it through the chain to the
inputs' override-aware reads. TradingView's tapes of
``fixtures/alias_len_tv/cgt_alias_len`` (and its twin with the input ``B`` at
6) spell ``ta.ema``, ``ta.sma``, ``ta.rsi`` and ``ta.highest`` over the alias
and over an alias of the alias on each close.
"""

from __future__ import annotations

import re
from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, closed_trades, execute_all, ok, skip_unless_e2e_env
from tests._security_tapes import mismatches, source, tape_exits, tape_feed

FIXTURES = Path(__file__).parent / "fixtures" / "alias_len_tv"
NAME = "cgt_alias_len"
TWIN = "cgt_alias_len_b6"
# TradingView seeds ta.ema at the run's first bar with the SMA of its first
# `length` closes (na before): the engine's chart EMA warmup of a run that
# starts on the range's first bar, the mode the verifier's range-start rung runs.
RANGE_START_EMA = {"chart_ema_na_warmup": 1}


def _resets(cpp: str) -> list[str]:
    block = cpp[cpp.index("if (!_ta_initialized_) {"):]
    return block[:block.index("_ta_initialized_ = true;")].splitlines()


def test_the_reset_reads_the_alias_through_the_inputs():
    cpp = transpile(source(NAME, FIXTURES))
    resets = "\n".join(_resets(cpp))
    for cls in ("EMA", "SMA", "RSI", "Highest"):
        line = next(ln for ln in resets.splitlines() if f"= ta::{cls}(" in ln)
        # The alias's own member is never the length: it is 0 until the body
        # assigns it, after the reset has run.
        assert not re.search(rf"ta::{cls}\((len|len2)\)", line), line
        assert 'get_input_int("A", 3)' in line and 'get_input_int("B", 4)' in line, line
    compile_cpp(cpp)


def test_an_alias_of_a_direct_input_keeps_its_input_read():
    cpp = transpile(
        '//@version=6\nstrategy("alias of an input")\n'
        'n = input.int(7, "N")\nlen = n\nlen2 = len\n'
        "plot(ta.ema(close, len2))\n")
    line = next(ln for ln in _resets(cpp) if "= ta::EMA(" in ln)
    assert 'ta::EMA(get_input_int("N", 7))' in line, line
    compile_cpp(cpp)


def test_the_alias_length_tapes_replay_with_an_override(tmp_path):
    engine = skip_unless_e2e_env()
    builds = {NAME: Build(source(NAME, FIXTURES)), "override": Build(source(NAME, FIXTURES)),
              TWIN: Build(source(TWIN, FIXTURES))}
    feed = tape_feed(engine, tmp_path)
    runs = execute_all(engine, feed, tmp_path, builds)
    params = {"override": {"B": "6"}}
    for name, run in ((NAME, NAME), (TWIN, TWIN), (TWIN, "override")):
        ok(runs, run)
        trades = closed_trades(engine, tmp_path / run, feed, params.get(run),
                               syminfo_metadata=RANGE_START_EMA)
        exits = {t["exit_time"]: t["exit_comment"] for t in trades}
        tape = tape_exits(name, FIXTURES)
        assert len(tape) == 336
        missed = mismatches(tape, exits)
        assert not missed, f"{run}: {len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"alias TA length: {len(tape)} of {len(tape)} exit Signals equal TradingView's, "
          "and the B=6 override replays the twin's tape")

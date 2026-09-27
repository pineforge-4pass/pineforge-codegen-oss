"""``bar_index`` in a request.security payload is the requested bar's.

TradingView evaluates a payload's ``bar_index`` in the requested context:
the count of requested bars before the one evaluated, published like any
payload value (a 15m chart bar that closes with its 60m bar reads that 60m
bar's index), with ``bar_index[k]`` k requested bars back and ``na``
before the first. The evaluator read the chart's (``pine_bar_index()``),
bare, under a builtin and through a helper (lane CG-SECURITY-2's
pre-existing defects). Each evaluator that reads it now keeps a counter
(``_sec<N>_bar_index_``) advanced where it opens a requested slot, as its TA
sites advance.

TradingView's tape of ``fixtures/open_items_tv/sec_bar_index`` (lab tv
--no-note, BINANCE:ETHUSDT.P 15, 265 closes) spells the payload's
``bar_index`` on "60" and "240", ``nz(bar_index) + 1``, a helper's
``bar_index * 2``, ``bar_index[1]`` and the chart timeframe's, beside the
chart's own; the replay compares all 265 closes.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay
from tests._tv_tapes import FIXTURES, exits_by_time

NAME = "sec_bar_index"


def test_the_requested_bar_index_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    engine_exits = replay(engine, tmp_path, {NAME: Build((FIXTURES / f"{NAME}.pine").read_text())})[NAME]
    tape = exits_by_time(NAME)
    assert len(tape) == 265
    missed = mismatches(tape, engine_exits)
    assert not missed, f"{len(missed)} of {len(tape)}: {missed[:3]}"


def test_a_script_variable_named_bar_index_is_not_the_counter():
    cpp = transpile('//@version=6\nstrategy("shadow")\nbar_index = close * 2\n'
                    'v = request.security(syminfo.tickerid, "60", bar_index)\n'
                    "if v > 0\n    strategy.entry(\"L\", strategy.long)\n")
    assert "_sec0_bar_index_" not in cpp


def test_bar_index_beside_a_call_keeps_the_call_on_the_requested_bar():
    # bar_index used to count as a chart read, which sent a user call beside
    # it under the builtin back to the chart (every earlier build's lowering).
    cpp = transpile('//@version=6\nstrategy("beside")\nf(x) => x * 2\n'
                    'v = request.security(syminfo.tickerid, "60", nz(f(close) * bar_index))\n'
                    "if v > 0\n    strategy.entry(\"L\", strategy.long)\n")
    body = cpp[cpp.index("void _eval_security_0"):]
    body = body[:body.index("\n    }\n")]
    assert "(bar.close * 2) * _sec0_bar_index_" in body, body

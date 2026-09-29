"""A helper returning a ``request.security`` of a TA tuple compiles.

``htf() => request.security(syminfo.tickerid, "D", ta.macd(close, 12, 26,
9))`` read as ``[m, s, h] = htf()``: the request stores the TA's result
struct (``ta::MACDResult`` in ``_req_sec_N``), and the helper was emitted
returning a ``double`` (or a ``std::tuple`` it cannot convert to), so it did
not compile, on the chart's symbol and on another's (XSYM-E u04, q02). The
helper returns the request's result struct now
(``_security_helper_request_struct``), which the caller's structured binding
decomposes.

TradingView's tape of ``fixtures/silent2_tv/cgs2_ta_tuple_helper`` spells
``ta.bb`` bands requested through two such helpers, from 08:00 UTC on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_ta_tuple_helper"


def test_the_ta_tuple_helper_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 320
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"TA tuple helper: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


@pytest.mark.parametrize("helper, struct", [
    ('htf() => request.security(syminfo.tickerid, "D", ta.macd(close, 12, 26, 9))\n'
     "[a, b, c] = htf()\n", "ta::MACDResult htf"),
    ('htf() => request.security(syminfo.tickerid, "60", ta.dmi(14, 14))\n'
     "[a, b, c] = htf()\n", "ta::DMIResult htf"),
    ('f(sym, len) => request.security(sym, "60", ta.macd(close, len, 26, 9))\n'
     '[a, b, c] = f("PF:A", 12)\n[a2, b2, c2] = f("PF:B", 8)\n', "ta::MACDResult f"),
])
def test_a_ta_tuple_request_helper_returns_its_struct(helper, struct):
    cpp = transpile(
        "//@version=6\n"
        'strategy("ta tuple helper")\n'
        + helper +
        "if a > b\n"
        '    strategy.entry("L", strategy.long)\n')
    assert struct in cpp
    compile_cpp(cpp)

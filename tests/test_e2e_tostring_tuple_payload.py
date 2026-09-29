"""``str.tostring`` in a ``request.security`` tuple payload compiles.

The TU-local TradingView number formatter (``pine_str_tostring_tv``,
``codegen/tv_number_format.py``) is emitted only when the script calls
``str.tostring`` / ``str.format`` / a formatting ``log.*``, and that scan used
``_walk_ast``, which does not enter a tuple literal: ``[str.tostring(close),
close > open]`` as a payload left the formatter undeclared and the TU did not
compile. The scan reads every syntax child now (``iter_ast_nodes``).

TradingView's tape of ``fixtures/silent2_tv/cgs2_tostring_tuple_payload``
spells the requested strings on every close from 02:00 UTC on.
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_tostring_tuple_payload"


def test_the_tostring_tuple_payload_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 332
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"tostring tuple payload: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_a_tuple_payload_brings_the_number_formatter():
    cpp = transpile(
        "//@version=6\n"
        'strategy("tostring tuple")\n'
        '[x, y] = request.security(syminfo.tickerid, "60", [str.tostring(close), close > open])\n'
        'if y and x != ""\n'
        '    strategy.entry("L", strategy.long)\n')
    assert "static std::string pine_str_tostring_tv(double value" in cpp
    compile_cpp(cpp)

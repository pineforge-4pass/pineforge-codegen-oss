"""A sign applied to a negative constant compiles.

A never-reassigned numeric name is inlined as its literal, so ``-NEG`` over
``NEG = -5`` rendered as ``(--5)``: a decrement of a literal, which does not
compile, on the chart and in a ``request.security`` payload alike. The
operand is now parenthesized when its text starts with a sign
(``unary_sign_cpp``); every other operand keeps its spelling.

TradingView's tape of ``fixtures/silent2_tv/cgs2_negated_constants`` spells
``-NEG * 2``, ``+NEG``, ``-FNEG`` and ``-NEG``.
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_negated_constants"


def test_the_negated_constants_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336 and set(tape.values()) == {"10|-5|2.5|5"}
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"negated constants: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_a_negated_negative_constant_is_parenthesized():
    cpp = transpile(
        "//@version=6\n"
        'strategy("negated")\n'
        "NEG = -5\n"
        "y = -NEG * 2\n"
        "x = request.security(syminfo.tickerid, \"60\", -NEG * close)\n"
        "k = -close\n"
        "if y > 0 and x > k\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "--5" not in cpp
    assert "y = ((-(-5)) * 2);" in cpp
    assert "_req_sec_0 = ((-(-5)) * bar.close);" in cpp
    # An operand that does not start with a sign keeps its spelling.
    assert "k = (-current_bar_.close);" in cpp
    compile_cpp(cpp)

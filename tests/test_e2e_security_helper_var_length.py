"""A helper's ``var`` as a ``ta.*`` length in a request.security payload is
read on the requested bars.

The payload builder plans each requested-context TA copy from the helper
locals its length reads, bound to the expression each holds. A ``var`` local
was bound to its initializer, a compound assignment to its right-hand side
alone, and a self-reading assignment to an expression reading its own
binding. So ``var int c = -1; c += 1; ta.highest(high, c % 5 + 1)`` built a
``ta::Highest`` of the constant length ``1 % 5 + 1``, whatever ``c`` held on
the requested bar (lane CGINT4 found it); ``c := c + 1`` recursed until
Python's stack ran out; and a local an if arm rebinds kept the value it held
before the if.

Each such local is now a value of the requested bar, as the evaluator keeps
it: ``ta.highest`` / ``ta.lowest`` (and the ``bars`` forms) re-window every
call with it (``pineforge::source::Series*``), and any other ``ta.*`` is
refused naming the local. TradingView refuses such a length for
``ta.ema`` (a series int where a simple int is expected) and computes
``ta.sma`` over it, which PineForge does not lower.

TradingView's tape of ``fixtures/silent_tv/cgs_sec_var_len`` reads each
shape on the 60- and 240-minute bars, one with ``lookahead_on``, beside the
chart's call. The engine replays it with its historical lookahead projection
(``historical_security_lookahead_projection``, one of the verifier's
candidates): without it the ``lookahead_on`` field reads the requested bar
in progress, and every other field still replays.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, closed_trades, execute_all, ok, skip_unless_e2e_env
from tests._security_tapes import mismatches, source, tape_exits, tape_feed

FIXTURES = Path(__file__).parent / "fixtures" / "silent_tv"
NAME = "cgs_sec_var_len"
LOOKAHEAD_ON_FIELD = 6  # ``hon``


def _engine_exits(tmp_path, metadata: dict | None) -> dict[int, str]:
    engine = skip_unless_e2e_env()
    feed = tape_feed(engine, tmp_path)
    ok(execute_all(engine, feed, tmp_path, {NAME: Build(source(NAME, FIXTURES))}), NAME)
    kwargs = {"syminfo_metadata": metadata} if metadata else {}
    return {t["exit_time"]: t["exit_comment"]
            for t in closed_trades(engine, tmp_path / NAME, feed, **kwargs)}


def test_the_helper_var_length_tape_replays(tmp_path):
    exits = _engine_exits(tmp_path, {"historical_security_lookahead_projection": 1})
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"helper var lengths: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_every_field_but_lookahead_on_replays_without_the_projection(tmp_path):
    exits = _engine_exits(tmp_path, None)
    tape = tape_exits(NAME, FIXTURES)
    drop = lambda s: "|".join(f for i, f in enumerate((s or "").split("|")) if i != LOOKAHEAD_ON_FIELD)
    missed = [ms for ms, signal in tape.items() if drop(exits.get(ms)) != drop(signal)]
    assert not missed, f"{len(missed)} of {len(tape)} exits differ"


def test_the_requested_copies_read_the_helper_state():
    cpp = transpile(source(NAME, FIXTURES))
    # ``var int c`` / ``var int n`` / ``var int k``: re-windowed with the
    # value the evaluator keeps for the local, never a constant class.
    for sec, cls in ((0, "SeriesHighest"), (1, "SeriesLowest"), (2, "SeriesHighest"),
                     (5, "SeriesHighest"), (6, "SeriesHighest")):
        assert re.search(rf"pineforge::source::{cls} _sec{sec}__ta_\w+;", cpp), sec
    assert '_security_helper_series_["_sec0_tw_1_c"][0]' in cpp
    # ``int p = 3; p += 2``: the requested-bar value, re-windowed too.
    assert re.search(r"pineforge::source::SeriesLowest _sec3__ta_lowest_\w+;", cpp)
    # ``q`` rebound in an if arm: the local the evaluator assigns.
    assert "pineforge::source::ta_number(_sec4_tq_5_q)" in cpp
    assert not re.search(r"ta::(Highest|Lowest)\(", cpp.split("void _eval_security_0")[1])
    compile_cpp(cpp)


def test_a_self_reading_var_update_plans_without_recursing():
    cpp = transpile(
        "//@version=6\n"
        'strategy("self-reading var")\n'
        "f() =>\n"
        "    var int c = 0\n"
        "    c := c + 1\n"
        "    ta.highest(high, c % 4 + 1)\n"
        'x = request.security(syminfo.tickerid, "60", f())\n'
        "plot(x)\n"
    )
    assert "pineforge::source::SeriesHighest _sec0__ta_highest_1;" in cpp


@pytest.mark.parametrize("update", ["c += 1", "c := c % 5 + 1"])
@pytest.mark.parametrize("call", ["ta.sma(close, c)", "ta.ema(close, c)", "ta.rsi(close, c)"])
def test_another_family_is_refused_naming_the_local(update, call):
    with pytest.raises(CompileError) as caught:
        transpile(
            "//@version=6\n"
            'strategy("var length refused")\n'
            "f() =>\n"
            "    var int c = 1\n"
            f"    {update}\n"
            f"    {call}\n"
            'x = request.security(syminfo.tickerid, "60", f())\n'
            "plot(x)\n"
        )
    message = str(caught.value)
    assert "requested-context TA constructor length 'c'" in message
    assert "it reads 'c', a helper local holding a value of each requested bar" in message


def test_an_indirect_self_reading_update_plans_without_recursing():
    # ``b = a`` then ``a := b + 1``: the planner followed b back to a forever.
    cpp = transpile(
        "//@version=6\n"
        'strategy("indirect self-reading local")\n'
        "f() =>\n"
        "    int a = 1\n"
        "    int b = a\n"
        "    a := b + 1\n"
        "    ta.highest(high, a % 5 + 1)\n"
        'x = request.security(syminfo.tickerid, "60", f())\n'
        "plot(x)\n"
    )
    assert "pineforge::source::SeriesHighest _sec0__ta_highest_1;" in cpp


@pytest.mark.parametrize("call, member", [
    # (The requested EMA's seeding wrapper is not this rule's.)
    ("ta.ema(close, q)", r"(_PFRequestedEma<)?ta::EMA>? _sec0__ta_ema_1;"),
    ("ta.highest(high, q)", r"ta::Highest _sec0__ta_highest_1;"),
])
def test_a_local_an_arm_only_declares_keeps_its_value(call, member):
    # ``q = 6`` in the arm declares a local of its own; only ``z`` is
    # reassigned there, so the length keeps q's constant 2.
    cpp = transpile(
        "//@version=6\n"
        'strategy("arm shadow")\n'
        "f() =>\n"
        "    int q = 2\n"
        "    float z = 0.0\n"
        "    if close > open\n"
        "        q = 6\n"
        "        z := q * 1.5\n"
        f"    {call} + z\n"
        'x = request.security(syminfo.tickerid, "60", f())\n'
        "plot(x)\n"
    )
    assert re.search(member, cpp), member
    compile_cpp(cpp)


def test_an_arm_reassigning_its_own_shadow_keeps_the_enclosing_value():
    # ``q = 6`` declares the arm's q; ``q := q + 1`` reassigns that one.
    cpp = transpile(
        "//@version=6\n"
        'strategy("arm shadow reassigned")\n'
        "f() =>\n"
        "    int q = 2\n"
        "    float z = 0.0\n"
        "    if close > open\n"
        "        q = 6\n"
        "        q := q + 1\n"
        "        z := q * 1.5\n"
        "    ta.highest(high, q) + z\n"
        'x = request.security(syminfo.tickerid, "60", f())\n'
        "plot(x)\n"
    )
    assert "ta::Highest _sec0__ta_highest_1;" in cpp
    compile_cpp(cpp)

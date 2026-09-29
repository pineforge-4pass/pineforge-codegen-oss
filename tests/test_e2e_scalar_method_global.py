"""A typed method called on a global scalar resolves its method.

``method m(float x) => ta.sma(x, 3)`` called as ``s5.m()`` over a global
``s5 = ta.sma(close, 5)`` resolves its method through the receiver's type,
and a global scalar is recorded as bound to no user type: that tombstone hid
its primitive type (``_type_spec_from_expr`` answered None), so the call was
emitted raw, ``s5.m()``, a member call on a double, which did not compile on
the chart or in a ``request.security`` payload. The receiver's own scalar type
now resolves the method (``_scalar_receiver_spec``): the chart calls the
method's variant, and a payload inlines it on the requested bar.

TradingView's tape of ``fixtures/silent2_tv/cgs2_scalar_method_global``
spells ``s5.m()`` and ``s5.half()`` on the chart and ``s5.m() + s5.half()``
requested at 60 minutes, from 08:00 UTC on.
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_scalar_method_global"


def test_the_scalar_method_global_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 320
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"scalar method global: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_a_method_on_a_global_scalar_resolves():
    cpp = transpile(
        "//@version=6\n"
        'strategy("scalar method")\n'
        "method m(float x) => ta.sma(x, 3)\n"
        "s5 = ta.ema(close, 5)\n"
        "y = s5.m()\n"
        'a = request.security(syminfo.tickerid, "60", s5.m() + ta.rsi(close, 3))\n'
        "if a > y\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "s5.m()" not in cpp
    assert "y = _udt_float_m_cs0(s5);" in cpp
    evaluator = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    assert "_sec0__ta_sma_1.compute(_secval_1)" in evaluator
    compile_cpp(cpp)

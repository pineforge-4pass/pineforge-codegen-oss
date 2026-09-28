"""A function whose last statement binds a variable returns its value.

Pine returns a function's last statement's value; for a declaration
(``float result = ...``) or a reassignment (``result := ...``, ``acc += 1``)
that is the variable's value. TradingView's own ``TradingView/ta/7`` library
ends every function that way. The codegen emitted the statement and then
the default return, ``0.0``, so every such function returned 0, and a
reassignment's return took the assigned value's type (``acc += 1`` of a float
returned an int). None of the 627 public sources, and one population script
(DonovanWall's ``f_filt9x``, ending in ``_f := ...``), has such a function.

TradingView's tape of ``xc_decl_return`` (``fixtures/xsym_lib_tv``,
BINANCE:ETHUSDT.P 15) spells each function's value in every exit Signal, and
PineForge reproduces all 96.
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "xsym_lib_tv"


def test_the_bound_variable_is_returned():
    cpp = transpile('//@version=6\nstrategy("T")\n'
                    'twice(float x) =>\n    float result = x * 2\n'
                    'bump(float x) =>\n    float acc = x\n    acc += 1\n'
                    'y = twice(close) + bump(close)\n'
                    'if y > 0\n    strategy.entry("L", strategy.long)\n')
    assert "return result;" in cpp and "return acc;" in cpp
    assert "double bump(" in cpp
    assert "return 0.0;" not in cpp.split("double twice(")[1].split("}")[0]


def test_function_last_bindings_replay_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_decl_return")
    exits = replay(engine, base, {"probe": Build(source("xc_decl_return", FIXTURES))})
    tape = {k: v for k, v in tape_exits("xc_decl_return", FIXTURES).items() if v}
    assert len(tape) == 96
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])

"""A string ``?:`` whose other arms are strings reads a bare ``na`` arm as the
string na: ``cond ? "BUY" : na`` is how a library returns a signal (the
open ``richardgong1988/HanJinSignals26`` libraries' ``pinbar``, ``engulf``,
``fractal``, ``harami`` and ``bigbody``). The arm used to be emitted as
``na<double>()`` beside a ``std::string``, which C++ does not compile, so
every script reaching such a function failed to build. PineForge stores a
string na as the empty string (``na<std::string>()``), which ``na()`` reads as
na.

TradingView's tape of ``xc_string_na`` (``fixtures/xsym_lib_tv``,
BINANCE:ETHUSDT.P 15) spells ``na()`` of each such value in every exit
Signal, and PineForge reproduces all 96.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "xsym_lib_tv"


@pytest.mark.parametrize("body", [
    's = close > open ? "UP" : na',
    's = close > open ? na : "DN"',
    's = close > open ? "UP" : close < open ? "DN" : na',
    'f() => close > open ? "UP" : na\ns = f()',
    '[a, s] = [close > open ? "UP" : na, "x"]',
])
def test_string_ternary_na_arm_is_the_string_na(body):
    cpp = transpile('//@version=6\nstrategy("T")\n' + body
                    + '\nif na(s)\n    strategy.entry("L", strategy.long)\n')
    assert "na<std::string>()" in cpp
    assert ': (na<double>())' not in cpp and '(na<double>()) :' not in cpp


def test_numeric_ternary_na_arm_is_unchanged():
    cpp = transpile('//@version=6\nstrategy("T")\nx = close > open ? 1.0 : na\nplot(x)\n')
    assert "? (1.0) : (na<double>()))" in cpp


def test_string_na_replays_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_string_na")
    exits = replay(engine, base, {"probe": Build(source("xc_string_na", FIXTURES))})
    # TradingView lists the position still open at the window's end with an
    # empty Signal: no close was sent for it.
    tape = {k: v for k, v in tape_exits("xc_string_na", FIXTURES).items() if v}
    assert len(tape) == 96
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])

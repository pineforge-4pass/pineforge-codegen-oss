"""A ``switch``-valued local in a ``request.security`` helper.

``ma = switch maType`` in a helper the payload calls rendered as
``/* unknown */`` in the evaluator, which did not compile
(michaellitton16-money-mike-macd-rsi-watchlist-mtf-strategy, the one
population script with the shape). It now lowers to the ternary chain of its
arms: the requested bar computes the arm the selector takes, and no other.
TradingView's tape of ``xa_switch_helper`` (``fixtures/xsym_tv``) spells the
60-minute RSI and its SMA on every close; under the override ``MA = EMA`` the
probe trades like its literal twin. A switch with a multi-statement arm is
refused by name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, closed_trades, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"
EMA_TWIN = source("xa_switch_helper", XSYM_TV).replace(
    '''    ma = switch maType
        "SMA" => ta.sma(r, 5)
        "EMA" => ta.ema(r, 5)
        => ta.wma(r, 5)
''', '''    ma = ta.ema(r, 5)
''')


def test_switch_local_in_helper_replays_tradingview_tape(tmp_path_factory):
    probe = source("xa_switch_helper", XSYM_TV)
    assert "/* unknown */" not in transpile(probe)
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_switch_helper")
    exits = replay(engine, base, {"probe": Build(probe), "ema": Build(EMA_TWIN)})
    tape = tape_exits("xa_switch_helper", XSYM_TV)
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv", {"MA": "EMA"})
    assert {t["exit_time"]: t["exit_comment"] for t in overridden} == exits["ema"]


def test_switch_with_a_block_arm_is_refused():
    src = source("xa_switch_helper", XSYM_TV).replace(
        '"EMA" => ta.ema(r, 5)', '"EMA" =>\n            e = ta.ema(r, 5)\n            e')
    with pytest.raises(CompileError) as err:
        transpile(src)
    assert [d.message for d in err.value.diagnostics] == [
        "request.security helper switch arms must each be one expression"]

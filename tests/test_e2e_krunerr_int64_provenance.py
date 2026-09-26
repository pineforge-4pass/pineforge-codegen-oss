"""A Pine ``int`` holding an epoch keeps all 64 bits wherever it is copied.

The codegen stored a Pine ``int`` in a C++ ``int`` unless the slot's own
initializer or a reassignment was an epoch builtin (``time``, ``timestamp``,
...), so the width stopped at the first copy. ``int sel = switch k => E2`` over
``const int E2 = timestamp(...)``, ``alias = firstT`` and ``copy := firstT``
over ``firstT := time``, and a declared ``int`` parameter called with ``time``
all narrowed to 32 bits: 1743468300000 read -288422176, and ``time == sel``
never held. The oracle probes pf-probe-quant-roc-admission-e2/e4/e5 select
their event bar exactly that way (``int selectedEventTime = switch ...``) and
stopped at their own "expected exactly one selected UTC event" check; the
stateful-roc probes narrowed their saved event times the same way.

The width now travels with the value: a name is wide when a declaration or
reassignment gives it a wide value, a wide name read anywhere is a wide value,
and an integer parameter a written call feeds a wide value is wide, fixed
point. TradingView's tape of the probe (``fixtures/krunerr_tv``) is replayed
end to end: every entry id -- the switch, the ternary, the alias, the copy,
the parameter and the returned ``t + 1`` -- equals TradingView's Signal.

``str.tostring(chart.is_standard)`` rides along: the chart-type reads are
Pine bools, and TradingView spells them "true" (the env-facts tape) where the
codegen printed the numeric "1".
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env


FIXTURES = Path(__file__).parent / "fixtures" / "krunerr_tv"
# TradingView's range: 2025-04-01 00:00 UTC for two days (bar_index 3 is
# 00:45 UTC, the bar both probes place their entries on).
RANGE_MS = (1743465600000, 1743638400000)


def _entry_signals(tape: str) -> list[str]:
    with (FIXTURES / tape).open(encoding="utf-8-sig") as fh:
        return sorted(r["Signal"] for r in csv.DictReader(fh) if r["Type"].startswith("Entry"))


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("krunerr_int64")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            if RANGE_MS[0] <= int(line.split(",", 1)[0]) < RANGE_MS[1]:
                out.write(line)
    outcomes = execute_all(engine, feed, base, {
        "int64": Build((FIXTURES / "int64_provenance.pine").read_text()),
        "cast": Build((FIXTURES / "int64_cast.pine").read_text()),
        "env": Build((FIXTURES / "env_facts.pine").read_text()),
    })
    return engine, feed, base, outcomes


def test_epoch_ints_read_like_the_tape(runs):
    engine, feed, base, outcomes = runs
    ok(outcomes, "int64")
    tape = _entry_signals("int64_provenance_tv_trades.csv")
    assert tape == sorted([
        "copy=1743465600000|alias=1743465600000",
        "f=1743468300000|g=1743465600000|p=1743468300001",
        "sel=1743468300000|tern=1743468300000|hit=true",
    ])
    engine_ids = sorted(t["entry_id"] for t in closed_trades(engine, base / "int64", feed))
    assert engine_ids == tape
    print("int64 provenance:", len(tape), "entry ids equal TradingView's Signals")


def test_int_cast_of_an_epoch_keeps_it(runs):
    # int(time), int(time_close), int(timestamp(...)) and int(t) over an int
    # parameter fed time: Pine int holds the epoch, where the cast used to be
    # a 32-bit (int) narrowing.
    engine, feed, base, outcomes = runs
    ok(outcomes, "cast")
    tape = _entry_signals("int64_cast_tv_trades.csv")
    assert tape == sorted([
        "it=1743468300000|first=1743466500000",
        "ts=1743468300000|fid=1743468300000",
    ])
    engine_ids = sorted(t["entry_id"] for t in closed_trades(engine, base / "cast", feed))
    assert engine_ids == tape


def test_chart_type_reads_spell_true(runs):
    engine, feed, base, outcomes = runs
    ok(outcomes, "env")
    # The other two entries spell the lane's syminfo (the harness runs the
    # engine defaults); the chart-type entry depends on nothing else.
    tape = [s for s in _entry_signals("env_facts_tv_trades.csv") if s.startswith("tf=")]
    assert tape == ["tf=15|m=15|min=true|d=false|std=true"]
    engine_ids = [t["entry_id"] for t in closed_trades(engine, base / "env", feed)
                  if t["entry_id"].startswith("tf=")]
    assert engine_ids == tape


def test_every_wide_slot_is_declared_int64():
    cpp = transpile((FIXTURES / "int64_provenance.pine").read_text())
    for declaration in (
        "int64_t sel = 0;", "int64_t tern = 0;", "int64_t alias = 0;",
        "int64_t copyT;", "std::string f_t(int64_t t)", "int64_t f_plus(int64_t t)",
    ):
        assert declaration in cpp, declaration


@pytest.mark.parametrize("source, narrow", [
    # A counter that never meets an epoch keeps its C++ int.
    ("var int cnt = 0\ncnt += 1\nplot(cnt)", "int cnt;"),
    # So does a declared int parameter no call feeds an epoch.
    ("f(int k) => k * 2\nplot(f(bar_index))", "int f(int k)"),
])
def test_narrow_ints_stay_int(source, narrow):
    cpp = transpile('//@version=6\nstrategy("krunerr narrow ints")\n' + source)
    assert narrow in cpp

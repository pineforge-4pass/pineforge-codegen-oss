"""``varip`` keeps its value like ``var``, outside the calc_on_order_fills rollback.

A historical bar executes the script once, so ``varip`` and ``var`` agree
there, except that a calc_on_order_fills recalculation rolls ``var`` back to
the bar's committed state and leaves ``varip`` alone. TradingView's own tapes
(``fixtures/popfix_tv``) prove it: each probe counts one ``var`` and one
``varip`` per execution and spells ``d = varip - var`` into its exit comment.
With calc_on_order_fills off every exit reads ``X0``; with it on, ``d`` grows
by one per fill-triggered recalculation.

Codegen lowers ``varip`` as ``var`` and leaves its storage (and first-run
latch) out of the rollback checkpoint. Each tape is replayed end to end --
``transpile_json``, the built runtime, the engine runner's own trade report
(its CSV carries no comments) -- on the corpus's ETH 15m chart feed. The
``var`` twin of the calc_on_order_fills probe, the rolled-back lowering, reads
``X0`` on every exit and misses the tape.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "popfix_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000),
                round(float(row["Price USDT"]), 2),
                row["Signal"],
            )
    return list(trades.values())


def _replay(tape: list[dict], engine: list[dict]) -> tuple[int, list[str]]:
    """``(tape trades whose engine twin agrees, first disagreements)``: the
    same entry bar and price, exit bar and price, and exit comment."""
    by_entry = {t["entry_time"]: t for t in engine}
    agree, misses = 0, []
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (
            twin["exit_time"], round(twin["exit_price"], 2), twin["exit_comment"],
            round(twin["entry_price"], 2))
        want = (*trade["exit"], trade["entry"][1])
        if got == want:
            agree += 1
        elif len(misses) < 3:
            misses.append(f"entry {trade['entry']}: tape {want}, engine {got}")
    return agree, misses


def _var_twin(source: str) -> str:
    return source.replace("varip int vpCount", "var int vpCount")


@pytest.fixture(scope="session")
def varip_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_varip")
    feed = derive_chart_feed(engine, base / "chart.csv")
    on = (FIXTURES / "varip_coof_on.pine").read_text()
    builds = {
        "on": Build(on),
        "off": Build((FIXTURES / "varip_coof_off.pine").read_text()),
        "var_twin": Build(_var_twin(on)),
    }
    runs = execute_all(engine, feed, base, builds)
    return {key: closed_trades(engine, base / key, feed)
            for key in builds if ok(runs, key)}


def test_varip_is_not_rolled_back_by_calc_on_order_fills(varip_runs):
    tape = _tape("varip_coof_on")
    agree, misses = _replay(tape, varip_runs["on"])
    assert agree == len(tape) == 174, misses
    assert tape[-1]["exit"][2] == "X346"
    print(f"varip calc_on_order_fills: {agree}/{len(tape)} TradingView trades "
          f"and exit comments, d up to {tape[-1]['exit'][2]}")


def test_varip_equals_var_without_calc_on_order_fills(varip_runs):
    tape = _tape("varip_coof_off")
    assert {t["exit"][2] for t in tape} == {"X0"}
    agree, misses = _replay(tape, varip_runs["off"])
    assert agree == len(tape) == 174, misses


def test_the_rolled_back_lowering_misses_the_tape(varip_runs):
    tape = _tape("varip_coof_on")
    comments = {t["exit_comment"] for t in varip_runs["var_twin"]}
    assert comments == {"X0"}
    agree, _ = _replay(tape, varip_runs["var_twin"])
    assert agree < len(tape) // 10


_INVENTORY_PROBE = """//@version=6
strategy("varip inventory", calc_on_order_fills = true)
var int vCount = 0
varip int vpCount = 0
bump() =>
    varip int calls = 0
    var int varCalls = 0
    calls += 1
    varCalls += 1
    calls - varCalls
vCount += 1
vpCount += 1
if close > open
    varip int inBlock = 0
    inBlock += close > close[1] ? 1 : 0
d = bump()
if d >= 0 and vpCount > vCount
    strategy.entry("L", strategy.long)
"""


def test_varip_storage_and_latch_stay_out_of_the_checkpoint():
    cpp = transpile(_INVENTORY_PROBE)
    state = cpp.split("struct _PFScriptState {", 1)[1].split("};", 1)[0]
    for member in ("vCount", "varCalls", "_pf_var_init_varCalls", "_var_initialized"):
        assert f"GeneratedStrategy::{member})" in state, member
    for member in ("vpCount", "calls", "inBlock", "_pf_var_init_calls"):
        assert f"GeneratedStrategy::{member})" not in state, member
        assert f" {member} = " in cpp or f" {member}{{" in cpp or f" {member};" in cpp

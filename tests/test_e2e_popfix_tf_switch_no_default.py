"""A ``request.security`` timeframe ``switch`` with no default arm.

``tf = switch choice`` with a string arm per choice and no ``=>`` default was
refused ("request.security timeframe switch requires a default arm";
awab-hassan-zone-flow-s-r-strategy, 7 rows), where TradingView runs it. When no
arm matches, the switch yields ``na``, and TradingView's own tape
(``fixtures/popfix_tv/tf_switch_no_arm``: the selector matches no arm) shows
``request.security`` then reads the chart's timeframe: on all 41 trades the
requested close equals the chart close and not the hour's. The registration
time timeframe now falls back to the chart's (``script_tf_``,
``timeframe.period``'s spelling) when no arm matches.

The tape is replayed end to end and each exit comment -- the requested
close, the chart close and the hour close -- equals TradingView's Signal. A
matching arm (an override picking "4H") runs like the same selection spelled
as a ternary: identical traces and trades.

Two neighbours stay refused, with TradingView's tapes saying why:

* an arm that yields ``na`` (``tf_switch_na_arm``: TradingView books the
  no-arm tape byte for byte) -- the script's own switch would store the
  ``na`` in the timeframe string as a number; before, a ``request.security``
  switch with an ``na`` arm was refused or failed the C++ compile;
* ``request.security_lower_tf`` with no default arm (``lowertf_no_arm``:
  TradingView returns one intrabar per chart bar, the chart bar) -- the
  engine's lower-timeframe request needs a strictly finer timeframe.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, derive_chart_feed,
    execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "popfix_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone

HEAD = '//@version=6\nstrategy("popfix tf switch", overlay = true)\n'
TAIL = """
reqClose = request.security(syminfo.tickerid, tf, close)
if ta.crossover(ta.ema(reqClose, 5), ta.ema(reqClose, 20))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(reqClose, 5), ta.ema(reqClose, 20))
    strategy.close("L")
// @pf-trace req=reqClose
"""
SHAPE = HEAD + """
pick = input.string("none", "Pick")
tf = switch pick
    "1H" => "60"
    "4H" => "240"
""" + TAIL
REFERENCE = HEAD + """
pick = input.string("none", "Pick")
tf = pick == "1H" ? "60" : pick == "4H" ? "240" : timeframe.period
""" + TAIL


def _tape(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_unmatched_switch_reads_the_chart_timeframe_like_the_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_tf_switch_tape")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    # The tape covers 2025-04-01..04-03 (Asia/Taipei): three days of 15m bars
    # from 2025-03-31 16:00 UTC, plus the hour before for the 60m request.
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            if 1743433200000 <= int(line.split(",", 1)[0]) < 1743696000000:
                out.write(line)
    runs = execute_all(engine, feed, base, {
        "probe": Build((FIXTURES / "tf_switch_no_arm.pine").read_text())})
    ok(runs, "probe")
    engine_exits = {t["exit_time"]: t["exit_comment"]
                    for t in closed_trades(engine, base / "probe", feed)}
    rows = _tape("tf_switch_no_arm_tv_trades.csv")
    assert {r["Signal"] for r in rows if r["Type"].startswith("Entry")} == {
        "tf=na chart=true hour=false"}
    tape_exits = {
        int(dt.datetime.strptime(r["Date and time"], "%Y-%m-%d %H:%M")
            .replace(tzinfo=TAPE_TZ).timestamp() * 1000): r["Signal"]
        for r in rows if r["Type"].startswith("Exit")
    }
    assert len(tape_exits) == 41
    assert {ms: engine_exits.get(ms) for ms in tape_exits} == tape_exits
    print(f"tf switch, no arm matched: {len(tape_exits)} exit comments equal TradingView's")


@pytest.fixture(scope="module")
def pair_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_tf_switch_pair")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, overrides={"Pick": "4H"}, trace=True),
        "reference": Build(REFERENCE, overrides={"Pick": "4H"}, trace=True),
    })


def test_switch_without_default_runs_like_its_ternary(pair_runs):
    print("tf switch:", assert_same_runs(ok(pair_runs, "shape"), ok(pair_runs, "reference")))


@pytest.mark.parametrize("probe, tape, entry_signal, refusal", [
    ("tf_switch_na_arm", "tf_switch_no_arm_tv_trades.csv",
     "tf=na chart=true hour=false", "request.security timeframe switch arm is na"),
    ("lowertf_no_arm", "lowertf_no_arm_tv_trades.csv", "n=1 chart=true",
     "request.security_lower_tf timeframe switch requires a default arm"),
])
def test_what_the_engine_cannot_run_stays_refused(probe, tape, entry_signal, refusal):
    rows = _tape(tape)  # TradingView runs the probe: 41 trades, each entry this Signal
    assert len({r["Trade number"] for r in rows}) == 41
    assert {r["Signal"] for r in rows if r["Type"].startswith("Entry")} == {entry_signal}
    with pytest.raises(CompileError, match=refusal):
        transpile((FIXTURES / f"{probe}.pine").read_text())


def test_an_na_arm_of_a_lower_timeframe_switch_is_refused_as_such():
    # An na lower timeframe is the chart's too (the lowertf_no_arm tape), which
    # the engine's lower-timeframe request rejects: the refusal says so.
    source = (FIXTURES / "lowertf_no_arm.pine").read_text().replace(
        '    "5" => "5"\n', '    "5" => "5"\n    "none" => na\n    => "1"\n')
    assert '"none" => na' in source
    with pytest.raises(CompileError, match="request.security_lower_tf timeframe switch arm is na"):
        transpile(source)

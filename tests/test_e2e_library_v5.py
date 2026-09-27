"""A v5 library's functions run by v5's rules inside a v6 script.

``fixtures/library_scripts/v5rules_import.pine`` is a v6 script importing the
synthetic ``pftest/V5Rules/1`` (``//@version=5``). Its global code is that of
``fixtures/xsym_lib_tv/xc_v5_lib.pine``, a v5 strategy holding the library's
functions as its own: an unpublished library cannot run on TradingView, so
that strategy's tapes are TradingView's values of the library's code. Each
exit Signal spells what v5 reads on that bar: int division by qualifiers,
strict ``and``/``or`` beside a lazy ``?:``, a ``for`` end fixed before the
first iteration, v5's color constants, numbers as conditions, a bool na, and
``timeframe.period``, which only a daily chart tells apart ("D", where v6
spells "1D"). The import build goes through the verifier's path: the glue's
``transpile_json`` reads the library from the environment through the
script's own requests manifest (``tests/_pine_libraries.py``).

On BINANCE:ETHUSDT.P 15 the build reproduces all 288 exit Signals of the
tape, on 1D all 45 (the corpus feed cut into UTC days). The same library
relabeled ``//@version=6`` misses every 15-minute exit instant, so the tape
tells the two versions apart. A negative array index stops a v5 library's
run where v6 counts from the array's end: TradingView stops ``xc_v5_negindex``
(the same read in a v5 strategy) on bar 10 with RE10045 "Index -1 is out of
bounds, array size is 3".
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env
from tests._pine_libraries import Layout, library_text
from tests._security_tapes import END_MS, START_MS, TAPE_TZ

SCRIPTS = Path(__file__).parent / "fixtures" / "library_scripts"
FIXTURES = Path(__file__).parent / "fixtures" / "xsym_lib_tv"
DAY_MS = 86_400_000
# The daily tape's last exit is on 2025-05-01 (UTC).
DAILY_END_MS = 1746144000000

NEGINDEX = """//@version=6
// SPDX-License-Identifier: Apache-2.0
strategy("xc v5 negindex lib", overlay=true, initial_capital=1000000, default_qty_type=strategy.fixed, default_qty_value=1)
import pftest/V5Rules/1 as R
a = array.from(1, 2, 3)
v = bar_index == 10 ? R.at(a, -1) : 0
if bar_index % 2 == 0
    strategy.entry("A", strategy.long)
else
    strategy.close("A", comment=str.tostring(v))
"""


def tape_signals(name: str) -> dict[int, list[str]]:
    """Exit instant (UTC ms) -> the sorted Signals of the trades exiting then.
    A position still open at the export's end has an empty Signal."""
    signals: dict[int, list[str]] = {}
    with (FIXTURES / name).open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["Type"].startswith("Exit") and row["Signal"]:
                ms = int(dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
                         .replace(tzinfo=TAPE_TZ).timestamp() * 1000)
                signals.setdefault(ms, []).append(row["Signal"])
    return {ms: sorted(v) for ms, v in signals.items()}


def engine_signals(trades: list[dict]) -> dict[int, list[str]]:
    signals: dict[int, list[str]] = {}
    for trade in trades:
        signals.setdefault(trade["exit_time"], []).append(trade["exit_comment"])
    return {ms: sorted(v) for ms, v in signals.items()}


def missed(tape: dict[int, list[str]], engine: dict[int, list[str]]) -> list[str]:
    return [f"{dt.datetime.fromtimestamp(ms / 1000, TAPE_TZ):%m-%d %H:%M} "
            f"tv {want!r} engine {engine.get(ms)!r}"
            for ms, want in sorted(tape.items()) if engine.get(ms) != want]


def cut_feeds(engine: Path, base: Path) -> tuple[Path, Path]:
    """The corpus 15m chart feed over the 15-minute tape's range, and the
    same feed folded into UTC days over the daily tape's."""
    full = derive_chart_feed(engine, base / "full_chart.csv")
    feed15, feed1d = base / "tape_chart.csv", base / "tape_daily.csv"
    days: dict[int, list[float]] = {}
    with full.open() as inp, feed15.open("w") as out15:
        header = next(inp)
        out15.write(header)
        for line in inp:
            ms = int(line.split(",", 1)[0])
            if START_MS <= ms < END_MS:
                out15.write(line)
            if START_MS <= ms < DAILY_END_MS:
                _, op, hi, lo, cl, vol = map(float, line.split(","))
                day = days.setdefault(ms - ms % DAY_MS, [op, hi, lo, cl, 0.0])
                day[1], day[2], day[3] = max(day[1], hi), min(day[2], lo), cl
                day[4] += vol
    with feed1d.open("w") as out1d:
        out1d.write(header)
        for ms, bar in sorted(days.items()):
            out1d.write(f"{ms}," + ",".join(map(repr, bar)) + "\n")
    return feed15, feed1d


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_library_v5")
    script = (SCRIPTS / "v5rules_import.pine").read_text(encoding="utf-8")
    env = Layout(base / "env").pin_all("v5rules-import", script, "pftest/V5Rules/1")
    relabeled = Layout(base / "env-v6")
    as_v6 = library_text("pftest/V5Rules/1").replace("//@version=5", "//@version=6")
    relabeled.add_probe("v5rules-import", script.encode("utf-8"), {
        "pftest/V5Rules/1": relabeled.add_library("pftest/V5Rules/1", as_v6.encode("utf-8"))})
    negindex = Layout(base / "env-negindex").pin_all(
        "v5-negindex", NEGINDEX, "pftest/V5Rules/1")
    feed15, feed1d = cut_feeds(engine, base)
    builds = {"v5": Build(script, env=tuple(env.items())),
              "as_v6": Build(script, env=tuple(relabeled.env.items())),
              "negindex": Build(NEGINDEX, env=tuple(negindex.items()))}
    return engine, base, feed15, feed1d, execute_all(engine, feed15, base, builds)


def test_a_v5_library_replays_the_15_minute_tape(runs):
    engine, base, feed15, _feed1d, outcomes = runs
    ok(outcomes, "v5")
    tape = tape_signals("xc_v5_lib_tv_trades.csv")
    assert sum(map(len, tape.values())) == 288
    got = engine_signals(closed_trades(engine, base / "v5", feed15))
    bad = missed(tape, got)
    assert not bad, "\n".join(bad[:10])


def test_a_v5_library_replays_the_daily_tape(runs):
    engine, base, _feed15, feed1d, outcomes = runs
    ok(outcomes, "v5")
    tape = tape_signals("xc_v5_lib_eth1d_tv_trades.csv")
    assert sum(map(len, tape.values())) == 45
    got = engine_signals(closed_trades(engine, base / "v5", feed1d))
    bad = missed(tape, got)
    assert not bad, "\n".join(bad[:10])
    # timeframe.period on a daily chart: v5 spells it "D".
    assert all(s.endswith(" f D") for v in tape.values() for s in v if s.startswith("k "))


def test_the_library_relabeled_v6_misses_the_tape(runs):
    engine, base, feed15, _feed1d, outcomes = runs
    ok(outcomes, "as_v6")
    tape = tape_signals("xc_v5_lib_tv_trades.csv")
    got = engine_signals(closed_trades(engine, base / "as_v6", feed15))
    assert len(missed(tape, got)) == len(tape)


def test_a_negative_index_stops_a_v5_librarys_run(runs):
    _engine, _base, _feed15, _feed1d, outcomes = runs
    error = outcomes["negindex"].error
    assert error is not None and "Index -1 is out of bounds. Array size is 3" in error, error

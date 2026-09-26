"""``alert.freq_*`` are TradingView's const strings, usable as values.

TradingView's own tape (``fixtures/popfix_tv/alert_freq_values``) spells them:
every entry Signal reads ``all|once_per_bar|once_per_bar_close`` and each exit
Signal the constant the bar selected plus whether it equals
``alert.freq_all``. The probe used to be refused ("only valid as the freq
argument of alert(...)"); it is replayed here end to end and every exit
comment of the engine's run equals the tape's Signal. Any other ``alert.*``
member is still refused, as TradingView refuses it.
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
    Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "popfix_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone


def test_tape_spells_the_constants():
    with (FIXTURES / "alert_freq_values_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    entries = {r["Signal"] for r in rows if r["Type"].startswith("Entry")}
    exits = {r["Signal"] for r in rows if r["Type"].startswith("Exit")}
    assert entries == {"all|once_per_bar|once_per_bar_close"}
    assert exits == {"Pall|true", "Ponce_per_bar|false", "Ponce_per_bar_close|false"}


def test_alert_freq_values_match_the_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_alert_freq")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    # The tape covers 2025-04-01..04-03 (Asia/Taipei); trade on 2025-03-31
    # 16:00 UTC onward, three days of 15m bars.
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if 1743436800000 <= ts < 1743696000000:
                out.write(line)
    runs = execute_all(engine, feed, base, {
        "probe": Build((FIXTURES / "alert_freq_values.pine").read_text()),
    })
    ok(runs, "probe")
    engine_exits = {t["exit_time"]: t["exit_comment"]
                    for t in closed_trades(engine, base / "probe", feed)}
    with (FIXTURES / "alert_freq_values_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        tape_exits = {
            int(dt.datetime.strptime(r["Date and time"], "%Y-%m-%d %H:%M")
                .replace(tzinfo=TAPE_TZ).timestamp() * 1000): r["Signal"]
            for r in csv.DictReader(fh) if r["Type"].startswith("Exit")
        }
    assert len(tape_exits) == 48
    assert {ms: engine_exits.get(ms) for ms in tape_exits} == tape_exits
    print(f"alert.freq_*: {len(tape_exits)} exit comments equal TradingView's Signals")


def test_alert_freq_constants_lower_to_their_strings():
    cpp = transpile('//@version=6\nstrategy("T")\n'
                    'f = close > open ? alert.freq_all : alert.freq_once_per_bar_close\n'
                    'if f == alert.freq_all\n'
                    '    strategy.entry("L", strategy.long, comment = f + alert.freq_once_per_bar)\n')
    assert 'std::string("all")' in cpp
    assert 'std::string("once_per_bar")' in cpp
    assert 'std::string("once_per_bar_close")' in cpp
    assert "std::string f = " in cpp


def test_other_alert_members_are_still_refused():
    with pytest.raises(CompileError, match="alert.freq_hourly is not a Pine alert constant"):
        transpile('//@version=6\nstrategy("T")\nx = alert.freq_hourly\n')

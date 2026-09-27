"""Replay of the exit-comment tapes in ``fixtures/open_items_tv``.

Each synthetic probe there opens a trade every six hours and spells the
values under test in its exit comment; ``lab tv --no-note`` exported
TradingView's trades on BINANCE:ETHUSDT.P 15 from 2025-04-01. ``tape``
reads an export (its ``Date and time`` column is the exporting account's
chart timezone, Asia/Taipei), ``window_feed`` cuts the corpus ETH 15m feed
to TradingView's run window, so a series' history and a ``var``'s first bar
agree bar for bar, and ``exit_misses`` lists every tape trade whose engine
twin (the trade entered at the same time) exits at another time or with
another comment.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from tests._e2e import closed_trades, derive_chart_feed


FIXTURES = Path(__file__).parent / "fixtures" / "open_items_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
WINDOW_START_MS = int(dt.datetime(2025, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
WINDOW_END_MS = int(dt.datetime(2025, 4, 4, tzinfo=dt.timezone.utc).timestamp() * 1000)


def tape(name: str) -> list[dict]:
    """``{"entry": (ms, signal), "exit": (ms, signal)}`` per tape trade."""
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000), row["Signal"])
    return list(trades.values())


def window_feed(engine: Path, base: Path) -> Path:
    full = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "window.csv"
    with full.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if WINDOW_START_MS <= ts < WINDOW_END_MS:
                out.write(line)
    return feed


def exit_misses(engine: Path, workdir: Path, feed: Path, trades: list[dict]) -> list[str]:
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, workdir, feed)}
    misses = []
    for trade in trades:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (twin["exit_time"], twin["exit_comment"])
        if got != trade["exit"]:
            misses.append(f"tape {trade['exit']}, engine {got}")
    return misses


def exits_by_time(name: str) -> dict[int, str]:
    """Exit instant (UTC ms) -> Signal of every trade on a tape; for the
    request.security probes, replayed by ``tests._security_tapes.replay``."""
    return {trade["exit"][0]: trade["exit"][1] for trade in tape(name)}

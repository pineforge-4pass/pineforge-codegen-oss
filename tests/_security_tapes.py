"""Replays of the CG-SECURITY-2 TradingView tapes (``fixtures/security2_tv``).

Each probe closes its position with a comment spelling the values it read on
that bar, so a tape's exit Signals are TradingView's values. A replay runs
the probe end to end (``tests/_e2e.py``: the glue's ``transpile_json``, the
built runtime, the engine's runner) on the corpus BINANCE:ETHUSDT.P 15m feed
cut to the tapes' range, starting on the tapes' first chart bar, and pairs
every tape exit with the engine trade exiting at the same instant.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok


FIXTURES = Path(__file__).parent / "fixtures" / "security2_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
# The tapes' first chart bar (TradingView's requested and returned range
# starts at 2025-04-01 00:00 UTC) and an end past their last exit.
START_MS = 1743465600000
END_MS = 1744092000000


def source(name: str) -> str:
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


def tape_exits(name: str) -> dict[int, str]:
    """Exit instant (UTC ms) -> Signal of every trade on a probe's tape."""
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return {
        int(dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            .replace(tzinfo=TAPE_TZ).timestamp() * 1000): row["Signal"]
        for row in rows if row["Type"].startswith("Exit")
    }


def tape_feed(engine: Path, base: Path) -> Path:
    """The corpus 15m chart feed over the tapes' range."""
    full = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "tape_chart.csv"
    with full.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            if START_MS <= int(line.split(",", 1)[0]) < END_MS:
                out.write(line)
    return feed


def replay(engine: Path, base: Path, builds: dict[str, Build],
           params: dict[str, dict] | None = None) -> dict[str, dict[int, str]]:
    """Run each build on the tape feed: its exit comments by exit instant,
    under ``params[key]`` (input overrides by title) when given."""
    feed = tape_feed(engine, base)
    runs = execute_all(engine, feed, base, builds)
    exits: dict[str, dict[int, str]] = {}
    for key in builds:
        ok(runs, key)
        trades = closed_trades(engine, base / key, feed, (params or {}).get(key))
        exits[key] = {t["exit_time"]: t["exit_comment"] for t in trades}
    return exits


def mismatches(tape: dict[int, str], engine: dict[int, str]) -> list[str]:
    """Tape exits whose Signal the engine does not reproduce at that instant."""
    return [
        f"{dt.datetime.fromtimestamp(ms / 1000, TAPE_TZ):%m-%d %H:%M} "
        f"tv {signal!r} engine {engine.get(ms)!r}"
        for ms, signal in sorted(tape.items()) if engine.get(ms) != signal
    ]

"""Replays of the TAIL-E TradingView tapes (``fixtures/tail_e_tv``).

Each probe spells the values under test in its orders: on a flat bar as the
entry's name (or comment), on a bar holding the position as the close's
comment, so a tape's rows carry TradingView's values bar by bar. A replay
builds the probe through the glue's ``transpile_json`` and the built runtime,
runs it on the corpus BINANCE:ETHUSDT.P 15m feed cut to the tape's range (one
bar past its end, so the last order fills), and pairs every tape row with the
engine row of the same kind filled at the same instant: an entry by its order
id, a close by its comment.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from tests._e2e import build_strategy_library, closed_trades, derive_chart_feed, transpile_json


FIXTURES = Path(__file__).parent / "fixtures" / "tail_e_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
START_MS = 1743465600000  # 2025-04-01 00:00 UTC, every tape's first chart bar
DAY_MS = 86_400_000
BAR_MS = 900_000


def source(name: str) -> str:
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


def tape_rows(tape: str, by_comment: bool = False) -> dict[tuple[str, int], str]:
    """``(kind, fill instant UTC ms) -> Signal`` of every tape row, ``kind``
    "E" for an entry and "X" for a close."""
    rows = {}
    with (FIXTURES / f"{tape}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not row["Signal"]:
                continue  # the range-end close of a position left open
            ms = int(dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
                     .replace(tzinfo=TAPE_TZ).timestamp() * 1000)
            rows[("E" if row["Type"].startswith("Entry") else "X", ms)] = row["Signal"]
    return rows


def feed(engine: Path, base: Path, end_ms: int) -> Path:
    """The corpus 15m chart feed from the tapes' first bar to ``end_ms``."""
    full = base / "full_chart.csv"
    if not full.exists():
        derive_chart_feed(engine, full)
    out = base / f"chart_{end_ms}.csv"
    with full.open() as inp, out.open("w") as dst:
        dst.write(next(inp))
        for line in inp:
            if START_MS <= int(line.split(",", 1)[0]) < end_ms:
                dst.write(line)
    return out


def aux_1m_feed(engine: Path, base: Path, end_ms: int) -> Path:
    """The corpus 1m feed over the same range: the lower-timeframe feed."""
    src = engine / "corpus" / "data" / "ohlcv_ETH-USDT-USDT_1m.csv"
    out = base / f"aux_1m_{end_ms}.csv"
    with src.open() as inp, out.open("w") as dst:
        dst.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if ts >= end_ms:
                break
            if ts >= START_MS:
                dst.write(line)
    return out


def build(base: Path, key: str, pine: str) -> Path:
    workdir = base / key
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "strategy.pine").write_text(pine, encoding="utf-8")
    result = transpile_json(workdir / "strategy.pine")
    assert result.get("ok"), result.get("diagnostics")
    build_strategy_library(result["cpp"], workdir)
    return workdir


def engine_rows(engine: Path, workdir: Path, chart: Path, params: dict | None = None,
                **run_kwargs) -> tuple[dict[tuple[str, int], str], list[dict]]:
    """The run's rows keyed like ``tape_rows`` (an entry by its id, a close
    by its comment), and its closed trades."""
    trades = closed_trades(engine, workdir, chart, params, **run_kwargs)
    rows = {}
    for trade in trades:
        rows[("E", trade["entry_time"])] = trade["entry_id"]
        rows[("X", trade["exit_time"])] = trade["exit_comment"]
    return rows, trades


def mismatches(tape: dict, engine: dict, drop=None) -> list[str]:
    """Tape rows the engine does not reproduce at that instant (each value
    passed through ``drop`` first when given)."""
    keep = drop or (lambda value: value)
    out = []
    for key, signal in sorted(tape.items(), key=lambda item: (item[0][1], item[0][0])):
        got = engine.get(key)
        if got is None or keep(got) != keep(signal):
            when = dt.datetime.fromtimestamp(key[1] / 1000, dt.timezone.utc)
            out.append(f"{key[0]} {when:%m-%d %H:%M} tv {signal!r} engine {got!r}")
    return out

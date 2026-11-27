"""Replay the 66 deciding SW3 entry IDs from TradingView (#458)."""

from datetime import datetime, timezone
from pathlib import Path

from tests._e2e import (
    build_strategy_library, closed_trades, derive_chart_feed, skip_unless_e2e_env,
    transpile_json,
)


FIXTURE = Path(__file__).parent / "fixtures" / "issue_458"
START = int(datetime(2025, 11, 1, tzinfo=timezone.utc).timestamp()) * 1000
INTERVAL = 900_000


def test_bar_distance_offsets(tmp_path):
    engine = skip_unless_e2e_env()
    full_feed = derive_chart_feed(engine, tmp_path / "full_chart.csv")
    feed = tmp_path / "chart.csv"
    # Warm the chart without reaching the call. Bar 300 requests the final
    # close; include bar 301 for settlement.
    grid = [START + b * INTERVAL for b in range(-5, 302)]
    stamps = []
    with full_feed.open() as src, feed.open("w") as out:
        out.write(next(src))
        for line in src:
            stamp = int(line.partition(",")[0])
            if stamp > grid[-1]:
                break
            if stamp >= grid[0]:
                stamps.append(stamp)
                out.write(line)
    assert stamps == grid, f"NOT COMPARED: 15m chart grid; expected={grid}, native={stamps}"

    pinned = (FIXTURE / "tv-458sw3-ids.txt").read_text(encoding="ascii")
    expected = [line for line in pinned.splitlines() if not line.startswith("#")]
    deciding = [str(b) for b in range(9, 300) if b % 9 in (0, 1)]
    fixture_bars = [entry.partition("|")[0] for entry in expected]
    assert fixture_bars == deciding, (
        f"NOT COMPARED: deciding bars; expected={deciding}, fixture={fixture_bars}"
    )

    result = transpile_json(FIXTURE / "probe-458sw3.pine")
    assert result["ok"], result.get("diagnostics")
    build_strategy_library(result["cpp"], tmp_path)
    trades = closed_trades(engine, tmp_path, feed, syminfo_metadata={"mintick": 0.01})
    entries = [trade["entry_id"] for trade in trades]
    keys = sorted(entry.split("|", 2)[:2] for entry in entries)
    wanted = sorted([str(b), "x" if b % 9 in (0, 1) else "-"] for b in range(300))
    assert keys == wanted, f"NOT COMPARED: entry bars/markers; expected={wanted}, native={keys}"
    by_bar = {entry.partition("|")[0]: entry for entry in entries}
    differences = [
        f"TV={entry}  native={by_bar[b]}" for b, entry in zip(deciding, expected, strict=True)
        if by_bar[b] != entry
    ]
    assert not differences, (
        f"{len(differences)}/{len(expected)} deciding IDs differ (b|x|ls|lr|bs|br):\n"
        + "\n".join(differences)
    )

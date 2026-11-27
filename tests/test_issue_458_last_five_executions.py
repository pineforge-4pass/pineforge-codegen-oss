"""#458: all four conditional lengths and the three controls in the TradingView fixture."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests._compile import skip_if_no_engine_lib
from tests._e2e import build_strategy_library, closed_trades, engine_root, transpile_json


FIXTURE = Path(__file__).parent / "fixtures" / "issue_458"


def test_last_five_executions(tmp_path):
    skip_if_no_engine_lib()
    engine = engine_root()
    assert engine is not None
    if not (engine / "scripts" / "run_strategy.py").is_file():
        pytest.skip(f"engine checkout {engine} has no scripts/run_strategy.py")

    result = transpile_json(FIXTURE / "probe-458.pine")
    assert result["ok"], result.get("diagnostics")
    build_strategy_library(result["cpp"], tmp_path)

    # The two sources and the gate use time only. Supply the fixture's 96
    # chart bars and a positive tick. All seven orders fit at this price.
    start = int(datetime(2026, 11, 16, tzinfo=timezone.utc).timestamp()) * 1000
    feed = tmp_path / "chart.csv"
    with feed.open("w", encoding="ascii") as bars:
        bars.write("timestamp,open,high,low,close,volume\n")
        for j in range(96):
            bars.write(f"{start + j * 900_000},100,101,99,100,1000\n")
    trades = closed_trades(engine, tmp_path, feed, syminfo_metadata={"mintick": 0.01})
    actual = sorted(trade["entry_id"] for trade in trades)
    expected = (FIXTURE / "tv-458-ids.txt").read_text(encoding="ascii").splitlines()
    assert len(actual) == len(expected) == 70, (
        f"NOT COMPARED: expected 70 entries; fixture={len(expected)}, native={len(actual)}"
    )
    fixture_keys = [entry.rsplit("|", 2)[0] for entry in expected]
    native_keys = [entry.rsplit("|", 2)[0] for entry in actual]
    assert native_keys == fixture_keys, (
        f"NOT COMPARED: entry tags or offsets differ; fixture={fixture_keys}, native={native_keys}"
    )
    assert actual == expected

"""Exact-source native witness for issue #291 case 3, fixed in 08261a24."""

import pytest

from tests._compile import skip_if_no_engine_lib
from tests._e2e import Build, engine_root, execute, ok


SOURCE = """//@version=6
strategy('repro')
string tag = na
if bar_index % 3 == 0
    tag := 'x'
var int seen = 0
if not na(tag)
    seen += 1
// @pf-trace seen=seen
plot(seen)
"""
BARS = 1441


def test_non_var_string_na_reaches_481_like_cast_control(tmp_path):
    skip_if_no_engine_lib()
    engine = engine_root()
    if engine is None or not (engine / "scripts" / "run_strategy.py").is_file():
        pytest.skip("requires the paired engine's scripts/run_strategy.py")

    # Only bar_index drives the script. No price fixture is needed.
    feed = tmp_path / "chart.csv"
    start_ms = 1704067200000
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"{start_ms + bar * 3600000},100,101,99,100,1\n" for bar in range(BARS)
        ),
        encoding="utf-8",
    )
    control = SOURCE.replace("string tag = na", "string tag = string(na)", 1)
    runs = {
        name: execute(engine, feed, tmp_path / name, Build(source, trace=True))
        for name, source in (("cast-control", control), ("bare-na", SOURCE))
    }
    expected_bars = list(range(BARS))
    expected_counts = [bar // 3 + 1 for bar in expected_bars]
    for name in runs:
        records = [
            row for row in ok(runs, name).traces["default"] if row["name"] == "seen"
        ]
        assert [row["bar_index"] for row in records] == expected_bars, name
        counts = [row["value"] for row in records]
        assert counts[-1] == 481, f"{name}: seen on bar 1440 = {counts[-1]}"
        assert counts == expected_counts, name

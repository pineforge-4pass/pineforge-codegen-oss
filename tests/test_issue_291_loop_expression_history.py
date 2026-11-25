"""Native witness for the exact script in public issue #291, case 1."""

import math
import os

import pytest

from tests._compile import skip_if_no_engine_lib
from tests._e2e import Build, engine_root, execute


SOURCE = """//@version=6
strategy('repro')
mid = (close + open) / 2
float a = 0.0
float b = 0.0
for i = 1 to 3
    a += ((close + open) / 2)[i]
    b += mid[i]
diff = a - b
// @pf-trace diff=diff
plot(a)
"""


def test_reported_loop_expression_history_matches_named_midpoint(tmp_path):
    skip_if_no_engine_lib()
    engine = engine_root()
    if engine is None or not (engine / "scripts" / "run_strategy.py").is_file():
        unavailable = pytest.fail if os.environ.get("PINEFORGE_REQUIRE_NATIVE") == "1" else pytest.skip
        unavailable("NOT COMPARED: the engine's scripts/run_strategy.py is unavailable")

    opens = (10, 23, 16, 34, 22, 39, 28, 45)
    closes = [price + 2 * (i + 1) for i, price in enumerate(opens)]
    midpoints = [(o + c) / 2 for o, c in zip(opens, closes)]
    feed = tmp_path / "chart.csv"
    rows = ["timestamp,open,high,low,close,volume"]
    for i, (o, c) in enumerate(zip(opens, closes)):
        timestamp = 1743465600000 + i * 3600000
        rows.append(f"{timestamp},{o},{c + 1},{o - 1},{c},1000")
    feed.write_text("\n".join(rows) + "\n", encoding="utf-8")

    # The extra trace only observes the existing control sum.
    build = Build(SOURCE + "// @pf-trace named_sum=b\n", trace=True)
    outcome = execute(engine, feed, tmp_path / "repro", build)
    assert outcome.error is None, f"NOT COMPARED: {outcome.error}"
    records = outcome.traces.get("default", [])
    bars = list(range(3, len(midpoints)))

    def mature_values(name):
        samples = [r for r in records if r["name"] == name and r["bar_index"] >= 3]
        assert [r["bar_index"] for r in samples] == bars, f"NOT COMPARED: incomplete {name} trace"
        values = [r["value"] for r in samples]
        assert all(type(value) in (int, float) and math.isfinite(value) for value in values), (
            f"NOT COMPARED: undefined or nonnumeric {name} trace: {values}"
        )
        return values

    expected = [sum(midpoints[i - 3:i]) for i in bars]
    assert mature_values("named_sum") == expected, "named midpoint sum differs from the feed"
    diff = mature_values("diff")
    wrong = [(bar, value) for bar, value in zip(bars, diff) if value != 0.0]
    assert not wrong, f"expression history diff is nonzero (bar, diff): {wrong}"

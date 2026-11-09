"""Native loop entry quantities use the recorded TradingView counts."""

import csv
import json
from pathlib import Path

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env


FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers"
TAPES = json.loads(
    (FIXTURES / "c2_end_rule_tapes.json").read_text(encoding="utf-8")
)["tapes"]
TV = json.loads((FIXTURES / "tradingview_quantities.json").read_text(encoding="utf-8"))
SOURCE = """//@version=6
strategy("{title}", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for x = {start} to {end} by {step}
    acc += 1
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("for_end_rule")
    feed = chart_feed_head(engine, base, 1443)
    builds = {
        tape["name"]: Build(SOURCE.format(
            title=tape["name"], start=tape["start"],
            end=tape["end"], step=tape["step"],
        ))
        for tape in TAPES
    }
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("tape", TAPES, ids=lambda tape: tape["name"])
def test_float_loop_end_matches_tradingview(runs, tape):
    outcome = ok(runs, tape["name"])
    rows = csv.DictReader(outcome.trades["default"].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    assert len(entries) == TV["entries_per_probe"]
    wrong = [
        (row["Trade #"], row["Qty"])
        for row in entries if float(row["Qty"]) != tape["tv_count"]
    ]
    assert not wrong, (
        f"{tape['name']}: expected every entry quantity to be "
        f"{tape['tv_count']}; got {wrong[:10]}"
    )

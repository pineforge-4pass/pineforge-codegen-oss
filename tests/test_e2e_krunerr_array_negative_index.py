"""Pine v6 negative array indices, replayed against TradingView.

Pine v6 counts a negative index from the end for ``array.get``, ``array.set``,
``array.insert`` and ``array.remove`` (and their method forms): ``-1`` is the
last element, and an insertion at ``-k`` lands before the k-th element from
the end. ``array.slice``, ``array.fill`` and ``array.percentrank`` take no
negative index, and an index outside ``[-size, size)`` stops the script.
TradingView's tape of ``fixtures/krunerr_tv/array_negative_index.pine`` pins the
values (int, string and float arrays; function and method forms), and its
verdicts file pins every refusal: "In 'array.<fn>()' function. Index <i> is
out of bounds, array size is <n>." The emitted checked-index lowering
(``tables._checked_array_index_prelude``) already agreed; these tests hold it
to TradingView's own record.

TradingView drops a call whose result is never used (``array.get(a, -4)`` as a
bare statement does not stop its script), where PineForge evaluates it; the
verdicts file records only probes that use the result.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env


FIXTURES = Path(__file__).parent / "fixtures" / "krunerr_tv"
RANGE_MS = (1743465600000, 1743638400000)  # TradingView's 2025-04-01..04-03
VERDICTS = json.loads((FIXTURES / "array_negative_index_verdicts.json").read_text())["probes"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("krunerr_array_negative_index")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            if RANGE_MS[0] <= int(line.split(",", 1)[0]) < RANGE_MS[1]:
                out.write(line)
    builds = {"values": Build((FIXTURES / "array_negative_index.pine").read_text())}
    for probe in VERDICTS:
        builds[probe["probe"]] = Build(probe["source"])
    return engine, feed, base, execute_all(engine, feed, base, builds)


def test_negative_indices_read_like_the_tape(runs):
    engine, feed, base, outcomes = runs
    ok(outcomes, "values")
    with (FIXTURES / "array_negative_index_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        tape = sorted(r["Signal"] for r in csv.DictReader(fh) if r["Type"].startswith("Entry"))
    assert tape == sorted([
        "g1=50|g5=10",
        "s1=10,20,30,44,45,50",
        "r=44|s2=10,20,30,45,50",
        "s3=5,10,20,30,45,50",
        "s4=yx|s5=3.5,1.5",
        "s6=99,50,99,5,10,20,30,45",
    ])
    engine_ids = sorted(t["entry_id"] for t in closed_trades(engine, base / "values", feed))
    assert engine_ids == tape


@pytest.mark.parametrize("probe", [p["probe"] for p in VERDICTS])
def test_each_verdict_is_tradingviews(runs, probe):
    engine, feed, base, outcomes = runs
    record = next(p for p in VERDICTS if p["probe"] == probe)["tradingview"]
    outcome = outcomes[probe]
    if record["verdict"] == "ran":
        ok(outcomes, probe)
        engine_ids = sorted(t["entry_id"] for t in closed_trades(engine, base / probe, feed))
        assert engine_ids == record["entry_signals"]
    else:
        ctx = record["ctx"]
        assert outcome.error is not None, f"{probe} ran; TradingView stopped it"
        assert (f"Index {ctx['index']} is out of bounds. Array size is {ctx['size']}"
                in outcome.error), outcome.error

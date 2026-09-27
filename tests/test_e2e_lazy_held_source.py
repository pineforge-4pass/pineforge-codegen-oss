"""A lazily executed ``ta.change`` / ``ta.mom`` / ``ta.roc`` reads its own held source.

Below a lazy edge the call's ``source`` history is written only on bars where
the call executes and held on the bars it skips, so ``source[length]`` is the
source at the call's latest execution at or before ``length`` bars ago, also
when the previous execution is closer than ``length`` bars. TradingView's tape
``fixtures/lazy_held_source/w8a-lazy-mom-held`` (NASDAQ:AAPL 15, see its
README) runs ``ta.mom(close, 3)`` on three bars of every four: on each
``bar_index % 4 == 2`` bar ``M`` is ``close - close[4]`` and the top-level
control ``C`` is ``close - close[3]``, on all 91 cycles. The codegen used to
read the chart's ``close[length]`` there (``_pf_lazy_src_chart_N``), which
makes ``M`` equal ``C``.

The AAPL feed does not ship here: the tape's own structure is checked, and the
rule is pinned end to end (``tests/_e2e.py``: ``transpile_json``, the built
runtime, ``run_strategy.py`` over the corpus 15m feed, ``@pf-trace`` values)
bar by bar against the spelled-out reads. The pre-lane build (``LEGACY``)
misses exactly the bars whose ``bar - 3`` was skipped.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pytest

from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, per_bar_mismatches,
    reference_codegen, same, skip_unless_e2e_env,
)


TAPE = Path(__file__).parent / "fixtures" / "lazy_held_source" / "w8a-lazy-mom-held"
# cg/tvdefaults, the lane's base: the eager chart read between close executions.
LEGACY = "d7e095fd7733303f8102c6f27d4c9ab12d631b8a"

HEADER = '//@version=6\nstrategy("e2e-w9-lazy-held", overlay=true)\n'
TRACES = "// @pf-trace m=m\n// @pf-trace ch=ch\n// @pf-trace r=r\n// @pf-trace c=c\n"
# The calls run on three bars of every four, as in the tape.
SUBJECT = (HEADER
           + "gate = bar_index % 4 != 3\n"
           + "m = gate ? ta.mom(close, 3) : na\n"
           + "ch = gate ? ta.change(close, 3) : na\n"
           + "r = gate ? ta.roc(close, 3) : na\n"
           + "c = ta.mom(close, 3)\n"
           + TRACES)
# The held source, spelled out: the execution at bar - 4 when bar - 3 was
# skipped (bar_index % 4 == 2), else the execution at bar - 3.
REFERENCE = (HEADER
             + "prev = bar_index % 4 == 2 ? close[4] : close[3]\n"
             + "held = bar_index % 4 == 3 ? na : close - prev\n"
             + "m = held\n"
             + "ch = held\n"
             + "r = bar_index % 4 == 3 ? na : (close - prev) / prev * 100\n"
             + "c = close - close[3]\n"
             + TRACES)


def test_tradingview_tape_separates_the_held_and_eager_reads():
    metrics = json.loads((TAPE / "metrics.json").read_text())
    with (TAPE / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        entries = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    cycles: dict[str, dict[str, float]] = defaultdict(dict)
    for row in entries:
        cycles[row["Date and time"]][row["Signal"]] = float(row["Size (qty)"])
    assert metrics["trades"] == len(entries) == 182
    assert len(cycles) == 91
    assert all(set(cycle) == {"M", "C"} for cycle in cycles.values())
    # quantity = 1 + round((value + 50) * 10000): M != C on every cycle.
    assert [t for t, cycle in cycles.items() if cycle["M"] == cycle["C"]] == []


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("lazy_held_source")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds = {"subject": Build(SUBJECT, trace=True),
              "reference": Build(REFERENCE, trace=True)}
    legacy = reference_codegen(LEGACY)
    if legacy is not None:
        builds["legacy"] = Build(SUBJECT, trace=True, codegen=legacy)
    return execute_all(engine_root, feed, base, builds)


def test_lazy_calls_read_their_held_source_bar_by_bar(outcomes):
    subject = ok(outcomes, "subject").traces["default"]
    reference = ok(outcomes, "reference").traces["default"]
    compared, mismatched, first = per_bar_mismatches(
        subject, reference, ("lazy call", "spelled out"))
    assert compared > 0
    assert mismatched == 0, first
    # The feed discriminates the two reads: M differs from C on those bars.
    by_bar = defaultdict(dict)
    for rec in subject:
        by_bar[rec["bar_index"]][rec["name"]] = rec["value"]
    distinct = [bar for bar, v in by_bar.items()
                if bar % 4 == 2 and not same(v["m"], v["c"])]
    assert len(distinct) > 1000


def test_the_pre_lane_build_read_the_charts_source_between_executions(outcomes):
    if "legacy" not in outcomes:
        pytest.skip(f"codegen {LEGACY} is not in this checkout's history")
    legacy = ok(outcomes, "legacy").traces["default"]
    reference = ok(outcomes, "reference").traces["default"]
    expected = {(rec["name"], rec["bar_index"]): rec["value"] for rec in reference}
    missed = {rec["bar_index"] % 4 for rec in legacy
              if not same(rec["value"], expected[(rec["name"], rec["bar_index"])])}
    assert missed == {2}

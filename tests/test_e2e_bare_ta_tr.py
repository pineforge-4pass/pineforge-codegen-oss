"""Bare ta.tr has the first-bar na of ta.tr(false), as TradingView trades prove.

Pine v6 reference (#var_ta.tr): "True range, equivalent to
`ta.tr(handle_na = false)`. It is calculated as
`math.max(high - low, math.abs(high - close[1]), math.abs(low - close[1]))`."
The #fun_ta.tr handle_na entry: "If true, the function returns the bar's
`high - low` value. If false, it returns na." TradingView's c6-ta-tr-bar-zero
probe exported 2025-04-01 08:00 as an Entry long with Signal "bare-na".
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, per_bar_mismatches,
    skip_unless_e2e_env, summary,
)


def _source(tr_expression: str) -> str:
    return ('//@version=6\nstrategy("c6-bare-tr", overlay=true)\n'
            f'tr = {tr_expression}\n'
            'tr_true = ta.tr(true)\n'
            'atr = ta.atr(3)\n'
            'if bar_index == 0 and na(tr)\n'
            '    strategy.entry("L", strategy.long)\n'
            'if bar_index == 2\n'
            '    strategy.close("L")\n'
            '// @pf-trace tr=tr\n'
            '// @pf-trace tr_true=tr_true\n'
            '// @pf-trace atr=atr\n')


@pytest.fixture(scope="session")
def tr_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("c6_bare_tr")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        for _, line in zip(range(65), inp):
            out.write(line)
    runs = execute_all(engine, feed, base, {
        "bare": Build(_source("ta.tr"), trace=True),
        "false": Build(_source("ta.tr(false)"), trace=True),
    })
    return ok(runs, "bare"), ok(runs, "false")


def test_bare_true_range_has_the_false_handle_na_seed(tr_runs):
    bare, reference = tr_runs
    expected = reference.traces["default"]
    actual = bare.traces["default"]
    first_tr = next(r["value"] for r in expected if r["name"] == "tr")
    first_true = next(r["value"] for r in expected if r["name"] == "tr_true")
    tape = Path(__file__).parent / "fixtures/c6_tv_evidence/ta_tr_bar_zero_tv_trades.csv"
    with tape.open(encoding="utf-8-sig") as file:
        entries = [row for row in csv.DictReader(file)
                   if row["Type"].startswith("Entry")]
    assert len(entries) == 1
    assert entries[0]["Signal"] == "bare-na"
    assert first_tr is None or math.isnan(first_tr)
    assert first_true is not None and math.isfinite(first_true)
    compared, mismatched, first = per_bar_mismatches(
        actual, expected, labels=("bare", "ta.tr(false)"))
    assert mismatched == 0, f"{mismatched}/{compared} trace differences; first {first}"
    assert bare.trades["default"] == reference.trades["default"], (
        f"bare {summary(bare.trades['default'])}; "
        f"ta.tr(false) {summary(reference.trades['default'])}")
    print(f"bare ta.tr: {compared} traces equal ta.tr(false), first-bar na; "
          f"bare {summary(bare.trades['default'])} = "
          f"ta.tr(false) {summary(reference.trades['default'])}")

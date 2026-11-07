"""ta.* calls whose length (or supertrend factor) is neither a constant nor an
input: each lowering replays a TradingView tape through transpile, compile and
run.

TradingView accepts a length the constructor cannot take. Its Pine qualifier
decides the answer (lab tv, 2026-09-26; the engine header
``pineforge/source/pine_ta_length.hpp`` states each rule with its tapes):

* a series length for ta.highest / ta.lowest / ta.highestbars /
  ta.lowestbars re-windows every call (``pineforge::source::Series*``);
* a series length for ta.percentile_nearest_rank appends every call to its
  call site's ``pineforge::source::SeriesPercentileNearestRank`` buffer;
* ta.supertrend reads its factor once, on its first execution
  (``pineforge::source::PineSupertrend``);
* a simple length -- a syminfo preset -- answers exactly as the constant
  (``pineforge::source::FirstCallBound``), a sparse window through the
  constant-length ring; an input-string ternary is input-derived and keeps
  its constructor (the request.security helpers row), with the same answer;
* a name a block shadows, or a value declared ``series``, is series; each
  request.security copy is lowered from the length its own call passes, and
  its ``timeframe.*`` reads the requested timeframe.

Each fixture under ``fixtures/ta_dynamic_length/<probe>/`` is a ``lab tv
--no-note`` export of the synthetic ``strategy.pine`` beside it on
BINANCE:ETHUSDT.P 15 from 2025-04-01 (``metrics.json`` is its provenance). The
probe enters on every bar with an id that spells the bar's values, so a trade
is equal only when every recorded value is. The run starts the corpus 15m feed
on TradingView's first bar (2025-04-01 00:00 UTC), so bar indices align.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from tests._e2e import (
    Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ta_dynamic_length"
PROBES = ("series_extremes", "supertrend_factor", "simple_length", "simple_window",
          "scoping_copies")
FIRST_BAR_MS = 1743465600000   # 2025-04-01 00:00 UTC, TradingView's bar 0
BARS = 320


def tv_signals(probe: str) -> dict[int, str]:
    """TradingView's entry Signal per recorded bar (the id's first field)."""
    with (FIXTURES / probe / "tv_trades.csv").open(encoding="utf-8-sig") as file:
        rows = [row for row in csv.DictReader(file) if row["Type"].startswith("Entry")]
    return {int(row["Signal"].split("|")[0]): row["Signal"] for row in rows}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("ta_dynamic_length")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        kept = 0
        for line in inp:
            if int(line.split(",", 1)[0]) < FIRST_BAR_MS:
                continue
            out.write(line)
            kept += 1
            if kept == BARS:
                break
    builds = {probe: Build((FIXTURES / probe / "strategy.pine").read_text(encoding="utf-8"))
              for probe in PROBES}
    outcomes = execute_all(engine, feed, base, builds)
    return engine, feed, base, outcomes


def replay(runs, probe: str) -> tuple[str, int]:
    """The probe's C++ and the number of recorded bars whose engine entry id
    equals TradingView's Signal; fails on the first difference."""
    engine, feed, base, outcomes = runs
    outcome = ok(outcomes, probe)
    trades = closed_trades(engine, base / probe, feed)
    engine_ids = {int(t["entry_id"].split("|")[0]): t["entry_id"] for t in trades}
    expected = tv_signals(probe)
    compared = 0
    for bar in sorted(expected):
        if bar not in engine_ids:
            # The last recorded entry stays open at the end of the fed range.
            assert bar == max(expected), f"{probe}: no engine entry on bar {bar}"
            continue
        assert engine_ids[bar] == expected[bar], (
            f"{probe} bar {bar}:\n  engine      {engine_ids[bar]}\n"
            f"  TradingView {expected[bar]}")
        compared += 1
    assert compared >= len(expected) - 1
    return outcome.transpiled["cpp"], compared


def test_series_length_extremes_rewindow_every_call(runs):
    cpp, compared = replay(runs, "series_extremes")
    assert "#include <pineforge/source/pine_ta_length.hpp>" in cpp
    for cls in ("SeriesLowest", "SeriesHighest", "SeriesLowestBars", "SeriesHighestBars"):
        assert f"pineforge::source::{cls} " in cpp, cls
    assert "pineforge::source::ta_number(" in cpp
    print(f"series extremes: {compared}/{compared} bars equal TradingView")


def test_supertrend_reads_its_first_factor(runs):
    cpp, compared = replay(runs, "supertrend_factor")
    assert "pineforge::source::PineSupertrend " in cpp
    # The input-backed atrPeriod stays an override-aware read, the series
    # factor is passed on the bar.
    assert "ta_number(effectiveMult)" in cpp
    print(f"supertrend series factor: {compared}/{compared} bars equal TradingView")


def test_simple_length_is_the_constant(runs):
    cpp, compared = replay(runs, "simple_length")
    for cls in ("ta::RSI", "ta::RMA", "ta::ATR"):
        assert f"pineforge::source::FirstCallBound<{cls}> " in cpp, cls
    # ``lenP`` is an input.string choice: input-derived, its EMA keeps the
    # constructor and is reset from the input (the request.security helpers
    # row), with the same trades.
    assert "pineforge::source::FirstCallBound<ta::EMA> " not in cpp
    assert "= ta::EMA(" in cpp
    # The request.security payload builds its own from the context-free
    # spelling of the length (inputs through their override-aware getters).
    assert "_sec0__ta_rsi_" in cpp and "simple_ta_length(" in cpp
    assert 'get_input_string("Preset"' in cpp
    print(f"simple lengths: {compared}/{compared} bars equal TradingView")


def test_simple_window_length_reads_the_constant_ring(runs):
    cpp, compared = replay(runs, "simple_window")
    assert "pineforge::source::FirstCallBound<ta::Lowest> " in cpp
    assert "pineforge::source::FirstCallBound<ta::HighestBars> " in cpp
    assert "pineforge::source::SeriesLowest " in cpp
    assert "pineforge::source::SeriesHighestBars " in cpp
    print(f"simple vs series window: {compared}/{compared} bars equal TradingView")


def test_scopes_and_request_security_copies(runs):
    # A length shadowed in a block, a simple value declared ``series``, one
    # helper reached from two payloads (simple and series lengths),
    # ``timeframe.*`` in a payload's length (the requested timeframe) and a
    # callable's request.security cloned per call site: every column of every
    # bar equals TradingView's.
    cpp, compared = replay(runs, "scoping_copies")
    assert "pineforge::source::SeriesHighest " in cpp
    assert "pineforge::source::SeriesLowest " in cpp
    assert 'tf_multiplier("60")' in cpp
    print(f"scopes and request.security copies: {compared}/{compared} bars equal TradingView")


SYNTHETIC_BARS = 240

PERCENTILE_LOOP = """//@version=6
strategy("series percentile loop")
length = input.int(20, "Length")
float acc = 0.0
for len = 2 to 4
    acc += ta.percentile_nearest_rank(close, len, 50)
// @pf-trace acc=acc
plot(acc)
"""

PERCENTILE_CONTROL = """//@version=6
strategy("series percentile control")
p2 = ta.percentile_nearest_rank(close, 2, 50)
p3 = ta.percentile_nearest_rank(close, 3, 50)
p4 = ta.percentile_nearest_rank(close, 4, 50)
acc = p2 + p3 + p4
// @pf-trace acc=acc
plot(acc)
"""


def nearest_rank(vals: list[float], pct: float) -> float:
    ordered = sorted(vals)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _synthetic_closes() -> list[float]:
    return [
        100.0 + ((bar * 37) % 97) + (bar % 7) * 0.125
        for bar in range(SYNTHETIC_BARS)
    ]


def _write_synthetic_feed(path: Path, closes: list[float]) -> None:
    rows = ["timestamp,open,high,low,close,volume\n"]
    for bar, close in enumerate(closes):
        timestamp = 1_704_067_200_000 + bar * 900_000
        rows.append(
            f"{timestamp},{close - 0.5},{close + 1.0},{close - 1.0},{close},1000\n"
        )
    path.write_text("".join(rows), encoding="utf-8")


def _loop_model(closes: list[float]) -> list[float]:
    buffer: list[float] = []
    expected: list[float] = []
    for src in closes:
        acc = 0.0
        for length in (2, 3, 4):
            buffer.append(src)
            if len(buffer) < length:
                acc = math.nan
            elif not math.isnan(acc):
                acc += nearest_rank(buffer[-length:], 50)
        expected.append(acc)
    return expected


def _control_model(closes: list[float]) -> list[float]:
    buffers = {length: [] for length in (2, 3, 4)}
    expected: list[float] = []
    for src in closes:
        values = []
        for length, buffer in buffers.items():
            buffer.append(src)
            values.append(
                nearest_rank(buffer[-length:], 50)
                if len(buffer) >= length else math.nan
            )
        expected.append(sum(values) if all(math.isfinite(v) for v in values)
                       else math.nan)
    return expected


def _trace_column(outcome, name: str) -> list[float]:
    records = [record for record in outcome.traces["default"]
               if record["name"] == name]
    assert [record["bar_index"] for record in records] == list(range(SYNTHETIC_BARS))
    return [record["value"] for record in records]


def _assert_trace_matches(got: list[float], expected: list[float]) -> None:
    assert len(got) == len(expected) == SYNTHETIC_BARS
    for bar, (actual, want) in enumerate(zip(got, expected)):
        if math.isnan(want):
            assert math.isnan(actual), (bar, actual, want)
        else:
            assert math.isfinite(actual)
            assert actual == want, (bar, actual, want)


def test_series_percentile_loop_matches_independent_model(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("series_percentile")
    closes = _synthetic_closes()
    feed = base / "synthetic.csv"
    _write_synthetic_feed(feed, closes)
    runs = execute_all(engine, feed, base, {
        "loop": Build(PERCENTILE_LOOP, trace=True),
        "control": Build(PERCENTILE_CONTROL, trace=True),
    })

    loop = ok(runs, "loop")
    control = ok(runs, "control")
    assert "pineforge::source::SeriesPercentileNearestRank" in loop.transpiled["cpp"]
    _assert_trace_matches(_trace_column(loop, "acc"), _loop_model(closes))
    _assert_trace_matches(_trace_column(control, "acc"), _control_model(closes))

    loop_values = _trace_column(loop, "acc")
    control_values = _trace_column(control, "acc")
    assert next(bar for bar, value in enumerate(loop_values)
                if math.isfinite(value)) == 1
    assert next(bar for bar, value in enumerate(control_values)
                if math.isfinite(value)) == 3

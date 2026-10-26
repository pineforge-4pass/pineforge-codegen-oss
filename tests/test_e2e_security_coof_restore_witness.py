"""RED-FIRST witness: deferred requested samples must survive a late COOF restore.

The 5m/lookahead_on auxiliary site publishes its first bucket before the
15m chart body and evaluates the other two after the chart checkpoint.
A full-margin long's rounded-money call then executes AFTER that tail.
The following bar must retain all three requested samples, not just the
first. A separate 30m/lookahead_off site exercises the ordinary HTF route.

Engine main 2561cfb4, paths relative to its root:
* src/source/pine_security_eval.cpp:337-373: finer auxiliary first-bucket latch.
* src/source/pine_aux_security.cpp:385-413,489-520: defer/drain the other buckets.
* src/source/pine_scheduler_native.cpp:703-714: commit BEFORE deferred feeding.
* src/source/pine_strategy_host.cpp:943-945: adapter close policy AFTER scheduler.
* src/source/pine_adapter.cpp:19100-19223,24721-24740: late one-unit money call.
* src/source/pine_scheduler_native.cpp:740-759,821,866: admitted fill restores.

This asserts preservation, with no xfail or output-derived expected counts.
The control must pass before the stressed strategy is built or run.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env


MINUTE_MS = 60_000
CHART_MINUTES = 15
CHART_MS = CHART_MINUTES * MINUTE_MS
CHART_BARS = 8
START_MS = 1_743_465_600_000  # 2025-04-01 00:00 UTC; aligned to both requests.
MARGIN_BAR = 3
ENTRY_PRICE = 100_000_000.0
TICK = 0.25
CAPITAL = 3 * ENTRY_PRICE
Bar = tuple[int, float, float, float, float, float]
TRACE_NAMES = (
    "higher", "finer", "chart_time", "chart_price", "chart_walks", "callbacks",
    "position", "equity", "closed", "margin", "margin_price",
)


def _source(units: int) -> str:
    return f"""//@version=6
strategy("requested state late restore witness", initial_capital={CAPITAL},
     currency=currency.USD, pyramiding=1, calc_on_order_fills=true,
     process_orders_on_close=false, margin_long=100, margin_short=100,
     commission_value=0, slippage=0)
h() =>
    var int n = 0
    n += 1
    n
higher = request.security(syminfo.tickerid, "30", h(), lookahead=barmerge.lookahead_off)
finer = request.security(syminfo.tickerid, "5", h(), lookahead=barmerge.lookahead_on)
var int chartWalks = 0
varip int callbacks = 0
chartWalks += 1
callbacks += 1
if bar_index == 0
    strategy.entry("L", strategy.long, qty={units})
lastIsMargin = strategy.closedtrades > 0 ? strategy.closedtrades.exit_comment(strategy.closedtrades - 1) == "Margin call" : false
lastExitPrice = strategy.closedtrades > 0 ? strategy.closedtrades.exit_price(strategy.closedtrades - 1) : 0.0
// @pf-trace higher=nz(higher, 0)
// @pf-trace finer=nz(finer, 0)
// @pf-trace chart_time=time
// @pf-trace chart_price=close
// @pf-trace chart_walks=chartWalks
// @pf-trace callbacks=callbacks
// @pf-trace position=strategy.position_size
// @pf-trace equity=strategy.equity
// @pf-trace closed=strategy.closedtrades
// @pf-trace margin=lastIsMargin ? 1 : 0
// @pf-trace margin_price=lastExitPrice
"""


def _write_feed(path: Path, rows: list[Bar]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        writer.writerows(rows)


def _feeds(base: Path) -> tuple[Path, Path, list[Bar], list[Bar]]:
    # Binary-exact prices avoid an accidental earlier floating-point deficit.
    # Only the last minute of bar 3 moves; every chart OHLC is derived below.
    change_minute = (MARGIN_BAR + 1) * CHART_MINUTES - 1
    minutes = []
    for i in range(CHART_BARS * CHART_MINUTES):
        opening = ENTRY_PRICE + (TICK if i > change_minute else 0.0)
        closing = ENTRY_PRICE + (TICK if i >= change_minute else 0.0)
        minutes.append((START_MS + i * MINUTE_MS, opening, closing,
                        opening, closing, 1.0))
    chart = []
    for begin in range(0, len(minutes), CHART_MINUTES):
        bucket = minutes[begin:begin + CHART_MINUTES]
        chart.append((bucket[0][0], bucket[0][1], max(b[2] for b in bucket),
                      min(b[3] for b in bucket), bucket[-1][4],
                      sum(b[5] for b in bucket)))
    chart_path, aux_path = base / "chart.csv", base / "aux_1m.csv"
    _write_feed(chart_path, chart)
    _write_feed(aux_path, minutes)
    return chart_path, aux_path, chart, minutes


def _completed_bucket_ends(minutes: list[Bar], length: int) -> list[int]:
    """Count actual complete sets of minute timestamps, independently of runs."""
    period = length * MINUTE_MS
    buckets: dict[int, set[int]] = {}
    for timestamp, *_ in minutes:
        opening = timestamp // period * period
        buckets.setdefault(opening, set()).add(timestamp)
    return sorted(
        opening + period for opening, timestamps in buckets.items()
        if timestamps == set(range(opening, opening + period, MINUTE_MS))
    )


def _oracle(chart: list[Bar], minutes: list[Bar]) -> dict[int, dict[str, int]]:
    higher_ends = _completed_bucket_ends(minutes, 30)
    finer_ends = _completed_bucket_ends(minutes, 5)
    expected = {}
    for timestamp, *_ in chart:
        chart_close = timestamp + CHART_MS
        # lookahead_on on a finer auxiliary request merges the FIRST bucket.
        # Its deferred siblings must still contribute to all subsequent bars.
        first_end = min(end for end in finer_ends if timestamp < end <= chart_close)
        expected[timestamp] = {
            "higher": sum(end <= chart_close for end in higher_ends),
            "finer": sum(end <= first_end for end in finer_ends),
        }
    return expected


def _frames(records: list[dict], chart: list[Bar]) -> dict[int, list[dict]]:
    """Keep EVERY callback; do not collapse a late fill into its chart close."""
    width = len(TRACE_NAMES)
    assert records and len(records) % width == 0, "missing/incomplete trace"
    by_bar: dict[int, list[dict]] = {}
    indices = {bar[0]: i for i, bar in enumerate(chart)}
    for offset in range(0, len(records), width):
        chunk = records[offset:offset + width]
        assert tuple(r["name"] for r in chunk) == TRACE_NAMES, chunk
        timestamp = chunk[0]["timestamp"]
        assert timestamp in indices, chunk
        assert all(r["timestamp"] == timestamp
                   and r["bar_index"] == indices[timestamp] for r in chunk), chunk
        by_bar.setdefault(timestamp, []).append({r["name"]: r["value"] for r in chunk})
    assert set(by_bar) == set(indices), "not every chart bar executed"
    return by_bar


def _assert_counts(by_bar: dict[int, list[dict]], expected: dict[int, dict[str, int]],
                   label: str) -> None:
    for timestamp, callbacks in by_bar.items():
        for execution, row in enumerate(callbacks):
            for name, count in expected[timestamp].items():
                assert row[name] == count, (
                    f"{label}: {name} at {timestamp}, callback {execution}: "
                    f"got {row[name]}, expected {count} completed requested buckets "
                    "at the request's merge frontier (counted from the minute feed)"
                )


def _run(engine: Path, base: Path, feed: Path, aux: Path, key: str, units: int):
    work = base / key
    work.mkdir()
    # _e2e.run_strategy's default run reads this inputs.json. Both builds use
    # the identical split feeds and explicit grid; no throwaway unconfigured run.
    (work / "inputs.json").write_text(json.dumps({
        "input_tf": "15", "script_tf": "15",
        "aux_security_ohlcv_csv": str(aux), "aux_security_input_tf": "1",
        "runtime_overrides": {
            "mintick": TICK, "pointvalue": 1, "qty_step": 1,
            "currency": "USD", "timezone": "UTC",
            "syminfo_metadata": {"mincontract": 1},
        },
    }), encoding="utf-8")
    return ok(execute_all(engine, feed, base, {key: Build(_source(units), trace=True)}), key)


def test_requested_counter_survives_post_commit_margin_fill_restore(tmp_path):
    engine = skip_unless_e2e_env()
    feed, aux, chart, minutes = _feeds(tmp_path)
    expected = _oracle(chart, minutes)
    trigger = chart[MARGIN_BAR][0]
    following = chart[MARGIN_BAR + 1][0]

    # Full margin: Q*entry == capital, so free cash is zero. At bar 3's
    # close, sig10(300000000.75) == 300000000.8 > the exact value. The
    # open/low remain exactly funded, and the high is excluded by the
    # exact-residual branch (pine_adapter.cpp:19201-19205).
    assert CAPITAL - 3 * chart[1][1] == 0
    assert 3 * chart[MARGIN_BAR][4] < 300_000_000.8
    assert CAPITAL - 2 * chart[1][1] > TICK

    control = _run(engine, tmp_path, feed, aux, "control", 2)
    control_rows = _frames(control.traces["default"], chart)
    _assert_counts(control_rows, expected, "control")
    for i, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            assert row["position"] == (0 if i == 0 else 2), row
            assert row["closed"] == row["margin"] == row["margin_price"] == 0, row
            assert row["chart_walks"] == i + 1, row
    assert len(control_rows[trigger]) == 1, "control unexpectedly recalculated on bar 3"

    # A failed control never starts the stressed build/run.
    stressed = _run(engine, tmp_path, feed, aux, "stressed", 3)
    stressed_rows = _frames(stressed.traces["default"], chart)
    late = stressed_rows[trigger]
    assert [(r["position"], r["closed"], r["margin"]) for r in late] == [
        (3, 0, 0), (2, 1, 1),
    ], f"witness did not reach a Margin call AFTER the ordinary chart body: {late}"
    before, after = late
    assert after["margin_price"] == chart[MARGIN_BAR][4], after
    assert after["callbacks"] == before["callbacks"] + 1, late
    assert after["chart_walks"] == before["chart_walks"] + 1, late
    next_row = stressed_rows[following][0]
    # The late callback's ordinary var increment is provisional; its varip
    # increment survives. Together these prove an actual script-state restore,
    # not merely a booked trade or a disabled/filtered fill recalculation.
    assert next_row["chart_walks"] == after["chart_walks"], (late, next_row)
    assert next_row["callbacks"] == after["callbacks"] + 1, (late, next_row)
    for i, bar in enumerate(chart):
        rows = stressed_rows[bar[0]]
        if i < MARGIN_BAR:
            assert all(r["closed"] == r["margin"] == 0 for r in rows), rows
            assert all(r["position"] == (0 if i == 0 else 3) for r in rows), rows
        elif i > MARGIN_BAR:
            assert all(r["closed"] == r["margin"] == 1 and r["position"] == 2
                       for r in rows), rows
    assert sum(map(len, stressed_rows.values())) == sum(map(len, control_rows.values())) + 1

    # RED-FIRST: no xfail, no expected deficit, no comparison only to output.
    # A stale checkpoint loses two deferred buckets; the first following bar
    # must fail this oracle even if the engine's evaluator counter advanced.
    _assert_counts(stressed_rows, expected, "stressed; late Margin call and restore proved")
    for timestamp, rows in stressed_rows.items():
        reference = control_rows[timestamp][0]
        for row in rows:
            for name in ("higher", "finer", "chart_time", "chart_price"):
                assert row[name] == reference[name], (timestamp, name, row, reference)
    # Position, equity, closed-trade metadata, the extra callback and its
    # provisional chart var increment legitimately differ between the runs.

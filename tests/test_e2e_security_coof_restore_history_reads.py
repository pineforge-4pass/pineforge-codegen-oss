"""Chart history reads must retain requested samples across a late COOF restore.

Reuse the counter witness's minute feed, financial trigger and source. Add
chart-clock [1] reads of its finer result and of a requested float counter [1].
Every callback is checked against complete minute buckets, control first.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests import test_e2e_security_coof_restore_witness as witness


TRACE_NAMES = witness.TRACE_NAMES + (
    "finer_prior", "requested_offset", "requested_prior",
)


def _source(units: int) -> str:
    return witness._source(units) + """\
historyCounter() =>
    var float count = 0.0
    count += 1.0
    count
requestedOffset = request.security(syminfo.tickerid, "5", historyCounter()[1], lookahead=barmerge.lookahead_on)
finerPrior = finer[1]
requestedPrior = requestedOffset[1]
// @pf-trace finer_prior=nz(finerPrior, 0)
// @pf-trace requested_offset=nz(requestedOffset, 0)
// @pf-trace requested_prior=nz(requestedPrior, 0)
"""


def _history_oracle(chart: list[witness.Bar], minutes: list[witness.Bar]):
    ends = witness._completed_bucket_ends(minutes, 5)
    frontiers = [
        min(end for end in ends if timestamp < end <= timestamp + witness.CHART_MS)
        for timestamp, *_ in chart
    ]
    histories = {}
    current = {}
    for index, (timestamp, *_) in enumerate(chart):
        previous = frontiers[index - 1] if index else None
        histories[timestamp] = {
            "finer_prior": (
                sum(end <= previous for end in ends) if previous is not None else 0
            ),
            "requested_prior": (
                sum(end < previous for end in ends) if previous is not None else 0
            ),
        }
        # The payload's [1] sees completed requested buckets strictly BEFORE
        # the first bucket merged into this bar. No sample is traced as zero.
        current[timestamp] = {
            "requested_offset": sum(end < frontiers[index] for end in ends),
        }
    return histories, current


def _frames(records: list[dict], chart: list[witness.Bar]) -> dict[int, list[dict]]:
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


def _assert_history(by_bar, expected, label: str) -> None:
    for timestamp, callbacks in by_bar.items():
        for execution, row in enumerate(callbacks):
            for name, value in expected[timestamp].items():
                assert row[name] == value, (
                    f"{label}: chart history {name} at {timestamp}, callback {execution}: "
                    f"got {row[name]}, expected {value} from the preceding chart "
                    "bar's minute-derived requested merge frontier"
                )


def _run(engine: Path, base: Path, feed: Path, aux: Path, key: str, units: int):
    work = base / key
    work.mkdir()
    (work / "inputs.json").write_text(json.dumps({
        "input_tf": "15", "script_tf": "15",
        "aux_security_ohlcv_csv": str(aux), "aux_security_input_tf": "1",
        "runtime_overrides": {
            "mintick": witness.TICK, "pointvalue": 1, "qty_step": 1,
            "currency": "USD", "timezone": "UTC",
            "syminfo_metadata": {"mincontract": 1},
        },
    }), encoding="utf-8")
    return ok(execute_all(engine, feed, base, {key: Build(_source(units), trace=True)}), key)


def test_requested_history_reads_survive_post_commit_margin_fill_restore(tmp_path):
    engine = skip_unless_e2e_env()
    feed, aux, chart, minutes = witness._feeds(tmp_path)
    expected_counts = witness._oracle(chart, minutes)
    expected_history, expected_offsets = _history_oracle(chart, minutes)
    trigger = chart[witness.MARGIN_BAR][0]
    following = chart[witness.MARGIN_BAR + 1][0]
    control_units, stressed_units = 2, 3

    assert witness.CAPITAL - stressed_units * chart[1][1] == 0
    notional = stressed_units * chart[witness.MARGIN_BAR][4]
    assert notional < float(f"{notional:.10g}")
    assert witness.CAPITAL - control_units * chart[1][1] > witness.TICK

    control = _run(engine, tmp_path, feed, aux, "history_control", control_units)
    control_rows = _frames(control.traces["default"], chart)
    _assert_history(control_rows, expected_history, "control")
    witness._assert_counts(control_rows, expected_offsets, "control requested [1]")
    witness._assert_counts(control_rows, expected_counts, "control")
    for i, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            assert row["position"] == (0 if i == 0 else control_units), row
            assert row["closed"] == row["margin"] == row["margin_price"] == 0, row
            assert row["chart_walks"] == i + 1, row
    assert len(control_rows[trigger]) == 1, "control unexpectedly recalculated at margin bar"

    # No stressed build/run starts until every control assertion above passes.
    stressed = _run(engine, tmp_path, feed, aux, "history_stressed", stressed_units)
    stressed_rows = _frames(stressed.traces["default"], chart)
    late = stressed_rows[trigger]
    assert [(r["position"], r["closed"], r["margin"]) for r in late] == [
        (stressed_units, 0, 0), (control_units, 1, 1),
    ], f"witness did not reach a Margin call AFTER the ordinary chart body: {late}"
    before, after = late
    assert after["margin_price"] == chart[witness.MARGIN_BAR][4], after
    assert after["callbacks"] == before["callbacks"] + 1, late
    assert after["chart_walks"] == before["chart_walks"] + 1, late
    next_row = stressed_rows[following][0]
    assert next_row["chart_walks"] == after["chart_walks"], (late, next_row)
    assert next_row["callbacks"] == after["callbacks"] + 1, (late, next_row)
    for i, bar in enumerate(chart):
        rows = stressed_rows[bar[0]]
        if i < witness.MARGIN_BAR:
            assert all(r["closed"] == r["margin"] == 0 for r in rows), rows
            assert all(r["position"] == (0 if i == 0 else stressed_units)
                       for r in rows), rows
        elif i > witness.MARGIN_BAR:
            assert all(r["closed"] == r["margin"] == 1 and r["position"] == control_units
                       for r in rows), rows
    assert sum(map(len, stressed_rows.values())) == sum(map(len, control_rows.values())) + 1

    # Check the CHART [1] reads first. A lost requested sample enters the
    # chart's finer result on the next bar and its [1] on the bar after that.
    # The unmodified witness independently checks the immediate counter loss.
    _assert_history(stressed_rows, expected_history, "stressed; late Margin restore proved")
    witness._assert_counts(stressed_rows, expected_offsets, "stressed requested [1]")
    witness._assert_counts(stressed_rows, expected_counts, "stressed")
    for timestamp, rows in stressed_rows.items():
        reference = control_rows[timestamp][0]
        for row in rows:
            for name in (
                "higher", "finer", "chart_time", "chart_price",
                "finer_prior", "requested_offset", "requested_prior",
            ):
                assert row[name] == reference[name], (timestamp, name, row, reference)

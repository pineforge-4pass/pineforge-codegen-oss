"""Derived argument history must survive the deferred tail's late COOF restore.

The finer request samples close - open into h(x) => x[1]. Its last deferred
5m bucket contains the minute feed's price change, so the NEXT chart bar must
read that bucket's difference. A restore that rolls the ring back loses it.
Every callback, including the late margin callback, uses a minute-derived
oracle. The fully checked control runs before the stressed build starts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests import test_e2e_security_coof_restore_witness as witness


def _source(units: int) -> str:
    return f"""//@version=6
strategy("argument history late restore witness", initial_capital={witness.CAPITAL},
     currency=currency.USD, pyramiding=1, calc_on_order_fills=true,
     process_orders_on_close=false, margin_long=100, margin_short=100,
     commission_value=0, slippage=0)
counter() =>
    var int n = 0
    n += 1
    n
h(x) => x[1]
higher = request.security(syminfo.tickerid, "30", counter(), lookahead=barmerge.lookahead_off)
finer = request.security(syminfo.tickerid, "5", h(close - open), lookahead=barmerge.lookahead_on)
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


def _oracle(chart: list[witness.Bar], minutes: list[witness.Bar]):
    ends = witness._completed_bucket_ends(minutes, 5)
    higher_ends = witness._completed_bucket_ends(minutes, 30)
    by_minute = {bar[0]: bar for bar in minutes}
    assert len(by_minute) == len(minutes), "duplicate minute timestamps"
    period = 5 * witness.MINUTE_MS
    differences = {}
    for end in ends:
        bucket = [
            by_minute[timestamp]
            for timestamp in range(end - period, end, witness.MINUTE_MS)
        ]
        differences[end] = bucket[-1][4] - bucket[0][1]

    expected = {}
    for timestamp, _, _, _, close, _ in chart:
        chart_close = timestamp + witness.CHART_MS
        # Finer lookahead_on publishes the first complete 5m bucket in this
        # chart bar. h(x)'s [1] reads the immediately preceding bucket.
        frontier = min(end for end in ends if timestamp < end <= chart_close)
        preceding = [end for end in ends if end < frontier]
        expected[timestamp] = {
            "higher": sum(end <= chart_close for end in higher_ends),
            "finer": differences[preceding[-1]] if preceding else 0.0,
            "chart_time": timestamp,
            "chart_price": close,
        }
    return expected


def _assert_values(by_bar, expected, label: str) -> None:
    for timestamp, callbacks in by_bar.items():
        for execution, row in enumerate(callbacks):
            for name, value in expected[timestamp].items():
                assert row[name] == value, (
                    f"{label}: {name} at {timestamp}, callback {execution}: "
                    f"got {row[name]}, expected {value} from complete minute "
                    "buckets at the request's merge frontier"
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


def test_argument_history_survives_post_commit_margin_fill_restore(tmp_path):
    try:
        engine = skip_unless_e2e_env()
    except pytest.skip.Exception as exc:
        pytest.fail(f"e2e environment is required for this witness: {exc}", pytrace=False)
    feed, aux, chart, minutes = witness._feeds(tmp_path)
    expected = _oracle(chart, minutes)
    trigger = chart[witness.MARGIN_BAR][0]
    following = chart[witness.MARGIN_BAR + 1][0]
    control_units, stressed_units = 2, 3

    assert witness.CAPITAL - stressed_units * chart[1][1] == 0
    notional = stressed_units * chart[witness.MARGIN_BAR][4]
    assert notional < float(f"{notional:.10g}")
    assert witness.CAPITAL - control_units * chart[1][1] > witness.TICK
    assert expected[following]["finer"] != expected[trigger]["finer"], (
        "the deferred tail must contribute a distinct previous-bucket sample"
    )

    control = _run(engine, tmp_path, feed, aux, "argument_control", control_units)
    control_rows = witness._frames(control.traces["default"], chart)
    _assert_values(control_rows, expected, "control")
    for i, bar in enumerate(chart):
        for row in control_rows[bar[0]]:
            assert row["position"] == (0 if i == 0 else control_units), row
            assert row["closed"] == row["margin"] == row["margin_price"] == 0, row
            assert row["chart_walks"] == i + 1, row
    assert len(control_rows[trigger]) == 1, "control unexpectedly recalculated at margin bar"

    # A failed control stops here, before the stressed strategy is built or run.
    stressed = _run(engine, tmp_path, feed, aux, "argument_stressed", stressed_units)
    stressed_rows = witness._frames(stressed.traces["default"], chart)
    late = stressed_rows[trigger]
    assert [(r["position"], r["closed"], r["margin"]) for r in late] == [
        (stressed_units, 0, 0), (control_units, 1, 1),
    ], f"witness did not reach a Margin call AFTER the ordinary chart body: {late}"
    before, after = late
    assert after["margin_price"] == chart[witness.MARGIN_BAR][4], after
    assert after["callbacks"] == before["callbacks"] + 1, late
    assert after["chart_walks"] == before["chart_walks"] + 1, late
    next_row = stressed_rows[following][0]
    # Ordinary var rolls back after the provisional late callback; varip
    # retains the extra callback. This proves a script-state restore occurred.
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

    # Every callback reads finer, including the late callback and NEXT bar.
    # A restored ring loses the deferred tail while the engine clock advances,
    # so the next bar fails this feed-derived previous-bucket oracle.
    _assert_values(stressed_rows, expected, "stressed; late Margin restore proved")
    for timestamp, rows in stressed_rows.items():
        reference = control_rows[timestamp][0]
        for row in rows:
            for name in ("higher", "finer", "chart_time", "chart_price"):
                assert row[name] == reference[name], (timestamp, name, row, reference)

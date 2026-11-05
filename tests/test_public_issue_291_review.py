"""Supplementary native review cases for public issue #291.

This module is test source only.  It adds three source-derived native cases
requested by the review:

* an actual late-entry ``calc_on_order_fills`` callback with a history
  expression inside a loop, plus an isolated marker-restore negative control;
* inferred and unannotated string Series, alongside one explicit declaration
  that exercises ``_global_string_hint``;
* input values that are never reassigned, covering typed and generic scalar
  families, an input source, and a lexical shadow next to a mutated control.

The cases use ``tests._e2e`` as the outside-in harness and need a native engine
build to run.

The COOF case covers a measured ``coof-sensitive.py`` contract: signal on bar
4, CSV entry fill and extra callback on bar 5 after history is finite, and an
ordinary close on bar 6.  The off/on sources differ only in the COOF flag.  The
original feed, source factory and arithmetic oracle are retained; older
margin attempts remain historical evidence and are not credited by this case.
A position transition between the two fill-bar callbacks is not a precondition:
the real CSV fill time must coincide with the extra callback instead.

The case also compiles and runs an isolated generated-C++ mutant, deleting only
the actual history gate's bar-marker restore and keeping its Series restore.
Missing or ambiguous restore matches, compile failures and native failures fail
the test; only actual history-value mismatches kill the mutant.  All positive
callbacks use the independent feed/depth/gain oracle, for defaults (2, 1.0)
and overrides (3, -0.5).  The original margin-call branch, sibling argument
order, other depths, full-suite behavior and TradingView parity are outside
these cases.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests._e2e import (
    Build,
    build_strategy_library,
    execute_all,
    ok,
    run_strategy,
    skip_unless_e2e_env,
    trade_count,
)


BAR_MS = 900_000
START_MS = 1_743_465_600_000
SERIES_BARS = 24
COOF_ENTRY = 100_000_000.0
COOF_CAPITAL = 3 * COOF_ENTRY
COOF_SIGNAL_BAR = 4
COOF_FILL_BAR = COOF_SIGNAL_BAR + 1
COOF_CLOSE_BAR = 6
COOF_TRACE_NAMES = (
    "loop_inline",
    "loop_named",
    "named_previous",
    "callbacks",
    "chart_passes",
    "is_new",
    "position",
    "closed",
    "margin",
    "margin_price",
)

Bar = tuple[int, float, float, float, float, float]


def _write_feed(path: Path, bars: list[Bar]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        writer.writerows(bars)


def _run(
    engine: Path,
    base: Path,
    feed: Path,
    key: str,
    source: str,
    overrides: dict[str, object] | None = None,
):
    return ok(
        execute_all(
            engine,
            feed,
            base,
            {key: Build(source, overrides=overrides, trace=True)},
        ),
        key,
    )


def _trace_values(outcome, tag: str, name: str, bars: list[Bar]) -> list[object]:
    records = [record for record in outcome.traces[tag] if record["name"] == name]
    assert len(records) == len(bars), f"{tag}/{name}: incomplete trace"
    for index, (record, bar) in enumerate(zip(records, bars)):
        assert record["bar_index"] == index
        assert record["timestamp"] == bar[0]
    return [record["value"] for record in records]


def _same_value(got: object, wanted: object) -> bool:
    if isinstance(wanted, float) and math.isnan(wanted):
        return isinstance(got, (int, float)) and math.isnan(float(got))
    return got == wanted


def _assert_values(
    outcome,
    tag: str,
    name: str,
    bars: list[Bar],
    expected: list[object],
) -> None:
    actual = _trace_values(outcome, tag, name, bars)
    assert len(actual) == len(expected)
    mismatches = [
        (index, got, wanted)
        for index, (got, wanted) in enumerate(zip(actual, expected))
        if not _same_value(got, wanted)
    ]
    assert not mismatches, f"{tag}/{name}: {mismatches[:3]}"


def _frames(
    records: list[dict],
    names: tuple[str, ...],
    bars: list[Bar],
) -> dict[int, list[dict]]:
    assert records
    assert len(records) % len(names) == 0, "incomplete callback trace"
    positions = {bar[0]: index for index, bar in enumerate(bars)}
    grouped: dict[int, list[dict]] = {}
    for offset in range(0, len(records), len(names)):
        chunk = records[offset:offset + len(names)]
        assert tuple(record["name"] for record in chunk) == names, chunk
        timestamp = chunk[0]["timestamp"]
        assert timestamp in positions, chunk
        index = positions[timestamp]
        assert all(
            record["timestamp"] == timestamp and record["bar_index"] == index
            for record in chunk
        ), chunk
        grouped.setdefault(timestamp, []).append(
            {record["name"]: record["value"] for record in chunk}
        )
    assert set(grouped) == set(positions)
    return grouped


@pytest.fixture(scope="module")
def native_engine() -> Path:
    """Use the existing native gate and its paired engine/runtime checks."""
    return skip_unless_e2e_env()


def _coof_bars() -> list[Bar]:
    """Retain the original varied 15m feed; the new proof enters after warm-up."""
    offsets = (
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -0.5, -0.5),
        (0.0, 0.25, 0.0, 0.25),
        (0.25, 1.25, -0.25, 0.75),
        (0.75, 2.25, 0.5, 1.5),
        (1.5, 2.5, -1.0, -0.75),
        (-0.75, 0.0, -2.0, -1.5),
    )
    return [
        (
            START_MS + index * BAR_MS,
            COOF_ENTRY + opening,
            COOF_ENTRY + high,
            COOF_ENTRY + low,
            COOF_ENTRY + close,
            1.0,
        )
        for index, (opening, high, low, close) in enumerate(offsets)
    ]


def _coof_source(units: int) -> str:
    return f"""//@version=6
strategy("public291 COOF loop history", initial_capital={COOF_CAPITAL},
     currency=currency.USD, pyramiding=1, calc_on_order_fills=true,
     process_orders_on_close=false, margin_long=100, margin_short=100,
     commission_value=0, slippage=0)
depth = input.int(2, "History depth", minval=1, maxval=4)
gain = input.float(1.0, "History gain")
named = (close + open) / 2 * gain
float inlineSum = 0.0
float namedSum = 0.0
for i = 1 to depth
    inlineSum += ((close + open) / 2 * gain)[i]
    namedSum += named[i]
namedPrevious = named[1]
var int chartPasses = 0
varip int callbacks = 0
chartPasses += 1
callbacks += 1
if bar_index == 0
    strategy.entry("L", strategy.long, qty={units})
lastIsMargin = strategy.closedtrades > 0 ? strategy.closedtrades.exit_comment(strategy.closedtrades - 1) == "Margin call" : false
lastExitPrice = strategy.closedtrades > 0 ? strategy.closedtrades.exit_price(strategy.closedtrades - 1) : 0.0
// @pf-trace loop_inline=inlineSum
// @pf-trace loop_named=namedSum
// @pf-trace named_previous=namedPrevious
// @pf-trace callbacks=callbacks
// @pf-trace chart_passes=chartPasses
// @pf-trace is_new=barstate.isnew ? 1 : 0
// @pf-trace position=strategy.position_size
// @pf-trace closed=strategy.closedtrades
// @pf-trace margin=lastIsMargin ? 1 : 0
// @pf-trace margin_price=lastExitPrice
"""


def _coof_late_entry_source(coof: bool) -> str:
    """Apply the measured diagnostic's source edits to the retained factory."""
    source = _coof_source(2).replace(
        "calc_on_order_fills=true",
        f"calc_on_order_fills={str(coof).lower()}",
    )
    source = source.replace(
        "if bar_index == 0\n",
        f"if bar_index == {COOF_SIGNAL_BAR} and strategy.position_size == 0\n",
    )
    return source.replace(
        "lastIsMargin =",
        f"if bar_index == {COOF_CLOSE_BAR} and strategy.position_size > 0\n"
        '    strategy.close("L", immediately=true, comment="Proof close")\n'
        "lastIsMargin =",
    )


def _coof_oracle(
    bars: list[Bar],
    depth: int,
    gain: float,
) -> tuple[list[float], list[float]]:
    named = [(bar[1] + bar[4]) / 2 * gain for bar in bars]
    loop = [
        math.nan if index < depth
        else sum(named[index - offset] for offset in range(1, depth + 1))
        for index in range(len(bars))
    ]
    previous = [math.nan, *named[:-1]]
    return loop, previous


def _closed_trade_count(trades_csv: bytes) -> int:
    """Exclude the runner's synthetic range-end exits for still-open trades."""
    columns = (
        "Trade #",
        "Type",
        "Date and time",
        "Price",
        "Qty",
        "Net PnL",
        "Net PnL %",
        "Favorable excursion USD",
        "Adverse excursion USD",
        "Cumulative PnL",
        "Engine entry incarnation",
        "Engine range-end",
    )
    rows = csv.reader(
        io.StringIO(trades_csv.decode("utf-8"), newline=""), strict=True,
    )
    assert tuple(next(rows, ())) == columns, "unexpected trade CSV schema"
    trades: dict[str, dict[str, dict[str, str]]] = {}
    for values in rows:
        assert len(values) == len(columns), values
        row = dict(zip(columns, values))
        number = row["Trade #"]
        assert number.isascii() and number.isdecimal() and int(number) > 0, row
        assert row["Type"] in (
            "Entry long", "Exit long", "Entry short", "Exit short",
        ), row
        role = row["Type"].split()[0]
        assert row["Date and time"], row
        for column in columns[3:10]:
            assert math.isfinite(float(row[column])), row
        assert float(row["Qty"]) > 0, row
        assert row["Engine range-end"] in ("", "open"), row
        incarnation = row["Engine entry incarnation"]
        if role == "Entry":
            assert row["Engine range-end"] == "", row
            assert (
                incarnation.isascii()
                and incarnation.isdecimal()
                and int(incarnation) > 0
            ), row
        else:
            assert incarnation == "", row
        pair = trades.setdefault(number, {})
        assert role not in pair, row
        pair[role] = row

    for pair in trades.values():
        assert set(pair) == {"Entry", "Exit"}, pair
        entry, exit_row = pair["Entry"], pair["Exit"]
        assert entry["Type"].split()[1] == exit_row["Type"].split()[1], pair
        assert float(entry["Qty"]) == float(exit_row["Qty"]), pair
    return sum(
        pair["Exit"]["Engine range-end"] == "" for pair in trades.values()
    )


def _assert_coof_fill_witness(
    trades_csv: bytes,
    records: list[dict],
    bars: list[Bar],
    *,
    coof: bool,
) -> dict[int, list[dict]]:
    """Bind the real closed trade's entry time to the extra callback."""
    # Validate the unchanged strict CSV contract before reading its timestamps.
    assert _closed_trade_count(trades_csv) == 1
    assert trade_count(trades_csv) == 1
    rows = list(csv.DictReader(
        io.StringIO(trades_csv.decode("utf-8"), newline=""), strict=True,
    ))
    assert len(rows) == 2
    trades = {row["Type"]: row for row in rows}
    assert set(trades) == {"Entry long", "Exit long"}
    times = {}
    for kind, row in trades.items():
        assert row["Engine range-end"] == ""
        assert float(row["Qty"]) == 2
        stamp = datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
        times[kind] = int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
    fill_time = times["Entry long"]
    assert fill_time == bars[COOF_FILL_BAR][0]
    assert times["Exit long"] == bars[COOF_CLOSE_BAR][0]
    assert float(trades["Entry long"]["Price"]) == bars[COOF_FILL_BAR][1]
    assert float(trades["Exit long"]["Price"]) == bars[COOF_CLOSE_BAR][4]

    grouped = _frames(records, COOF_TRACE_NAMES, bars)
    extra_times = {time for time, frames in grouped.items() if len(frames) > 1}
    assert extra_times == ({fill_time} if coof else set())
    callback_number = 0
    for index, bar in enumerate(bars):
        rows = grouped[bar[0]]
        assert len(rows) == (2 if coof and bar[0] == fill_time else 1)
        for row in rows:
            callback_number += 1
            assert row["callbacks"] == callback_number
            assert row["is_new"] == 1
            # Unlike varip callbacks, chartPasses is rolled back before recalc.
            assert row["chart_passes"] == index + 1
            expected_position = (
                2 if COOF_FILL_BAR <= index < COOF_CLOSE_BAR else 0
            )
            assert row["position"] == expected_position
            after_close = index >= COOF_CLOSE_BAR
            assert row["closed"] == (1 if after_close else 0)
            assert row["margin"] == 0
            assert row["margin_price"] == (
                bars[COOF_CLOSE_BAR][4] if after_close else 0
            )
    return grouped


def _coof_history_mismatches(
    grouped: dict[int, list[dict]],
    bars: list[Bar],
    depth: int,
    gain: float,
) -> list[tuple[int, int, str, object, float]]:
    """Compare every callback with arithmetic independent of either C++ arm."""
    expected_loop, expected_previous = _coof_oracle(bars, depth, gain)
    assert math.isfinite(expected_loop[COOF_FILL_BAR])
    assert math.isfinite(expected_previous[COOF_FILL_BAR])
    mismatches = []
    for index, bar in enumerate(bars):
        for execution, row in enumerate(grouped[bar[0]]):
            for name, wanted in (
                ("loop_inline", expected_loop[index]),
                ("loop_named", expected_loop[index]),
                ("named_previous", expected_previous[index]),
            ):
                actual = row[name]
                if index >= depth:
                    assert isinstance(actual, (int, float))
                    assert math.isfinite(float(actual)), (index, execution, name)
                if not _same_value(actual, wanted):
                    mismatches.append((index, execution, name, actual, wanted))
    return mismatches


def _skip_coof_bar_marker_restore(cpp: str) -> str:
    """Remove only the actual inline gate's clock restore, preserving Series."""
    gates = set(re.findall(
        r"history_advances_new_bar\(\) && (\w+) != bar_index_\) "
        r"(\w+)\.push\(",
        cpp,
    ))
    assert len(gates) == 1, f"expected one actual inline history gate: {gates}"
    mutant = cpp
    for clock, series in sorted(gates):
        assert clock != series
        series_pattern = (
            r"(?m)^[ \t]*this->" + re.escape(series)
            + r" = _pf_script_state_checkpoint_->_pf_value_\d+;\n"
        )
        series_restores = re.findall(series_pattern, cpp)
        assert len(series_restores) == 1, (series, series_restores)
        marker_pattern = (
            r"(?m)^[ \t]*this->" + re.escape(clock)
            + r" = _pf_script_state_checkpoint_->_pf_value_\d+;\n"
        )
        mutant, count = re.subn(marker_pattern, "", mutant)
        assert count == 1, f"expected exactly one marker restore: {clock}: {count}"
        assert re.findall(series_pattern, mutant) == series_restores
    assert len(cpp.splitlines()) - len(mutant.splitlines()) == len(gates)
    return mutant


def test_coof_late_fill_history_requires_bar_marker_restore(
    native_engine: Path,
    tmp_path: Path,
) -> None:
    bars = _coof_bars()
    feed = tmp_path / "coof.csv"
    _write_feed(feed, bars)
    overrides = {"History depth": "3", "History gain": "-0.5"}
    control_source = _coof_late_entry_source(False)
    candidate_source = _coof_late_entry_source(True)
    assert control_source == candidate_source.replace(
        "calc_on_order_fills=true", "calc_on_order_fills=false",
    )
    control = _run(
        native_engine,
        tmp_path / "coof-runs",
        feed,
        "control",
        control_source,
        overrides,
    )
    candidate = _run(
        native_engine,
        tmp_path / "coof-runs",
        feed,
        "candidate",
        candidate_source,
        overrides,
    )

    for outcome, coof in ((control, False), (candidate, True)):
        witnesses = {}
        for tag, depth, gain in (
            ("default", 2, 1.0),
            ("override", 3, -0.5),
        ):
            grouped = _assert_coof_fill_witness(
                outcome.trades[tag],
                outcome.traces[tag],
                bars,
                coof=coof,
            )
            mismatches = _coof_history_mismatches(grouped, bars, depth, gain)
            assert not mismatches, (coof, tag, mismatches)
            witnesses[tag] = grouped
        last_time = bars[-1][0]
        assert (
            witnesses["default"][last_time][-1]["loop_named"]
            != witnesses["override"][last_time][-1]["loop_named"]
        )

    assert candidate.transpiled is not None
    mutant_cpp = _skip_coof_bar_marker_restore(candidate.transpiled["cpp"])
    mutant_dir = tmp_path / "coof-marker-restore-mutant"
    mutant_dir.mkdir()
    (mutant_dir / "strategy.pine").write_text(candidate_source, encoding="utf-8")
    # These real build/run calls must succeed; no setup exception is a kill.
    build_strategy_library(mutant_cpp, mutant_dir)
    for tag, depth, gain, params in (
        ("default", 2, 1.0, None),
        ("override", 3, -0.5, overrides),
    ):
        trades, records, _stderr = run_strategy(
            native_engine, mutant_dir, feed, params, tag, trace=True,
        )
        assert records is not None
        grouped = _assert_coof_fill_witness(trades, records, bars, coof=True)
        mismatches = _coof_history_mismatches(grouped, bars, depth, gain)
        assert mismatches, f"{tag}: omitted marker restore did not alter history"
        # Named Series and previous-value controls must retain their oracle.
        assert {item[2] for item in mismatches} == {"loop_inline"}, mismatches
        assert all(item[0] >= COOF_FILL_BAR for item in mismatches), mismatches
        assert any(
            index == COOF_FILL_BAR and execution > 0
            for index, execution, _name, _actual, _wanted in mismatches
        ), (tag, "no semantic mismatch at the real fill's extra callback", mismatches)


STRING_SOURCE = """//@version=6
strategy("public291 inferred string series")
cycle = input.int(3, "String cycle")
payload = input.string("red", "Payload")
alternate = input.string("go", "Alternate")
inputTag = input.string("seed", "Input tag")
chooseTag(int n, string value) =>
    n % 4 == 0 ? value : na

// Inferred global string: its ternary and later := reach the string target
// broadening in visit_stmt.py, and its history read makes it a Series.
inferred = bar_index % cycle == 0 ? payload : na
if bar_index % 4 == 1
    inferred := alternate
inferredLength = str.length(inferred)
inferredMissing = na(inferred) ? 1 : 0
inferredPayload = na(inferred) ? 0 : inferred == payload ? 1 : 0
inferredAlternate = na(inferred) ? 0 : inferred == alternate ? 1 : 0
inferredPreviousLength = str.length(inferred[1])
inferredPreviousMissing = na(inferred[1]) ? 1 : 0

// A callable-return string is also inferred and history-referenced.
fromFunction = chooseTag(bar_index, alternate)
functionLength = str.length(fromFunction)
functionMissing = na(fromFunction) ? 1 : 0
functionAlternate = na(fromFunction) ? 0 : fromFunction == alternate ? 1 : 0
functionPreviousLength = str.length(fromFunction[1])

// This explicit declaration is the direct _global_string_hint path.
string explicit = na
if bar_index % cycle == 0
    explicit := payload
explicitLength = str.length(explicit)
explicitMissing = na(explicit) ? 1 : 0
explicitPayload = na(explicit) ? 0 : explicit == payload ? 1 : 0
explicitPreviousLength = str.length(explicit[1])

// An unannotated input string reaches the input branch with string Series
// storage, while the block-local string checks lexical lifetime.
inputSeries = input.string("seed", "Input series")
inputLength = str.length(inputSeries)
inputPreviousLength = str.length(inputSeries[1])
inputPreviousMissing = na(inputSeries[1]) ? 1 : 0
inputDefault = inputSeries == "seed" ? 1 : 0
int blockLength = 0
if bar_index % 2 == 0
    string blockString = payload
    blockLength := str.length(blockString)

var string kept = na
varip string keptip = na
if bar_index == cycle
    kept := alternate
    keptip := alternate
keptLength = str.length(kept)
keptMissing = na(kept) ? 1 : 0
keptPreviousLength = str.length(kept[1])
keptipLength = str.length(keptip)
"""

STRING_TRACE_NAMES = (
    "inferredLength",
    "inferredMissing",
    "inferredPayload",
    "inferredAlternate",
    "inferredPreviousLength",
    "inferredPreviousMissing",
    "functionLength",
    "functionMissing",
    "functionAlternate",
    "functionPreviousLength",
    "explicitLength",
    "explicitMissing",
    "explicitPayload",
    "explicitPreviousLength",
    "inputLength",
    "inputPreviousLength",
    "inputPreviousMissing",
    "inputDefault",
    "blockLength",
    "keptLength",
    "keptMissing",
    "keptPreviousLength",
    "keptipLength",
)

STRING_SOURCE += "".join(
    f"// @pf-trace {name}={name}\n" for name in STRING_TRACE_NAMES
)


def _string_state(
    index: int,
    cycle: int,
    payload: str,
    alternate: str,
) -> str | None:
    value = payload if index % cycle == 0 else None
    if index % 4 == 1:
        value = alternate
    return value


def _string_expected(
    bars: list[Bar],
    cycle: int,
    payload: str,
    alternate: str,
    input_value: str,
) -> dict[str, list[object]]:
    inferred = [_string_state(index, cycle, payload, alternate)
                for index in range(len(bars))]
    function = [alternate if index % 4 == 0 else None
                for index in range(len(bars))]
    explicit = [payload if index % cycle == 0 else None
                for index in range(len(bars))]
    kept = [alternate if index >= cycle else None
            for index in range(len(bars))]

    def length(value: str | None) -> int:
        return 0 if value is None else len(value)

    def missing(value: str | None) -> int:
        return 1 if value is None else 0

    def match(value: str | None, wanted: str) -> int:
        return 1 if value is not None and value == wanted else 0

    return {
        "inferredLength": [length(value) for value in inferred],
        "inferredMissing": [missing(value) for value in inferred],
        "inferredPayload": [match(value, payload) for value in inferred],
        "inferredAlternate": [match(value, alternate) for value in inferred],
        "inferredPreviousLength": [0, *[length(v) for v in inferred[:-1]]],
        "inferredPreviousMissing": [1, *[missing(v) for v in inferred[:-1]]],
        "functionLength": [length(value) for value in function],
        "functionMissing": [missing(value) for value in function],
        "functionAlternate": [match(value, alternate) for value in function],
        "functionPreviousLength": [0, *[length(v) for v in function[:-1]]],
        "explicitLength": [length(value) for value in explicit],
        "explicitMissing": [missing(value) for value in explicit],
        "explicitPayload": [match(value, payload) for value in explicit],
        "explicitPreviousLength": [0, *[length(v) for v in explicit[:-1]]],
        "inputLength": [len(input_value)] * len(bars),
        "inputPreviousLength": [0, *([len(input_value)] * (len(bars) - 1))],
        "inputPreviousMissing": [1, *([0] * (len(bars) - 1))],
        "inputDefault": [1 if input_value == "seed" else 0] * len(bars),
        "blockLength": [len(payload) if index % 2 == 0 else 0
                        for index in range(len(bars))],
        "keptLength": [length(value) for value in kept],
        "keptMissing": [missing(value) for value in kept],
        "keptPreviousLength": [
            0,
            *[length(value) for value in kept[:-1]],
        ],
        "keptipLength": [length(value) for value in kept],
    }


def test_inferred_string_series_history_and_string_hint_controls(
    native_engine: Path,
    tmp_path: Path,
) -> None:
    bars = [
        (
            START_MS + index * BAR_MS,
            100.0 + index * 0.75,
            101.0 + index * 0.75,
            99.0 + index * 0.75,
            100.5 + index * 0.75,
            1000.0 + index,
        )
        for index in range(SERIES_BARS)
    ]
    feed = tmp_path / "strings.csv"
    _write_feed(feed, bars)
    overrides = {
        "String cycle": "5",
        "Payload": "blueberry",
        "Alternate": "odd",
        "Input series": "changed",
    }
    outcome = _run(
        native_engine,
        tmp_path / "string-runs",
        feed,
        "strings",
        STRING_SOURCE,
        overrides,
    )
    for tag in ("default", "override"):
        grouped = _frames(outcome.traces[tag], STRING_TRACE_NAMES, bars)
        assert all(len(rows) == 1 for rows in grouped.values())
    for tag, cycle, payload, alternate, input_value in (
        ("default", 3, "red", "go", "seed"),
        ("override", 5, "blueberry", "odd", "changed"),
    ):
        expected = _string_expected(
            bars,
            cycle,
            payload,
            alternate,
            input_value,
        )
        for name, values in expected.items():
            _assert_values(outcome, tag, name, bars, values)

    assert _trace_values(outcome, "default", "inferredLength", bars) != (
        _trace_values(outcome, "override", "inferredLength", bars)
    )
    assert _trace_values(outcome, "default", "inputDefault", bars)[0] == 1
    assert _trace_values(outcome, "override", "inputDefault", bars)[0] == 0


INPUT_SOURCE = """//@version=6
strategy("public291 untouched inputs")
typedInt = input.int(4, "Typed int")
typedFloat = input.float(1.25, "Typed float")
typedBool = input.bool(true, "Typed bool")
typedString = input.string("seed", "Typed string")
typedColor = input.color(#123456, "Typed color")
genericInt = input(4, "Generic int")
genericFloat = input(2.5, "Generic float")
genericString = input("generic", "Generic string")
genericBool = input(true, "Generic bool")
sourceSeries = input.source(close, "Source input")
genericSource = input(close, "Generic source")
mutatedSibling = input.float(2.0, "Mutated sibling")

typedIntPrevious = na(typedInt[1]) ? -1 : typedInt[1]
typedFloatPrevious = na(typedFloat[1]) ? -1 : typedFloat[1]
typedBoolPrevious = typedBool[1] ? 1 : 0
typedStringLength = str.length(typedString)
typedStringPreviousLength = str.length(typedString[1])
typedStringPreviousMissing = na(typedString[1]) ? 1 : 0
typedStringDefault = typedString == "seed" ? 1 : 0
typedColorRed = color.r(typedColor)
typedColorGreen = color.g(typedColor)
typedColorBlue = color.b(typedColor)
typedColorPreviousRed = na(typedColor[1]) ? -1 : color.r(typedColor[1])
genericIntPrevious = na(genericInt[1]) ? -1 : genericInt[1]
genericFloatPrevious = na(genericFloat[1]) ? -1 : genericFloat[1]
genericBoolPrevious = genericBool[1] ? 1 : 0
genericStringLength = str.length(genericString)
genericStringPreviousLength = str.length(genericString[1])
genericStringPreviousMissing = na(genericString[1]) ? 1 : 0
genericStringDefault = genericString == "generic" ? 1 : 0
sourceCurrent = sourceSeries
sourcePrevious = na(sourceSeries[1]) ? -1 : sourceSeries[1]
genericSourceCurrent = genericSource
genericSourcePrevious = na(genericSource[1]) ? -1 : genericSource[1]

// The input named typedFloat is never reassigned.  This block declaration
// shadows it and mutates only the sibling local.
float shadowResult = 0.0
if bar_index % 2 == 0
    float typedFloat = close
    typedFloat += 1
    shadowResult := typedFloat
typedFloatValue = typedFloat

mutatedSibling += bar_index + 1
mutatedSiblingPrevious = na(mutatedSibling[1]) ? -1 : mutatedSibling[1]
// @pf-trace typedInt=typedInt
// @pf-trace typedIntPrevious=typedIntPrevious
// @pf-trace typedFloat=typedFloat
// @pf-trace typedFloatPrevious=typedFloatPrevious
// @pf-trace typedBool=typedBool ? 1 : 0
// @pf-trace typedBoolPrevious=typedBoolPrevious
// @pf-trace typedStringLength=typedStringLength
// @pf-trace typedStringPreviousLength=typedStringPreviousLength
// @pf-trace typedStringPreviousMissing=typedStringPreviousMissing
// @pf-trace typedStringDefault=typedStringDefault
// @pf-trace typedColorRed=typedColorRed
// @pf-trace typedColorGreen=typedColorGreen
// @pf-trace typedColorBlue=typedColorBlue
// @pf-trace typedColorPreviousRed=typedColorPreviousRed
// @pf-trace genericInt=genericInt
// @pf-trace genericIntPrevious=genericIntPrevious
// @pf-trace genericFloat=genericFloat
// @pf-trace genericFloatPrevious=genericFloatPrevious
// @pf-trace genericBool=genericBool ? 1 : 0
// @pf-trace genericBoolPrevious=genericBoolPrevious
// @pf-trace genericStringLength=genericStringLength
// @pf-trace genericStringPreviousLength=genericStringPreviousLength
// @pf-trace genericStringPreviousMissing=genericStringPreviousMissing
// @pf-trace genericStringDefault=genericStringDefault
// @pf-trace sourceCurrent=sourceCurrent
// @pf-trace sourcePrevious=sourcePrevious
// @pf-trace genericSourceCurrent=genericSourceCurrent
// @pf-trace genericSourcePrevious=genericSourcePrevious
// @pf-trace shadowResult=shadowResult
// @pf-trace typedFloatValue=typedFloatValue
// @pf-trace mutatedSibling=mutatedSibling
// @pf-trace mutatedSiblingPrevious=mutatedSiblingPrevious
"""

INPUT_TRACE_NAMES = (
    "typedInt",
    "typedIntPrevious",
    "typedFloat",
    "typedFloatPrevious",
    "typedBool",
    "typedBoolPrevious",
    "typedStringLength",
    "typedStringPreviousLength",
    "typedStringPreviousMissing",
    "typedStringDefault",
    "typedColorRed",
    "typedColorGreen",
    "typedColorBlue",
    "typedColorPreviousRed",
    "genericInt",
    "genericIntPrevious",
    "genericFloat",
    "genericFloatPrevious",
    "genericBool",
    "genericBoolPrevious",
    "genericStringLength",
    "genericStringPreviousLength",
    "genericStringPreviousMissing",
    "genericStringDefault",
    "sourceCurrent",
    "sourcePrevious",
    "genericSourceCurrent",
    "genericSourcePrevious",
    "shadowResult",
    "typedFloatValue",
    "mutatedSibling",
    "mutatedSiblingPrevious",
)


def _input_bars() -> list[Bar]:
    bars = []
    for index in range(SERIES_BARS):
        close = 100.0 + index * 0.75 + (index % 3) * 0.125
        opening = close - 0.25 - (index % 2) * 0.125
        bars.append(
            (
                START_MS + index * BAR_MS,
                opening,
                max(opening, close) + 0.5,
                min(opening, close) - 0.5,
                close,
                2000.0 + index,
            )
        )
    return bars


def _input_expected(
    bars: list[Bar],
    *,
    integer: int,
    floating: float,
    boolean: bool,
    string: str,
    color_red: int,
    color_green: int,
    color_blue: int,
    generic_integer: int,
    generic_floating: float,
    generic_boolean: bool,
    generic_string: str,
    source_index: int,
    generic_source_index: int,
    mutated: float,
) -> dict[str, list[object]]:
    close = [bar[4] for bar in bars]
    prior = lambda values, missing=-1: [missing, *values[:-1]]
    after_mutation = [mutated + index + 1 for index in range(len(bars))]
    return {
        "typedInt": [integer] * len(bars),
        "typedIntPrevious": prior([integer] * len(bars)),
        "typedFloat": [floating] * len(bars),
        "typedFloatPrevious": prior([floating] * len(bars)),
        "typedBool": [1 if boolean else 0] * len(bars),
        "typedBoolPrevious": [
            0,
            *([1 if boolean else 0] * (len(bars) - 1)),
        ],
        "typedStringLength": [len(string)] * len(bars),
        "typedStringPreviousLength": [0, *([len(string)] * (len(bars) - 1))],
        "typedStringPreviousMissing": [1, *([0] * (len(bars) - 1))],
        "typedStringDefault": [1 if string == "seed" else 0] * len(bars),
        "typedColorRed": [color_red] * len(bars),
        "typedColorGreen": [color_green] * len(bars),
        "typedColorBlue": [color_blue] * len(bars),
        "typedColorPreviousRed": prior([color_red] * len(bars)),
        "genericInt": [generic_integer] * len(bars),
        "genericIntPrevious": prior([generic_integer] * len(bars)),
        "genericFloat": [generic_floating] * len(bars),
        "genericFloatPrevious": prior([generic_floating] * len(bars)),
        "genericBool": [1 if generic_boolean else 0] * len(bars),
        "genericBoolPrevious": [
            0,
            *([1 if generic_boolean else 0] * (len(bars) - 1)),
        ],
        "genericStringLength": [len(generic_string)] * len(bars),
        "genericStringPreviousLength": [
            0,
            *([len(generic_string)] * (len(bars) - 1)),
        ],
        "genericStringPreviousMissing": [1, *([0] * (len(bars) - 1))],
        "genericStringDefault": [
            1 if generic_string == "generic" else 0
        ] * len(bars),
        "sourceCurrent": [values[source_index] for values in bars],
        "sourcePrevious": prior([values[source_index] for values in bars]),
        "genericSourceCurrent": [
            values[generic_source_index] for values in bars
        ],
        "genericSourcePrevious": prior(
            [values[generic_source_index] for values in bars]
        ),
        "shadowResult": [
            close[index] + 1 if index % 2 == 0 else 0
            for index in range(len(bars))
        ],
        "typedFloatValue": [floating] * len(bars),
        "mutatedSibling": after_mutation,
        "mutatedSiblingPrevious": prior(after_mutation),
    }


def test_untouched_inputs_keep_selected_values_and_history_against_mutated_shadow(
    native_engine: Path,
    tmp_path: Path,
) -> None:
    bars = _input_bars()
    feed = tmp_path / "inputs.csv"
    _write_feed(feed, bars)
    overrides = {
        "Typed int": "9",
        "Typed float": "-2.75",
        "Typed bool": "false",
        "Typed string": "changed",
        "Typed color": str(0xFF426180),
        "Generic int": "11",
        "Generic float": "4.5",
        "Generic bool": "false",
        "Generic string": "override",
        "Source input": "open",
        "Generic source": "high",
        "Mutated sibling": "3.25",
    }
    outcome = _run(
        native_engine,
        tmp_path / "input-runs",
        feed,
        "inputs",
        INPUT_SOURCE,
        overrides,
    )
    for tag in ("default", "override"):
        grouped = _frames(outcome.traces[tag], INPUT_TRACE_NAMES, bars)
        assert all(len(rows) == 1 for rows in grouped.values())
    expected = {
        "default": _input_expected(
            bars,
            integer=4,
            floating=1.25,
            boolean=True,
            string="seed",
            color_red=0x12,
            color_green=0x34,
            color_blue=0x56,
            generic_integer=4,
            generic_floating=2.5,
            generic_boolean=True,
            generic_string="generic",
            source_index=4,
            generic_source_index=4,
            mutated=2.0,
        ),
        "override": _input_expected(
            bars,
            integer=9,
            floating=-2.75,
            boolean=False,
            string="changed",
            color_red=0x42,
            color_green=0x61,
            color_blue=0x80,
            generic_integer=11,
            generic_floating=4.5,
            generic_boolean=False,
            generic_string="override",
            source_index=1,
            generic_source_index=2,
            mutated=3.25,
        ),
    }
    for tag, values in expected.items():
        for name, wanted in values.items():
            _assert_values(outcome, tag, name, bars, wanted)

    # Negative controls: a dropped input override makes these selected streams
    # equal across runs; a leaked block shadow changes typedFloatValue; and a
    # mutation of the untouched input changes its current or prior stream.
    for name in (
        "typedInt",
        "typedFloat",
        "typedBool",
        "typedStringLength",
        "typedColorRed",
        "genericInt",
        "genericFloat",
        "genericBool",
        "genericStringLength",
        "sourceCurrent",
        "genericSourceCurrent",
    ):
        assert _trace_values(outcome, "default", name, bars) != _trace_values(
            outcome, "override", name, bars
        )
    assert _trace_values(outcome, "default", "typedFloatValue", bars) != (
        _trace_values(outcome, "default", "shadowResult", bars)
    )

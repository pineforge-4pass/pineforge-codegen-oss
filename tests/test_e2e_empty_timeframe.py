"""Empty timeframe strings resolve after argument evaluation.

The complete reporter strategies pin the two original defects. The argument
matrix observes native values for inputs, variables, returns and helpers on
several chart periods, and counts evaluations of a stateful string argument.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path

import pytest

from tests._e2e import (
    Build, assert_same_runs, build_strategy_library, chart_feed_head,
    execute_all, ok, run_strategy, skip_unless_e2e_env, transpile_json,
)
from tests._requests import Feed


FIXTURES = Path(__file__).parent / "fixtures" / "empty_timeframe"
REPORTER_BARS = 1441
FIXED_SECONDS = {"1S": 1, "5": 300, "60": 3600, "D": 86400, "W": 604800}
ARGUMENTS = {
    "literal": "chart",
    "input": "input",
    "string_input": "input",
    "variable": "input",
    "temporary_name": "input",
    "parameter_name": "input",
    "occupied_escape": "explicit",
    "plain_variable": "chart",
    "persistent": "chart",
    "return": "chart",
    "return_input": "input",
    "period": "chart",
    "explicit": "explicit",
}
BUILTINS = {"S": "in_seconds", "C": "change"}
STYLES = ("pos", "named")


@pytest.fixture(scope="module")
def reporter_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("reporter_empty_timeframe")
    # A public corpus replay, distinct from the reporter's BTC date window.
    input_bars = REPORTER_BARS * 4
    expanded = chart_feed_head(engine, base, input_bars + 4)
    rows = expanded.read_text().splitlines()
    first = next(index for index, line in enumerate(rows[1:], 1)
                 if int(line.split(",", 1)[0]) % 3_600_000 == 0)
    selected = rows[first:first + input_bars]
    assert len(selected) == input_bars, "public feed has too few hourly chart inputs"
    feed = base / "aligned_15m.csv"
    feed.write_text("\n".join([rows[0], *selected]) + "\n")
    builds = {}
    for kind, function in (("seconds", "in_seconds"), ("change", "change")):
        source = (FIXTURES / f"reporter-{kind}.pine").read_text()
        original = f'timeframe.{function}("")'
        assert source.count(original) == 1
        for variant, argument in (("empty", '""'), ("period", "timeframe.period"),
                                  ("omitted", "")):
            builds[f"{kind}_{variant}"] = Build(
                source.replace(original, f"timeframe.{function}({argument})", 1),
                overrides={
                    "input_tf": "15", "script_tf": "60",
                    "runtime_overrides": {"currency": "USDT", "timezone": "UTC"},
                },
                trace=True,
            )
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("kind", ("seconds", "change"), ids=("in_seconds", "change"))
def test_reporter_empty_matches_chart_controls(kind, reporter_runs):
    subject = ok(reporter_runs, f"{kind}_empty")
    period = ok(reporter_runs, f"{kind}_period")
    omitted = ok(reporter_runs, f"{kind}_omitted")
    label = "secs" if kind == "seconds" else "chg"
    for tag, count, seconds in (("default", REPORTER_BARS * 4, 900.0),
                                ("override", REPORTER_BARS, 3600.0)):
        control = period.traces[tag]
        assert len(control) == count
        assert {row["name"] for row in control} == {label}
        assert [row["bar_index"] for row in control] == list(range(count))
        assert all(b["timestamp"] - a["timestamp"] == seconds * 1000
                   for a, b in zip(control, control[1:]))
        expected = ([seconds] * count if kind == "seconds"
                    else [0.0] + [1.0] * (count - 1))
        assert [row["value"] for row in control] == expected
    assert_same_runs(omitted, period)
    assert_same_runs(subject, period)


def _clock_stamps(period):
    epoch = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    if period == "M":
        return [int(datetime(2024, month, 1, tzinfo=timezone.utc).timestamp() * 1000)
                for month in range(1, 6)]
    return [epoch + index * FIXED_SECONDS[period] * 1000 for index in range(33)]


@pytest.fixture(scope="module")
def argument_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("empty_timeframe_arguments")
    pine = FIXTURES / "arguments.pine"
    source = pine.read_text()
    result = transpile_json(pine)
    assert result.get("ok"), result.get("diagnostics")
    build_strategy_library(result["cpp"], base)
    expected_names = {line.split()[2].split("=", 1)[0]
                      for line in source.splitlines() if line.startswith("// @pf-trace ")}
    outcomes = {}
    for period in (*FIXED_SECONDS, "M"):
        stamps = _clock_stamps(period)
        last_close = (
            int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
            if period == "M" else stamps[-1] + stamps[-1] - stamps[-2]
        )
        bars = [(stamp, close_stamp, 100.0 + index)
                for index, (stamp, close_stamp)
                in enumerate(zip(stamps, [*stamps[1:], last_close]))]
        feed = base / f"clock_{period}.csv"
        feed.write_bytes(Feed("BINANCE:BTCUSDT", period, bars).csv())
        for argument in ("", "240"):
            tag = f"{period}_{'empty' if not argument else 'explicit'}"
            overrides = {
                "input_tf": period, "script_tf": period,
                "Frame input": argument, "String input": argument,
                "runtime_overrides": {
                    "currency": "USDT", "timezone": "UTC", "session": "24x7",
                },
            }
            _trades, records, stderr = run_strategy(
                engine, base, feed, overrides, tag, trace=True,
            )
            (base / f"run_{tag}.stderr").write_text(stderr)
            columns = {}
            for row in records:
                columns.setdefault(row["name"], []).append(row)
            assert set(columns) == expected_names, f"{tag}: missing native trace names"
            for name, series in columns.items():
                assert len(series) == len(stamps), (tag, name, "missing native calls")
                assert [row["bar_index"] for row in series] == list(range(len(stamps)))
                assert [row["timestamp"] for row in series] == stamps
            outcomes[period, argument] = {
                name: [row["value"] for row in series] for name, series in columns.items()
            }
    return outcomes


def _expected(frame, stamps, chart_seconds):
    if frame == "chart":
        return [chart_seconds] * len(stamps), [0.0] + [1.0] * (len(stamps) - 1)
    assert frame == "explicit"
    size = 240 * 60 * 1000
    return ([14400.0] * len(stamps),
            [0.0] + [float(now // size != before // size)
                     for before, now in zip(stamps, stamps[1:])])


@pytest.mark.parametrize("builtin", BUILTINS, ids=("in_seconds", "change"))
@pytest.mark.parametrize("name", ARGUMENTS)
@pytest.mark.parametrize("style", STYLES)
def test_empty_argument_source_uses_the_evaluated_string(builtin, name, style, argument_runs):
    for (period, argument), columns in argument_runs.items():
        stamps = _clock_stamps(period)
        chart_seconds = columns["S_period_pos"][0]
        assert math.isfinite(chart_seconds)
        if period in FIXED_SECONDS:
            assert chart_seconds == FIXED_SECONDS[period]
        # Calendar-only engine periods retain their existing conversion;
        # equality to the period control is the empty-argument contract.
        assert columns["S_period_pos"] == [chart_seconds] * len(stamps)
        assert columns["C_period_pos"] == [0.0] + [1.0] * (len(stamps) - 1)
        resolution = ARGUMENTS[name]
        frame = ("explicit" if resolution == "explicit"
                 or (resolution == "input" and argument) else "chart")
        seconds, changes = _expected(frame, stamps, chart_seconds)
        assert columns[f"{builtin}_{name}_{style}"] == (
            seconds if builtin == "S" else changes
        ), (period, argument, builtin, name, style)


@pytest.mark.parametrize("builtin", BUILTINS, ids=("in_seconds", "change"))
@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("body", ("helper", "stateful"))
def test_each_helper_body_preserves_empty_and_explicit_arguments(builtin, style, body, argument_runs):
    for (period, argument), columns in argument_runs.items():
        stamps = _clock_stamps(period)
        for suffix, frame in (("empty", "chart"), ("explicit", "explicit")):
            seconds, changes = _expected(frame, stamps, columns["S_period_pos"][0])
            assert columns[f"{builtin}_{body}_{suffix}_{style}"] == (
                seconds if builtin == "S" else changes
            ), (period, argument, builtin, style, body, suffix)


def test_omitted_arguments_keep_the_existing_chart_resolution(argument_runs):
    for columns in argument_runs.values():
        for builtin in BUILTINS:
            assert columns[f"{builtin}_omitted"] == columns[f"{builtin}_period_pos"]


def test_change_evaluates_each_string_once_and_can_switch_to_explicit(argument_runs):
    for (period, argument), columns in argument_runs.items():
        stamps = _clock_stamps(period)
        _, chart = _expected("chart", stamps, columns["S_period_pos"][0])
        _, explicit = _expected("explicit", stamps, columns["S_period_pos"][0])
        dynamic = [chart[index] if index % 2 == 0 else explicit[index]
                   for index in range(len(stamps))]
        assert columns["C_dynamic_pos"] == dynamic
        assert columns["C_dynamic_named"] == dynamic
        assert columns["C_once_pos"] == (explicit if argument else chart)
        assert columns["C_once_named"] == (explicit if argument else chart)
        assert columns["callCount"] == [2.0 * (index + 1) for index in range(len(stamps))]

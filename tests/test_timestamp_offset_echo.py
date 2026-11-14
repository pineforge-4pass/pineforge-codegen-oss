"""Current-engine rejected-offset echoes; calendar-form TradingView parity is open."""

import json

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests.test_timestamp_rejected_zone import MISSPELLED_ZONE_MESSAGE
from tests.test_timestamp_strategy_stop import (
    _STRATEGY_DRIVER, _STRATEGY_SOURCE, _paired_engine_sha,
)


@pytest.mark.parametrize("raw_zone, echoed_zone", [
    # Current-engine observations, not TradingView calendar expectations.
    ("UTC+24:00", "UTC+24:00"),
    ("GMT-24", "GMT-24"),
])
def test_rejected_offset_echo_observes_current_behavior(raw_zone, echoed_zone):
    """Require the raw echo and stop before post-call writes on bar 1."""
    driver = _STRATEGY_DRIVER
    input_overrides = (
        (
            'strategy.set_input("Zone", "America/Newyork");',
            f'strategy.set_input("Zone", {json.dumps(raw_zone)});',
        ),
        (
            'strategy.set_input("Start bar", "2");',
            'strategy.set_input("Start bar", "1");',
        ),
        (
            'strategy.set_input("Day", "4");',
            'strategy.set_input("Day", "2");',
        ),
    )
    for anchor, replacement in input_overrides:
        assert driver.count(anchor) == 1, anchor
        driver = driver.replace(anchor, replacement, 1)

    # The source's UTC default cannot satisfy these rejected-input assertions.
    output = run_emitted_tu(
        transpile(_STRATEGY_SOURCE), driver,
        opt="-O2", label="timestamp rejected offset echo",
    )
    print(output, end="")
    lines = output.splitlines()
    assert lines[:2] == ["ARM typed", f"HEADER_ENGINE {_paired_engine_sha()}"], output
    assert "ARM fallback" not in output, output

    keys = (
        "STATUS", "CODE", "NAME", "ARGS", "TEXT",
        "ENTERED", "COMPLETED", "LAST", "EFFECT", "STAMP",
    )
    assert len(lines) == len(keys) + 2, output
    row = {}
    for expected, line in zip(keys, lines[2:]):
        key, separator, value = line.partition(" ")
        assert separator and key == expected, output
        row[key] = value

    assert row["STATUS"] == "1", output
    assert row["CODE"] == "88", output
    assert row["NAME"] == "pine_timezone_error", output
    arguments = json.loads(row["ARGS"])
    assert type(arguments) is dict, output
    assert arguments == {
        "bar_index": 1, "function": "timestamp", "zone": echoed_zone,
    }, output
    assert type(arguments["bar_index"]) is int, output
    original_quoted_zone = '"America/Newyork"'
    assert MISSPELLED_ZONE_MESSAGE.count(original_quoted_zone) == 1
    expected_message = MISSPELLED_ZONE_MESSAGE.replace(
        original_quoted_zone, f'"{echoed_zone}"', 1,
    )
    assert row["TEXT"] == "Error on bar 1: " + expected_message, output

    assert row["ENTERED"] == "2", output
    assert row["COMPLETED"] == "1", output
    assert row["LAST"] == "0", output
    # Only bar 0 completes: Day 2 + bar_index 0 + 1 = 3.
    assert row["EFFECT"] == "3", output
    # January 2, 1970 at 08:00 UTC: one day plus eight hours after epoch.
    assert row["STAMP"] == "115200000", output

"""A typed timestamp failure stops the generated strategy before later effects."""

import json
import re

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._compile import run_emitted_tu
from tests.test_timestamp_rejected_zone import MISSPELLED_ZONE_MESSAGE


def _paired_engine_sha() -> str:
    """Read the version header with run_emitted_tu's include precedence."""
    assert compile_env._ENGINE_INC is not None, "Engine include is unresolved"
    header = compile_env._ENGINE_INC / "pineforge/version.h"
    if not header.is_file():
        assert compile_env._GENERATED_INC is not None, (
            "Engine version header is unresolved"
        )
        header = compile_env._GENERATED_INC / "pineforge/version.h"
    definitions = re.findall(
        r"^[ \t]*#[ \t]*define[ \t]+PINEFORGE_GIT_SHA\b([^\r\n]*)",
        header.read_text(encoding="utf-8"), re.MULTILINE,
    )
    assert len(definitions) == 1, f"{header}: expected one PINEFORGE_GIT_SHA define"
    identity = re.fullmatch(r'[ \t]+"([0-9a-f]{7,40})"[ \t]*', definitions[0])
    assert identity is not None, (
        f"{header}: malformed or unknown PINEFORGE_GIT_SHA"
    )
    return identity.group(1)


_STRATEGY_SOURCE = '''//@version=6
strategy("Timestamp strategy stop")
zoneInput = input.string("UTC", "Zone")
startBar = input.int(0, "Start bar")
dayInput = input.int(2, "Day")
var int entered = 0
var int completed = 0
var int lastCompleted = -1
var int effect = 0
var int stampValue = 0
runtimeZone = bar_index < startBar ? "UTC" : zoneInput
entered += 1
stampValue := timestamp(runtimeZone, 1970, 1, dayInput, 8, 0, 0)
completed += 1
lastCompleted := bar_index
effect += dayInput + bar_index + 1
'''

_STRATEGY_DRIVER = r'''
#include <iostream>
int main() {
#ifndef PINEFORGE_HAS_TIMEZONE_STOP_V1
    std::cout << "ARM fallback\n";
    return 80;
#else
    std::cout << "ARM typed\n";
    std::cout << "HEADER_ENGINE " << PINEFORGE_GIT_SHA << "\n";
    GeneratedStrategy strategy;
    strategy.set_input("Zone", "America/Newyork");
    strategy.set_input("Start bar", "2");
    strategy.set_input("Day", "4");
    Bar bars[] = {
        {7, 7, 7, 7, 1, 0}, {11, 11, 11, 11, 1, 60000},
        {19, 19, 19, 19, 1, 120000}, {23, 23, 23, 23, 1, 180000},
        {29, 29, 29, 29, 1, 240000},
    };
    strategy.run(bars, 5, "1", "1");
    const auto failure = pineforge::run_failure_value_of(strategy);
    std::cout << "STATUS " << strategy.last_run_status() << "\n"
              << "CODE " << static_cast<unsigned>(failure.code) << "\n"
              << "NAME " << pineforge::run_failure_code_of(strategy) << "\n"
              << "ARGS " << pineforge::run_failure_args_of(strategy) << "\n"
              << "TEXT " << strategy.last_error() << "\n"
              << "ENTERED " << strategy.entered << "\n"
              << "COMPLETED " << strategy.completed << "\n"
              << "LAST " << strategy.lastCompleted << "\n"
              << "EFFECT " << strategy.effect << "\n"
              << "STAMP " << strategy.stampValue << "\n";
    return 0;
#endif
}
'''


def test_generated_strategy_stops_before_post_call_effect():
    """Require the typed failure and no post-call writes on the failing bar."""
    output = run_emitted_tu(
        transpile(_STRATEGY_SOURCE), _STRATEGY_DRIVER,
        opt="-O2", label="timestamp typed strategy stop",
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
        "bar_index": 2, "function": "timestamp", "zone": "America/Newyork",
    }, output
    assert type(arguments["bar_index"]) is int, output
    assert row["TEXT"] == "Error on bar 2: " + MISSPELLED_ZONE_MESSAGE, output

    assert row["ENTERED"] == "3", output
    assert row["COMPLETED"] == "2", output
    assert row["LAST"] == "1", output
    # Only bars 0 and 1 complete: (4 + 0 + 1) + (4 + 1 + 1) = 11.
    assert row["EFFECT"] == "11", output
    # January 4, 1970 at 08:00 UTC: three days plus eight hours after epoch.
    assert row["STAMP"] == "288000000", output

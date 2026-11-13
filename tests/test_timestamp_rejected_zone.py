"""Rejected timestamp zones throw before conversion or memoization."""

import json
import re

import pytest

from tests._compile import run_emitted_tu
from tests.test_timezone_lambda_validation import _render_lambdas


MISSPELLED_ZONE_MESSAGE = (
    'Incorrect `timezone` value for the timestamp() function: "America/Newyork". '
    'The value must be a string in either UTC/GMT notation '
    '(e.g. "UTC-5", "GMT+0530") or an IANA time zone database name '
    '(e.g. "America/New_York").'
)

# An engine that defines the typed timezone stop (PINEFORGE_HAS_TIMEZONE_STOP_V1)
# puts its own bar prefix before the text that the generated code passes:
# "Error on bar N: " while a bar is running, "Error before the first bar: " when
# none is. The generated code passes the text without it. The driver prints which
# arm it was built with; only in the typed arm does a thrown line drop exactly one
# of these two forms from its start, and in the fallback arm the text is compared
# as it is.
_ENGINE_BAR_PREFIX = re.compile(r"Error on bar [0-9]+: |Error before the first bar: ")

_ARM_MARKER = r'''
#ifdef PINEFORGE_HAS_TIMEZONE_STOP_V1
static const char* const arm_marker = "ARM typed\n";
#else
static const char* const arm_marker = "ARM fallback\n";
#endif
'''


def _drop_engine_prefix(text: str) -> str:
    """Drop one engine bar prefix from the start of a text, and nothing else."""
    match = _ENGINE_BAR_PREFIX.match(text)
    return text[match.end():] if match else text


def _compare_form(output: str) -> str:
    """Remove the arm marker line and, in the typed arm only, the engine prefixes."""
    marker, _, body = output.partition("\n")
    assert marker in ("ARM typed", "ARM fallback"), marker
    if marker == "ARM fallback":
        return body
    lines = []
    for line in body.splitlines():
        if line.startswith("THROWN "):
            line = "THROWN " + _drop_engine_prefix(line[len("THROWN "):])
        lines.append(line + "\n")
    return "".join(lines)


def _execute_timestamp(
    zone: str,
    calendar: tuple[int, ...] = (1970, 1, 2, 8, 0, 0),
    *,
    exchange: bool = False,
    repetitions: int = 1,
) -> str:
    """Evaluate one emitted call site and observe its throws and environment writes."""
    cpp, bodies = _render_lambdas(zone, calendar)
    driver = r'''
#include <exception>
#include <iostream>
static int observed_writes = 0;
static void prime_timezone() {
    ::setenv("TZ", "UTC", 1);
    ::tzset();
}
static int observe_setenv(const char* name, const char* value, int overwrite) {
    ++observed_writes;
    return ::setenv(name, value, overwrite);
}
#define setenv observe_setenv
int main() {
    std::cout << arm_marker;
    prime_timezone();
    struct { std::string timezone; } syminfo_{ZONE};
    auto evaluate = [&]() -> int64_t { return (TIMESTAMP_LAMBDA); };
    for (int attempt = 0; attempt < REPETITIONS; ++attempt) {
        try {
            const auto value = evaluate();
            std::cout << "VALUE " << value << "\n";
        } catch (const std::exception& error) {
            std::cout << "THROWN " << error.what() << "\n";
        }
    }
    std::cout << "WRITES " << observed_writes << "\n";
}
#undef setenv
'''
    driver = driver.replace("ZONE", f"std::string({json.dumps(zone)})")
    driver = driver.replace("TIMESTAMP_LAMBDA", bodies[1 if exchange else 0])
    driver = driver.replace("REPETITIONS", str(repetitions))
    # The marker is added after the replacements: its macro name contains "ZONE".
    output = run_emitted_tu(
        cpp, _ARM_MARKER + driver, opt="-O0", label="timestamp rejected zone",
    )
    return _compare_form(output)


@pytest.mark.parametrize("zone", [
    "America/Newyork",
    "Invalid/Timezone",
    "/usr/share/zoneinfo/Asia/Taipei",
    "../UTC",
    "UTC+24:00",
])
def test_rejected_explicit_zone_throws(zone):
    """Pin a runtime error instead of a silent UTC value for rejected zone names."""
    output = _execute_timestamp(zone)
    lines = output.splitlines()
    assert lines[0].startswith(
        'THROWN Incorrect `timezone` value for the timestamp() function: "'
    )
    if zone == "America/Newyork":
        assert lines[0] == f"THROWN {MISSPELLED_ZONE_MESSAGE}"
    assert lines[1:] == ["WRITES 0"]


def test_rejected_exchange_zone_throws():
    """Pin the same complete error for a rejected exchange timezone."""
    assert _execute_timestamp("America/Newyork", exchange=True) == (
        f"THROWN {MISSPELLED_ZONE_MESSAGE}\nWRITES 0\n"
    )


@pytest.mark.parametrize("exchange", [False, True], ids=["explicit", "exchange"])
def test_rejected_tuple_is_not_memoized(exchange):
    """Pin two throws from repeated evaluation of the same rejected call site."""
    assert _execute_timestamp(
        "America/Newyork", exchange=exchange, repetitions=2,
    ) == (
        f"THROWN {MISSPELLED_ZONE_MESSAGE}\n"
        f"THROWN {MISSPELLED_ZONE_MESSAGE}\nWRITES 0\n"
    )


@pytest.mark.parametrize("exchange", [False, True], ids=["explicit", "exchange"])
@pytest.mark.parametrize("calendar, expected", [
    ((2024, 1, 15, 8, 0, 0), "VALUE 1705323600000\nWRITES 2\n"),
    ((2024, 7, 15, 8, 0, 0), "VALUE 1721044800000\nWRITES 2\n"),
])
def test_accepted_zone_preserves_value_and_environment_writes(exchange, calendar, expected):
    """Pin New York winter and summer values and their two environment writes."""
    assert _execute_timestamp(
        "America/New_York", calendar, exchange=exchange,
    ) == expected


@pytest.mark.parametrize("exchange", [False, True], ids=["explicit", "exchange"])
@pytest.mark.parametrize("zone", ["", "UTC", "Etc/UTC"])
def test_utc_shortcuts_preserve_value_without_environment_writes(exchange, zone):
    """Pin UTC conversion and zero environment writes for all three shortcuts."""
    assert _execute_timestamp(zone, exchange=exchange) == "VALUE 115200000\nWRITES 0\n"


@pytest.mark.parametrize("prefix", [
    "Error on bar 0: ", "Error on bar 12345: ", "Error before the first bar: ",
], ids=["bar-zero", "bar-number", "before-the-first-bar"])
def test_the_two_engine_prefix_forms_are_dropped(prefix):
    assert _drop_engine_prefix(prefix + MISSPELLED_ZONE_MESSAGE) == MISSPELLED_ZONE_MESSAGE


@pytest.mark.parametrize("prefix", [
    "Warning on bar 7: ",
    "Error on bar x: ",
    "Error on bar : ",
    "Error on bar 7 ",
    "error on bar 7: ",
    "Error: ",
    " Error on bar 7: ",
    "Runtime Error on bar 7: ",
], ids=[
    "other-word", "bar-not-digits", "no-bar-number", "no-colon", "other-case",
    "no-bar", "not-at-the-start", "text-before-it",
])
def test_another_prefix_is_not_dropped(prefix):
    text = prefix + MISSPELLED_ZONE_MESSAGE
    assert _drop_engine_prefix(text) == text


def test_only_one_prefix_is_dropped():
    second = "Error on bar 4: " + MISSPELLED_ZONE_MESSAGE
    assert _drop_engine_prefix("Error on bar 3: " + second) == second


def test_the_prefix_is_dropped_only_in_the_typed_arm():
    thrown = "THROWN Error on bar 3: " + MISSPELLED_ZONE_MESSAGE + "\nWRITES 0\n"
    assert _compare_form("ARM typed\n" + thrown) == (
        "THROWN " + MISSPELLED_ZONE_MESSAGE + "\nWRITES 0\n"
    )
    assert _compare_form("ARM fallback\n" + thrown) == thrown

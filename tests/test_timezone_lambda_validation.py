"""Calendar lambdas validate timezones before writing the environment."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import sys

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.tables import tz_time_field_lambda
from pineforge_codegen.codegen.visit_call import _timestamp_calendar_lambda
from pineforge_codegen.cpp_allowlist import check_generated_cpp


HEADER = '//@version=6\nstrategy("Timezone validation")\n'

# Timestamp fields are 1970-01-02 08:00; field extraction reads 08:00 UTC.
ZONE_CASES = [
    # Taipei is UTC+8: 24h in ms, hour 16; four conversions each set and restore TZ.
    ("Asia/Taipei", "86400000 86400000 16 16 8 UTC\n"),
    # UTC+05:30: 26.5h in ms, hour 13; four accepted conversions write TZ twice.
    ("UTC+05:30", "95400000 95400000 13 13 8 UTC\n"),
    # January uses EST (UTC-5): 37h in ms, hour 3; each conversion restores TZ.
    ("EST5EDT,M3.2.0/2,M11.1.0/2", "133200000 133200000 3 3 8 UTC\n"),
    # Empty selects UTC: 32h in ms, hour 8, and no environment writes.
    ("", "115200000 115200000 8 8 0 UTC\n"),
    # An unknown name falls back to UTC without exposing that name to setenv.
    ("Invalid/Timezone", "115200000 115200000 8 8 0 UTC\n"),
    # An absolute tzfile path is rejected: 32h in ms, hour 8, and no writes.
    ("/usr/share/zoneinfo/Asia/Taipei", "115200000 115200000 8 8 0 UTC\n"),
    # Parent traversal is rejected before libc: the UTC fields and TZ stay intact.
    ("../UTC", "115200000 115200000 8 8 0 UTC\n"),
    # A 24-hour offset exceeds the accepted range and takes the UTC fallback.
    ("UTC+24:00", "115200000 115200000 8 8 0 UTC\n"),
    # UTC retains its short-circuit path: 32h in ms, hour 8, no writes.
    ("UTC", "115200000 115200000 8 8 0 UTC\n"),
    # Etc/UTC retains the same short-circuit conversion and untouched TZ.
    ("Etc/UTC", "115200000 115200000 8 8 0 UTC\n"),
]

# Each instant is 08:00 UTC on the calendar date; stamps use 08:00 local time.
ACCEPTED_ZONE_CASES = [
    # UTC+3 flips its sign to UTC-3: 32h - 3h = 29h; 08:00 UTC becomes 11:00.
    ("UTC+3", "UTC-3", (1970, 1, 2, 8, 0, 0), 115200000,
     "104400000 104400000 11 11 8 UTC\n", ()),
    # UTC-5 flips its sign to UTC+5: 32h + 5h = 37h; 08:00 UTC becomes 03:00.
    ("UTC-5", "UTC+5", (1970, 1, 2, 8, 0, 0), 115200000,
     "133200000 133200000 3 3 8 UTC\n", ()),
    # GMT+8 becomes UTC-8: 32h - 8h = 24h; 08:00 UTC becomes 16:00.
    ("GMT+8", "UTC-8", (1970, 1, 2, 8, 0, 0), 115200000,
     "86400000 86400000 16 16 8 UTC\n", ()),
    # GMT+0530 splits 05/30, drops the hour's zero and flips to UTC-5:30: 26.5h.
    ("GMT+0530", "UTC-5:30", (1970, 1, 2, 8, 0, 0), 115200000,
     "95400000 95400000 13 13 8 UTC\n", ()),
    # New York stays unchanged; January 15 is EST (UTC-5): UTC 08:00 + 5h.
    ("America/New_York", "America/New_York", (2024, 1, 15, 8, 0, 0),
     1705305600000, "1705323600000 1705323600000 3 3 8 UTC\n",
     ("America/New_York",)),
    # July 15 is between March's second and November's first Sunday: EDT, UTC-4.
    ("America/New_York", "America/New_York", (2024, 7, 15, 8, 0, 0),
     1721030400000, "1721044800000 1721044800000 4 4 8 UTC\n",
     ("America/New_York",)),
    # London stays unchanged; January 15 is GMT (UTC+0): UTC 08:00 + 0h.
    ("Europe/London", "Europe/London", (2024, 1, 15, 8, 0, 0),
     1705305600000, "1705305600000 1705305600000 8 8 8 UTC\n",
     ("Europe/London",)),
    # July 15 is between March's and October's last Sundays: BST, UTC+1.
    ("Europe/London", "Europe/London", (2024, 7, 15, 8, 0, 0),
     1721030400000, "1721026800000 1721026800000 9 9 8 UTC\n",
     ("Europe/London",)),
    # EST5EDT stays unchanged; January uses EST, UTC-5, with US default rules.
    ("EST5EDT", "EST5EDT", (2024, 1, 15, 8, 0, 0),
     1705305600000, "1705323600000 1705323600000 3 3 8 UTC\n",
     ("EST5EDT", "posixrules")),
    # US default rules put July between March's second and November's first Sunday.
    ("EST5EDT", "EST5EDT", (2024, 7, 15, 8, 0, 0),
     1721030400000, "1721044800000 1721044800000 4 4 8 UTC\n",
     ("EST5EDT", "posixrules")),
]


def _render_lambdas(
    zone: str, calendar: tuple[int, ...] = (1970, 1, 2, 8, 0, 0),
    instant: int = 115200000,
) -> tuple[str, list[str]]:
    literal = json.dumps(zone)
    calendar_fields = ", ".join(map(str, calendar))
    cpp = transpile(
        HEADER
        + f"explicitStamp = timestamp({literal}, {calendar_fields})\n"
        + f"exchangeStamp = timestamp({calendar_fields})\n"
    )
    calendars = re.findall(
        r"\[&\]\(\) -> int64_t \{ .*?return _res; \}\(\)", cpp, re.DOTALL,
    )
    assert len(calendars) == 2  # Each timestamp overload emits one calendar lambda.
    # Time fields now use engine helpers; exercise both retained template forms.
    fields = [
        tz_time_field_lambda(
            "tm_buf.tm_hour", f"{instant}LL", f"std::string({literal})",
        ),
        tz_time_field_lambda(
            "tm_buf.tm_hour", f"{instant}LL", "syminfo_.timezone",
        ),
    ]
    return cpp, calendars + fields


def _assert_guarded(lambdas: list[str]) -> None:
    for body in lambdas:
        # Rejection joins the UTC branch, so it cannot enter the environment block.
        assert (
            'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC" || '
            '!pineforge::native_calendar::timezone_accepted(_tz)) { '
        ) in body
        # The first write is the script zone; the later write restores the old TZ.
        assert body.index(
            "pineforge::native_calendar::timezone_accepted(_tz)"
        ) < body.index('std::getenv("TZ")') < body.index(
            '::setenv("TZ", _tz.c_str(), 1)'
        )
        if "-> int64_t" in body:
            # Rejected calendar fields use the existing UTC conversion.
            assert "_res = (int64_t)timegm(&t) * 1000; } else { " in body
        else:
            # Rejected time fields decompose the instant in UTC.
            assert "gmtime_r(&_secs, &tm_buf); } else { " in body


@pytest.mark.parametrize("zone", [
    *[zone for zone, _expected in ZONE_CASES],
    *sorted({row[0] for row in ACCEPTED_ZONE_CASES}),
])
def test_zone_classes_guard_both_lambdas_and_overloads(zone):
    cpp, lambdas = _render_lambdas(zone)
    _assert_guarded(lambdas)
    # The class's actual string reaches both explicit forms before validation.
    explicit_prefix = (
        "std::string _tz = pineforge::normalize_timezone_for_posix("
        f"(std::string({json.dumps(zone)}))); "
    )
    assert explicit_prefix in lambdas[0]
    assert explicit_prefix in lambdas[2]
    # The other overloads read the exchange zone, rather than a literal substitute.
    exchange_prefix = (
        "std::string _tz = pineforge::normalize_timezone_for_posix("
        "(syminfo_.timezone)); "
    )
    assert exchange_prefix in lambdas[1]
    assert exchange_prefix in lambdas[3]
    # The existing sealed environment runs remain valid for the C++ allowlist.
    assert check_generated_cpp(cpp) == []
    for body in lambdas[2:]:
        assert check_generated_cpp(body) == []


def test_timezone_acceptance_precedes_first_environment_write():
    bodies = [
        _timestamp_calendar_lambda("read_zone()", "1970", "1", "2", "8", "0", "0"),
        tz_time_field_lambda("tm_buf.tm_hour", "read_instant()", "read_zone()"),
    ]
    for body in bodies:
        # A dynamic zone is evaluated once, then checked before either TZ write.
        assert body.count("read_zone()") == 1
        assert "pineforge::native_calendar::timezone_accepted(_tz)" in body
        assert body.index("read_zone()") < body.index(
            "pineforge::native_calendar::timezone_accepted(_tz)"
        ) < body.index('::setenv("TZ",')


@pytest.mark.parametrize("zone", [
    "",  # Empty satisfies the first UTC term without calling the validator.
    "UTC",  # UTC satisfies the second term without calling the validator.
    "Etc/UTC",  # Etc/UTC satisfies the third term without calling the validator.
])
def test_utc_short_circuit_stays_before_timezone_acceptance(zone):
    _cpp, bodies = _render_lambdas(zone)
    for body in bodies:
        # The original three terms still short-circuit before the new last term.
        assert (
            'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC" || '
            '!pineforge::native_calendar::timezone_accepted(_tz)) { '
        ) in body


def _skip_unless_zoneinfo(alternatives: tuple[str, ...]) -> None:
    """Require one backing TZif file without consulting timezone acceptance."""
    if not alternatives:
        return
    # Match the engine's effective root, including glibc's exclusive TZDIR.
    if sys.platform == "darwin":
        root = Path("/var/db/timezone/zoneinfo")
        if not root.is_dir():
            root = Path("/usr/share/zoneinfo")
    elif platform.libc_ver()[0] == "glibc":
        root = Path(os.environ.get("TZDIR") or "/usr/share/zoneinfo")
    else:
        root = Path("/usr/share/zoneinfo")
    for name in alternatives:
        try:
            with (root / name).open("rb") as stream:
                if stream.read(4) == b"TZif":
                    return
        except OSError:
            continue
    pytest.skip(
        f"zoneinfo prerequisite missing or not TZif: "
        f"{' or '.join(alternatives)} under {root}"
    )


def _execute_lambdas(cpp: str, bodies: list[str], zone: str) -> tuple[str, str]:
    from tests._compile import run_emitted_tu

    # Observe the actual emitted expressions while forwarding every write to libc.
    driver = r'''
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
    prime_timezone();
    struct { std::string timezone; } syminfo_{ZONE};
    std::cout << pineforge::normalize_timezone_for_posix(syminfo_.timezone) << "\n";
    auto explicit_stamp = (EXPLICIT_STAMP);
    auto exchange_stamp = (EXCHANGE_STAMP);
    auto explicit_hour = (EXPLICIT_HOUR);
    auto exchange_hour = (EXCHANGE_HOUR);
    std::cout << explicit_stamp << " " << exchange_stamp << " "
              << explicit_hour << " " << exchange_hour << " "
              << observed_writes << " " << std::getenv("TZ") << "\n";
}
#undef setenv
'''
    driver = driver.replace("ZONE", f"std::string({json.dumps(zone)})")
    for marker, body in zip(
        ("EXPLICIT_STAMP", "EXCHANGE_STAMP", "EXPLICIT_HOUR", "EXCHANGE_HOUR"),
        bodies,
    ):
        driver = driver.replace(marker, body)
    normalized, values = run_emitted_tu(
        cpp, driver, opt="-O0", label="timezone validation",
    ).split("\n", 1)
    return normalized, values


@pytest.mark.parametrize("zone, expected", ZONE_CASES)
def test_timezone_values_and_environment_writes_in_engine(zone, expected):
    cpp, bodies = _render_lambdas(zone)
    _assert_guarded(bodies)
    _skip_unless_zoneinfo(("Asia/Taipei",) if zone == "Asia/Taipei" else ())
    _normalized, values = _execute_lambdas(cpp, bodies, zone)
    assert values == expected


@pytest.mark.parametrize(
    "zone, normalized, calendar, instant, expected, zoneinfo",
    ACCEPTED_ZONE_CASES,
)
def test_accepted_timezone_values_and_normalization_in_engine(
    zone, normalized, calendar, instant, expected, zoneinfo,
):
    cpp, bodies = _render_lambdas(zone, calendar, instant)
    _assert_guarded(bodies)
    _skip_unless_zoneinfo(zoneinfo)
    actual_normalized, values = _execute_lambdas(cpp, bodies, zone)
    assert actual_normalized == normalized
    assert values == expected

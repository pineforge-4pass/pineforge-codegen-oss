"""Calendar values and int64 consumers of full English month names."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.visit_call import _parse_pine_datestring_ms


MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
HEADER = '//@version=6\nstrategy("Month names", currency=currency.NONE)\n'
REPORTER = """//@version=6
strategy('repro')
start = timestamp('01 February 2024 00:00')
if time >= start and bar_index % 20 == 0
    strategy.entry('Long', strategy.long)
if bar_index % 20 == 10
    strategy.close('Long')
"""


def _calendar_ms(fields, offset_minutes=0):
    """Independent numeric calendar arithmetic; never parse the date string."""
    zone = timezone(timedelta(minutes=offset_minutes))
    delta = datetime(*fields, tzinfo=zone) - EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000


def _month_case(month, *, full):
    # Distinct years, days and times expose an ignored input or a fixed epoch.
    fields = (1960 + month * 7, month, 8 + month,
              month % 24, month * 3 % 60, month * 7 % 60)
    year, _, day, hour, minute, second = fields
    name = MONTHS[month - 1] if full else MONTHS[month - 1][:3]
    literal = f"{day:02} {name} {year} {hour:02}:{minute:02}:{second:02}"
    return literal, fields


def _emitted_ms(cpp, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(-?\d+)LL\s*;", cpp)
    assert match is not None, f"No folded int64 timestamp assigned to {name}"
    return int(match.group(1))


def test_reporter_full_month_strategy():
    cpp = transpile(REPORTER)
    assert _emitted_ms(cpp, "start") == _calendar_ms((2024, 2, 1, 0, 0, 0))


@pytest.mark.parametrize("literal, fields, offset", [
    ("01 Feb 2024 00:00 +0000", (2024, 2, 1, 0, 0, 0), 0),
    ("Jul 20 2021 12:34:56 +0530", (2021, 7, 20, 12, 34, 56), 330),
    ("03 Nov 2031 02:17 -0430", (2031, 11, 3, 2, 17, 0), -270),
    ("2025-01-01T00:00:00", (2025, 1, 1, 0, 0, 0), 0),
    ("2020-02-29T23:59:58+05:30", (2020, 2, 29, 23, 59, 58), 330),
    ("1 Jan 2020 09:30 GMT+2", (2020, 1, 1, 9, 30, 0), 120),
    ("1969-12-31 23:59:59 UTC", (1969, 12, 31, 23, 59, 59), 0),
])
def test_existing_datestring_values(literal, fields, offset):
    expected = _calendar_ms(fields, offset)
    assert _parse_pine_datestring_ms(literal) == expected
    cpp = transpile(HEADER + f'probeStamp = timestamp("{literal}")\nplot(probeStamp)\n')
    assert _emitted_ms(cpp, "probeStamp") == expected


@pytest.mark.parametrize("month", range(1, 13), ids=MONTHS)
def test_every_full_month_has_its_calendar_value(month):
    literal, fields = _month_case(month, full=True)
    expected = _calendar_ms(fields)
    assert _parse_pine_datestring_ms(literal) == expected
    cpp = transpile(HEADER + f'probeStamp = timestamp("{literal}")\nplot(probeStamp)\n')
    assert _emitted_ms(cpp, "probeStamp") == expected
    assert re.search(r"\bint64_t\s+probeStamp\b", cpp)


def test_full_month_input_time_preserves_int64():
    cpp = transpile(
        HEADER
        + 'inputStamp = input.time(timestamp("01 February 2024 00:00"), "Start Time")\n'
        + "plot(inputStamp)\n"
    )
    assert 'get_input_int64("Start Time"' in cpp
    assert 'get_input_int("Start Time"' not in cpp
    assert f"{_calendar_ms((2024, 2, 1, 0, 0, 0))}LL" in cpp


@pytest.mark.parametrize("full", [False, True], ids=["abbreviated-control", "full"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_calendar_values_and_consumers(full, opt):
    """Consume the complete generated TU through the built engine runtime."""
    from tests._compile import run_emitted_tu

    declarations = []
    members = []
    expected = []
    for month in range(1, 13):
        literal, fields = _month_case(month, full=full)
        name = f"probeStamp{month}"
        declarations.append(f'{name} = timestamp("{literal}")')
        members.append(name)
        expected.append(_calendar_ms(fields))

    name = "February" if full else "Feb"
    declarations += [
        f'inputStamp = input.time(timestamp("01 {name} 2024 00:00"), "Start Time")',
        'numericStamp = timestamp("UTC", 2024, 2, 1, 0, 0)',
        "namedStamp = timestamp(minute=0, day=1, year=2024, hour=0, month=2)",
        "carryStamp(int stampArg) => stampArg",
        f'carriedStamp = carryStamp(timestamp("01 {name} 2024 00:00"))',
        f'stampPair() => [timestamp("01 {name} 2024 00:00"), '
        f'timestamp("31 {"December" if full else "Dec"} 1999 23:59:59")]',
        "[pairStampA, pairStampB] = stampPair()",
    ]
    members += [
        "inputStamp", "numericStamp", "namedStamp", "carriedStamp",
        "pairStampA", "pairStampB",
    ]
    expected += [_calendar_ms((2024, 2, 1, 0, 0, 0))] * 5
    expected += [_calendar_ms((1999, 12, 31, 23, 59, 59))]
    cpp = transpile(HEADER + "\n".join(declarations) + "\nplot(inputStamp)\n")
    reads = "\n".join(
        f'    std::cout << strategy.{member} << "\\n";' for member in members
    )
    driver = r"""
#include <iostream>
int main() {
    Bar bars[] = {{1.0, 1.0, 1.0, 1.0, 1.0, 0}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1, "60", "60");
""" + reads + "\n}\n"
    output = run_emitted_tu(cpp, driver, opt=opt, label="month calendar consumers")
    assert [int(line) for line in output.splitlines()] == expected

"""Month-name date orders, offsets and calendar validation."""

from __future__ import annotations

from calendar import timegm
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.visit_call import _parse_pine_datestring_ms
from pineforge_codegen.errors import CompileError


MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
HEADER = '//@version=6\nstrategy("Calendar boundaries")\n'


def _assert_calendar_value(literal, fields, offset_minutes=0):
    # The oracle reads numeric fields only; it never parses a month name.
    expected = (timegm((*fields, 0, 0, 0)) - offset_minutes * 60) * 1000
    assert _parse_pine_datestring_ms(literal) == expected
    cpp = transpile(
        HEADER + f'calendarStamp = timestamp("{literal}")\nplot(calendarStamp)\n'
    )
    match = re.search(r"\bcalendarStamp\s*=\s*(-?\d+)LL\s*;", cpp)
    assert match is not None, "No folded int64 timestamp assigned to calendarStamp"
    assert int(match.group(1)) == expected
    assert re.search(r"\bint64_t\s+calendarStamp\b", cpp)


@pytest.mark.parametrize("month", range(1, 13), ids=MONTHS)
def test_month_first_full_names_with_offsets(month):
    fields = (1955 + month * 6, month, month + 2, month, month * 4 % 60, 59)
    year, _, day, hour, minute, second = fields
    offset = 345 if month % 2 else -270
    zone = "+0545" if offset > 0 else "-0430"
    literal = (
        f"{MONTHS[month - 1]} {day} {year} "
        f"{hour:02}:{minute:02}:{second:02} {zone}"
    )
    _assert_calendar_value(literal, fields, offset)


@pytest.mark.parametrize("literal, fields, offset", [
    ("29 February 2000", (2000, 2, 29, 0, 0, 0), 0),
    ("February 29 2000", (2000, 2, 29, 0, 0, 0), 0),
    ("30 April 2031 04:05", (2031, 4, 30, 4, 5, 0), 0),
    ("April 30 2031 04:05", (2031, 4, 30, 4, 5, 0), 0),
    ("01 March 1969 00:15 +0530", (1969, 3, 1, 0, 15, 0), 330),
    ("March 01 1969 00:15 +0530", (1969, 3, 1, 0, 15, 0), 330),
    ("31 December 2040 23:59:58 -0430", (2040, 12, 31, 23, 59, 58), -270),
    ("December 31 2040 23:59:58 -0430", (2040, 12, 31, 23, 59, 58), -270),
    ("2 May 1977 08:09 UTC", (1977, 5, 2, 8, 9, 0), 0),
    ("May 2 1977 08:09 UTC", (1977, 5, 2, 8, 9, 0), 0),
    ("31 OCTOBER 2027 02:30 GMT-4", (2027, 10, 31, 2, 30, 0), -240),
    ("june 15 2029 11:12:13 GMT+5:45", (2029, 6, 15, 11, 12, 13), 345),
])
def test_full_month_calendar_boundaries_and_time_forms(literal, fields, offset):
    _assert_calendar_value(literal, fields, offset)


@pytest.mark.parametrize("literal", [
    "29 February 1900 12:00",
    "February 29 1900 12:00",
    "29 February 2023 00:00 UTC",
    "February 29 2023 00:00 UTC",
    "31 April 2024 23:59:59 +0530",
    "April 31 2024 23:59:59 +0530",
    "00 January 2024",
    "January 00 2024",
    "32 December 2024",
    "December 32 2024",
    "01 February 2024 24:00",
    "February 01 2024 12:60",
    "01 February 2024 12:34:60",
    "01 Februarysuffix 2024 00:00",
    "Febru 01 2024 00:00",
    "01 February 2024 00:00 trailing",
])
def test_invalid_full_month_dates_keep_the_parse_diagnostic(literal):
    assert _parse_pine_datestring_ms(literal) is None
    with pytest.raises(CompileError, match="could not parse") as caught:
        transpile(HEADER + f'calendarStamp = timestamp("{literal}")\n')
    assert any(
        diagnostic.code == "PF-E5027" and literal in diagnostic.message
        for diagnostic in caught.value.diagnostics
    )

"""Literal UTC/GMT offset signs, with independent UTC calendar expectations."""

from __future__ import annotations

from calendar import timegm

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.visit_call import _parse_pine_datestring_ms
from pineforge_codegen.errors import CompileError


HEADER = '//@version=6\nstrategy("Zero-hour offsets", currency=currency.NONE)\n'

# UTC = written local time minus its signed offset. These expected UTC fields
# are specified independently of the parser and generated C++; no oracle parses
# the literal or infers its offset. At local midnight on 2024-01-01, -00:30 is
# 1704069000000 ms and +00:30 is 1704065400000 ms.
VALUE_CASES = [
    (f"2024-01-01 00:00 {word}{suffix}", utc_fields)
    for word in ("UTC", "GMT")
    for suffix, utc_fields in (
        ("+00:30", (2023, 12, 31, 23, 30, 0)),
        ("-00:30", (2024, 1, 1, 0, 30, 0)),
        ("+0:30", (2023, 12, 31, 23, 30, 0)),
        ("-0:30", (2024, 1, 1, 0, 30, 0)),
        ("+0030", (2023, 12, 31, 23, 30, 0)),
        ("-0030", (2024, 1, 1, 0, 30, 0)),
        ("+030", (2023, 12, 31, 23, 30, 0)),
        ("-030", (2024, 1, 1, 0, 30, 0)),
    )
] + [
    # Vary minutes, dates, seconds, month spelling/order and word case.
    ("1969-12-31 23:40:59 UTC-00:45", (1970, 1, 1, 0, 25, 59)),
    ("1970-01-01T00:10:01 GMT+00:45", (1969, 12, 31, 23, 25, 1)),
    ("29 February 2024 23:50:12 UTC-0:15", (2024, 3, 1, 0, 5, 12)),
    ("February 29 2000 00:10:17 GMT+0:15", (2000, 2, 28, 23, 55, 17)),
    ("30 Apr 2031 23:50 GMT-0045", (2031, 5, 1, 0, 35, 0)),
    ("Apr 30 2031 00:10 UTC+0045", (2031, 4, 29, 23, 25, 0)),
    ("December 31 1999 23:45:56 UTC-015", (2000, 1, 1, 0, 0, 56)),
    ("31 Dec 1999 00:01:02 GMT+0015", (1999, 12, 30, 23, 46, 2)),
    ("2025-06-15 12:34:56 utc-00:01", (2025, 6, 15, 12, 35, 56)),
    ("2027-08-09 04:02:03 gMt+0001", (2027, 8, 9, 4, 1, 3)),
    # Nonzero hours must retain their sign over the complete magnitude.
    ("2024-02-29 23:40:50 UTC-01:30", (2024, 3, 1, 1, 10, 50)),
    ("2024-03-01 01:10:50 GMT+0130", (2024, 2, 29, 23, 40, 50)),
    ("June 15 2029 11:12:13 GMT-5:45", (2029, 6, 15, 16, 57, 13)),
    ("15 Jun 2029 11:12:13 UTC+0545", (2029, 6, 15, 5, 27, 13)),
    ("2032-01-01 00:20 GMT-2", (2032, 1, 1, 2, 20, 0)),
    ("2032-01-01 00:20 UTC+2", (2031, 12, 31, 22, 20, 0)),
    # Numeric suffixes without a UTC/GMT word use the existing parsing paths.
    ("2024-01-01T00:00:00-00:30", (2024, 1, 1, 0, 30, 0)),
    ("01 January 2024 00:00 +0030", (2023, 12, 31, 23, 30, 0)),
    ("Jan 1 2024 00:00 -0030", (2024, 1, 1, 0, 30, 0)),
] + [
    (f"2024-07-02 03:04:05 {suffix}".rstrip(), (2024, 7, 2, 3, 4, 5))
    for suffix in (
        "", "UTC", "GMT", "UTC-00:00", "GMT+00:00",
        "UTC-0", "GMT+0", "UTC-0000", "GMT+0000",
    )
]


def _utc_ms(utc_fields):
    return timegm((*utc_fields, 0, 0, 0)) * 1000


@pytest.mark.parametrize("literal, expected_utc", VALUE_CASES)
def test_datestring_offset_calendar_value(literal, expected_utc):
    assert _parse_pine_datestring_ms(literal) == _utc_ms(expected_utc)


@pytest.mark.parametrize("literal", [
    "2024-02-30 00:00 UTC-00:30",
    "29 February 2023 00:00 GMT-0:45",
])
def test_invalid_dates_keep_the_located_parse_diagnostic(literal):
    assert _parse_pine_datestring_ms(literal) is None
    with pytest.raises(CompileError, match="could not parse") as caught:
        transpile(HEADER + f'probeStamp = timestamp("{literal}")\n')
    diagnostics = [d for d in caught.value.diagnostics if d.code == "PF-E5027"]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert literal in diagnostic.message
    assert diagnostic.location.line == 3
    assert diagnostic.location.col > 0


@pytest.mark.parametrize("word", ["UTC", "GMT"])
@pytest.mark.parametrize("suffix, utc_fields", [
    # UTC = written local time minus its signed offset, as for every row above.
    ("+24:00", (2023, 12, 31, 0, 0, 0)),
    ("-24:00", (2024, 1, 2, 0, 0, 0)),
])
def test_word_offsets_of_24_hours_are_plain_arithmetic(word, suffix, utc_fields):
    # A zone word of 24 hours or more is plain arithmetic on the date fields, as
    # in TradingView (its exported trade lists for UTC+24, GMT-24 and UTC+25;
    # tests/test_timestamp_zone_offsets.py): the fields read as UTC minus the
    # signed offset. The offset used to be built as a datetime.timezone, which
    # refuses 24 hours or more, so these strings stopped the translator with a
    # ValueError. The +-24:00 spelling is the same rule applied; the exported
    # lists cover the spellings UTC+24 and GMT-24.
    literal = f"2024-01-01 00:00 {word}{suffix}"
    assert _parse_pine_datestring_ms(literal) == _utc_ms(utc_fields)

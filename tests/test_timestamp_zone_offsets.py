"""timestamp(dateString) with a UTC or GMT zone word of 24 hours or more.

TradingView reads the zone word as plain arithmetic on the wall-clock fields:
the value is the fields taken as UTC, minus the signed offset, whatever its
size. Its exported trade lists for scripts built on the three strings in
TV_ROWS decode to exactly these constants ("01 Feb 2024 00:00" taken as UTC is
1706745600000 ms):

    UTC+24  ->  1706745600000 - 24 h = 1706659200000
    GMT-24  ->  1706745600000 + 24 h = 1706832000000
    UTC+25  ->  1706745600000 - 25 h = 1706655600000

The translator used to stop with an internal ValueError on these strings
(datetime.timezone refuses an offset of 24 hours or more) instead of emitting
the constant. CONTROL_ROWS are the neighbouring forms: a zone word under 24
hours, a bare UTC word and no zone at all. They pass on the unfixed tree too
and pin that the repair moves none of them.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp

PRELUDE = '//@version=6\nstrategy("T")\n'

TV_ROWS = [
    ("01 Feb 2024 00:00 UTC+24", 1706659200000),
    ("01 Feb 2024 00:00 GMT-24", 1706832000000),
    ("01 Feb 2024 00:00 UTC+25", 1706655600000),
]

CONTROL_ROWS = [
    ("01 Feb 2024 00:00", 1706745600000),
    ("01 Feb 2024 00:00 UTC", 1706745600000),
    # 1706745600000 - 2 h
    ("01 Feb 2024 00:00 UTC+2", 1706738400000),
    # GMT-5:30 is 5 h 30 min behind: 1706745600000 + 19800000
    ("01 Feb 2024 00:00 GMT-5:30", 1706765400000),
    # the largest whole-hour offsets under 24 h: 1706745600000 -/+ 82800000
    ("01 Feb 2024 00:00 UTC+23", 1706662800000),
    ("01 Feb 2024 00:00 UTC-23", 1706828400000),
]


def _datestring_cpp(text: str) -> str:
    return transpile(PRELUDE + f't = timestamp("{text}")\nplot(close)\n')


@pytest.mark.parametrize("text, ms", TV_ROWS)
def test_zone_word_of_24_hours_or_more_is_plain_arithmetic(text, ms):
    assert f"{ms}LL" in _datestring_cpp(text)


@pytest.mark.parametrize("text, ms", CONTROL_ROWS)
def test_neighbouring_zone_forms_keep_their_value(text, ms):
    assert f"{ms}LL" in _datestring_cpp(text)


def test_zone_word_of_25_hours_as_an_input_default():
    # The usual way a script carries a date: the default of an input.time.
    cpp = transpile(
        PRELUDE
        + 'start = input.time(timestamp("01 Feb 2024 00:00 UTC+25"), "Start")\n'
        + "plot(close)\n"
    )
    assert "get_input_int64" in cpp
    assert "1706655600000LL" in cpp


@pytest.mark.parametrize("text", [text for text, _ in TV_ROWS])
def test_zone_word_of_24_hours_or_more_builds(text):
    # A script TradingView accepts must translate AND its C++ must build.
    compile_cpp(_datestring_cpp(text), label=f"timestamp {text}")

"""timestamp(dateString) with a UTC or GMT zone word: the forms where the value differs.

TradingView's exported trade lists for scripts built on the strings below decode
to the values in TV_ROWS ("01 Feb 2024 00:00" taken as UTC is 1706745600000 ms;
a zone east of UTC gives a smaller value, a zone west of UTC a larger one):

* a signed zone keeps its sign when its hour is zero:
  ``GMT-00:30``, ``GMT-0:45``, ``UTC-0030`` and ``UTC-0:30`` are 30 or 45
  minutes WEST of UTC, so the value is that much larger;
* minutes of 60 or more are dropped and the hours are kept:
  ``UTC+00:60``, ``GMT+00:60`` and ``UTC+00:90`` are UTC, ``UTC+01:60`` is UTC+1;
* a number without a sign after the word is ignored: ``UTC30``, ``GMT30``,
  ``UTC5`` are UTC;
* with a numeric offset and a word in one string the LAST zone token decides:
  ``+0000 UTC+2`` is UTC+2, ``+0000 GMT-3`` is GMT-3, ``UTC+2 +0000`` is UTC.

The translator gave another value for twelve of these strings and refused two
of them (``UTC5`` and ``UTC+2 +0000``) as unparseable. CONTROL_ROWS are the
neighbouring zone forms that already agree with TradingView and must not move.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp

PRELUDE = '//@version=6\nstrategy("T")\n'

TV_ROWS = [
    # sign kept for a zero hour: +30 min, +45 min
    ("01 Feb 2024 00:00 GMT-00:30", 1706747400000),
    ("01 Feb 2024 00:00 GMT-0:45", 1706748300000),
    ("01 Feb 2024 00:00 UTC-0030", 1706747400000),
    ("01 Feb 2024 00:00 UTC-0:30", 1706747400000),
    # minutes of 60 or more dropped, hours kept
    ("01 Feb 2024 00:00 UTC+00:60", 1706745600000),
    ("01 Feb 2024 00:00 GMT+00:60", 1706745600000),
    ("01 Feb 2024 00:00 UTC+00:90", 1706745600000),
    ("01 Feb 2024 00:00 UTC+01:60", 1706742000000),
    # an unsigned number after the word is ignored
    ("01 Feb 2024 00:00 UTC30", 1706745600000),
    ("01 Feb 2024 00:00 GMT30", 1706745600000),
    ("01 Feb 2024 00:00 UTC5", 1706745600000),
    # the last zone token decides
    ("01 Feb 2024 00:00 +0000 UTC+2", 1706738400000),
    ("01 Feb 2024 00:00 +0000 GMT-3", 1706756400000),
    ("01 Feb 2024 00:00 UTC+2 +0000", 1706745600000),
]

CONTROL_ROWS = [
    ("01 Feb 2024 00:00 GMT-0", 1706745600000),
    # 30 min east: 1706745600000 - 1800000
    ("01 Feb 2024 00:00 GMT+00:30", 1706743800000),
    # 5 h west: 1706745600000 + 18000000
    ("01 Feb 2024 00:00 UTC-5", 1706763600000),
    # 5 h 30 min east: 1706745600000 - 19800000
    ("01 Feb 2024 00:00 UTC+5:30", 1706725800000),
    # a numeric offset alone
    ("20 Jul 2021 00:00 +0300", 1626728400000),
    ("01 Feb 2024 00:00 UTC+25", 1706655600000),
]


def _datestring_cpp(text: str) -> str:
    return transpile(PRELUDE + f't = timestamp("{text}")\nplot(close)\n')


@pytest.mark.parametrize("text, ms", TV_ROWS)
def test_zone_word_agrees_with_tradingview(text, ms):
    assert f"{ms}LL" in _datestring_cpp(text)


@pytest.mark.parametrize("text, ms", CONTROL_ROWS)
def test_zone_forms_that_agree_keep_their_value(text, ms):
    assert f"{ms}LL" in _datestring_cpp(text)


@pytest.mark.parametrize("text", [text for text, _ in TV_ROWS])
def test_zone_word_builds(text):
    # A script TradingView accepts must translate AND its C++ must build.
    compile_cpp(_datestring_cpp(text), label=f"timestamp {text}")

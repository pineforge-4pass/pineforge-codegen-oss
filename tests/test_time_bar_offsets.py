"""``time()`` / ``time_close()`` reading another bar are refused, not miscompiled.

Pine v6's ``time(timeframe, session, bars_back, timeframe_bars_back)``
overloads take a bar offset where the timezone form has its string:
``time("", "", -1)`` is the next bar's open. The offset reached the C++ as the
timezone (``std::string`` from ``-1``: "no matching function for call to
pine_time"). The engine exposes no other chart bar's time, above all a future
one, so a non-zero offset is refused with a located error; a literal 0 is the
current bar and is dropped.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


HEAD = '//@version=6\nstrategy("time offsets")\n'


def test_next_bar_time_is_refused():
    with pytest.raises(CompileError, match=r"time\(\) with bars_back"):
        transpile(HEAD + 'nextDay = dayofweek(time("", "", -1))\n')


def test_keyword_offsets_are_refused():
    with pytest.raises(CompileError, match=r"time_close\(\) with bars_back"):
        transpile(HEAD + 'x = time_close("60", bars_back = 2)\n')
    with pytest.raises(CompileError, match=r"time\(\) with bars_back"):
        transpile(HEAD + 'x = time("60", timeframe_bars_back = 1)\n')


def test_zero_offset_and_timezone_forms_compile():
    cpp = transpile(HEAD + 'x = time("D", "", 0)\n'
                    'y = time("D", "0930-1600", "America/New_York")\n'
                    'if x > 0 and y > 0\n    strategy.entry("L", strategy.long)\n')
    assert 'std::string("D"), std::string(""), std::string(""), script_tf_' in cpp
    assert 'std::string("0930-1600"), std::string("America/New_York")' in cpp
    compile_cpp(cpp, label="time offset 0")

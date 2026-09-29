"""``time()`` / ``time_close()`` reading another bar (lane TAIL-E, item 2).

Pine v6's ``time(timeframe, bars_back, timeframe_bars_back)``,
``time(timeframe, session, bars_back, timeframe_bars_back)`` and ``time(timeframe,
session, timezone, bars_back, timeframe_bars_back)`` put a bar offset where the
longer form has a string: ``time("", "", -1)`` is the next bar's open,
``time("60", -1)`` the hour holding the next bar, ``time("D", 0, 1)`` the
day before. They were refused ("the engine exposes no other chart bar's
time"; job-2660-pinecoderstasc-tasc-2026-06-one-percent-a-week-adaptive reads
the next bar's weekday). They now lower to the host's ``pine_time_offset``,
which steps the chart's bars and the requested timeframe's as TradingView
does: the engine's ``tests/test_time_bars_back_tapes.cpp`` replays the tapes
of six charts cell by cell; here TradingView's BINANCE:ETHUSDT.P 15 tapes of
both probes (``fixtures/tail_e_tv``) replay end to end. A call whose offsets
are literal 0 reads the current bar through the plain lowering, and an offset
inside a request.security payload is refused.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches, source, tape_rows,
)


HEAD = '//@version=6\nstrategy("time offsets")\n'


@pytest.mark.parametrize("pine, call", [
    ('time("", "", -1)',
     'pine_time_offset(current_bar_.timestamp, ((-1)), std::string(""), std::string(""), '
     'std::string(""), 0, false)'),
    ('time("60", -1)',
     'pine_time_offset(current_bar_.timestamp, ((-1)), std::string("60"), std::string(""), '
     'std::string(""), 0, false)'),
    ('time("D", 0, 1)',
     'pine_time_offset(current_bar_.timestamp, (0), std::string("D"), std::string(""), '
     'std::string(""), (1), false)'),
    ('time_close("60", bars_back = 2)',
     'pine_time_offset(current_bar_.timestamp, (2), std::string("60"), std::string(""), '
     'std::string(""), 0, true)'),
    ('time("60", timeframe_bars_back = -1)',
     'pine_time_offset(current_bar_.timestamp, 0, std::string("60"), std::string(""), '
     'std::string(""), ((-1)), false)'),
    ('time("D", "0930-1600", "America/New_York", 1, -1)',
     'pine_time_offset(current_bar_.timestamp, (1), std::string("D"), '
     'std::string("0930-1600"), std::string("America/New_York"), ((-1)), false)'),
])
def test_offsets_lower_to_the_host(pine, call):
    cpp = transpile(HEAD + f"x = {pine}\n"
                    'if x > 0\n    strategy.entry("L", strategy.long)\n')
    assert call in cpp
    compile_cpp(cpp, label=pine)


def test_zero_offset_and_timezone_forms_compile():
    cpp = transpile(HEAD + 'x = time("D", "", 0)\n'
                    'y = time("D", "0930-1600", "America/New_York")\n'
                    'z = time_close("", 0)\n'
                    'if x > 0 and y > 0 and z > 0\n    strategy.entry("L", strategy.long)\n')
    assert 'std::string("D"), std::string(""), std::string(""), script_tf_' in cpp
    assert 'std::string("0930-1600"), std::string("America/New_York")' in cpp
    # time_close("", 0): the 0 is an offset, never the session.
    assert "pine_time_close(current_bar_.timestamp, std::string(\"\"), std::string(\"\")," in cpp
    assert "pine_time_offset" not in cpp
    compile_cpp(cpp, label="time offset 0")


def test_an_offset_inside_a_security_payload_is_refused():
    with pytest.raises(CompileError, match=r"time\(\) with bars_back / timeframe_bars_back "
                                           r"inside request.security"):
        transpile(HEAD + 'x = request.security(syminfo.tickerid, "60", time("", -1))\n'
                  'if x > 0\n    strategy.entry("L", strategy.long)\n')


@pytest.mark.parametrize("name", ["te_time_bb_chart", "te_time_bb_tf"])
def test_the_offset_tapes_replay(name, tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows(f"{name}_eth15")
    assert len(tape) == 192
    # One bar past the range, so the last order fills.
    chart = feed(engine, tmp_path, START_MS + 2 * DAY_MS + 2 * BAR_MS)
    rows, _ = engine_rows(engine, build(tmp_path, name, source(name)), chart)
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])

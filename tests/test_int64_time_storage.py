"""Regression: ``int x = time(...) / time_close(...) / timestamp(...)`` must
emit ``Series<int64_t>`` (or ``int64_t`` scalar) storage. The Pine ``int``
type collapses to C++ 32-bit ``int`` by default, but ``pine_time`` returns
``na<int64_t>() == INT64_MIN`` outside the requested session, and that
sentinel narrows to ``0`` when stored in ``int`` — silently breaking
``is_na`` detection in strategy gates.
"""

from __future__ import annotations

from pineforge_codegen import transpile


def test_time_int64_storage():
    src = """//@version=6
strategy("t")
int sessId = time(timeframe.period, "0930-1600", "America/New_York")
plot(na(sessId) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "int64_t" in cpp
    # No bare ``Series<int>`` / ``int sessId`` slipping through (allow ``int64``).
    assert "Series<int> sessId" not in cpp
    assert "int sessId" not in cpp.replace("int64_t sessId", "")


def test_time_close_int64_storage():
    src = """//@version=6
strategy("t")
int tc = time_close(timeframe.period, "0930-1600", "America/New_York")
plot(na(tc) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "int64_t" in cpp
    assert "Series<int> tc" not in cpp


def test_timestamp_int64_storage():
    src = """//@version=6
strategy("t")
int t0 = timestamp("2025-01-01")
plot(close + (t0 == 0 ? 0 : 1))
"""
    cpp = transpile(src)
    assert "int64_t" in cpp


def test_var_time_int64_storage():
    src = """//@version=6
strategy("t")
var int firstSess = time(timeframe.period, "0930-1600", "America/New_York")
plot(na(firstSess) ? na : 1.0)
"""
    cpp = transpile(src)
    assert "Series<int64_t> firstSess" in cpp or "int64_t firstSess" in cpp
    assert "Series<int> firstSess" not in cpp

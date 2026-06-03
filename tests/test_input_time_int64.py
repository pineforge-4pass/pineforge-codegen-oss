"""input.time must route to get_input_int64 (int64 epoch ms, not int32).

Pine v6 `input.time(defval)` returns a `series int` Unix timestamp in
MILLISECONDS, which trivially overflows the int32 used by
`get_input_int` for any modern date. The codegen must route
`input.time` to the int64 helper instead.
"""

from __future__ import annotations

from pineforge_codegen import transpile


def test_input_time_routes_to_int64():
    src = """//@version=6
strategy("t")
ts = input.time(timestamp("2025-01-01T00:00:00"), "Start Time")
plot(close)
"""
    cpp = transpile(src)
    assert "get_input_int64" in cpp
    # Defensive: must NOT use the int32 helper for the time input. The
    # exact-call literal here (key in quotes immediately after `(`)
    # avoids matching unrelated `get_input_int_<other>` substrings.
    assert 'get_input_int("Start Time"' not in cpp


def test_input_color_routes_to_int64():
    """input.color routes to get_input_int64: a packed ARGB color
    (0xAARRGGBB, e.g. color.red == 0xFFFF0000) overflows signed int32, so
    int32 truncates/sign-flips the value. int64 preserves it.
    """
    src = """//@version=6
strategy("t")
c = input.color(color.red, "Line Color")
plot(close)
"""
    cpp = transpile(src)
    assert 'get_input_int64("Line Color"' in cpp

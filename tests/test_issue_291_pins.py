"""Exact reporter/control pins for public issue #291, cases 1 and 2.

Case 4 already has an exact pin in tests/test_named_array_constructors.py.
Reuse the existing native fixture and compare the issue's original traces.
"""

import math

from pineforge_codegen import transpile
from tests.test_public_issue_291 import _assert_values, _values, native


def test_case_1_reporter_matches_named_history_control(native):
    source = """//@version=6
strategy('repro')
mid = (close + open) / 2
float a = 0.0
float b = 0.0
for i = 1 to 3
    a += ((close + open) / 2)[i]
    b += mid[i]
diff = a - b
// @pf-trace diff=diff
plot(a)
"""
    control = source.replace("a += ((close + open) / 2)[i]", "a += mid[i]")
    expected = _values(native("case_1_control", control), "diff")
    assert all(math.isfinite(value) for value in expected[3:]), (
        "The named-history control must have defined values after warmup"
    )
    observed = _values(native("case_1_reporter", source), "diff")
    _assert_values(observed, expected, "case 1: expression vs named history")


def test_case_2_reporter_matches_plain_copy_control(native):
    source = """//@version=6
strategy('repro')
[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
float histCopy = hist
readHist() => hist
v = readHist()
diff = v - hist
// @pf-trace v=v
// @pf-trace diff=diff
plot(v)
"""
    control = source.replace("readHist() => hist\n", "readHist() => histCopy\n")
    for script in (source, control):
        cpp = transpile(script)
        assert "#include <pineforge/source/pine_ta_length.hpp>" in cpp
        assert "pineforge::source::PineMacd _ta_macd_1;" in cpp
        assert "hist = _result__ta_macd_1.histogram;" in cpp
        assert "double hist = _result__ta_macd_1.histogram;" not in cpp
    expected = native("case_2_control", control)
    expected_v = _values(expected, "v")
    assert len({value for value in expected_v if math.isfinite(value)}) > 1, (
        "The plain-copy control must supply varying MACD values"
    )
    observed = native("case_2_reporter", source)
    _assert_values(_values(observed, "v"), expected_v, "case 2: tuple vs plain copy")
    _assert_values(
        _values(observed, "diff"),
        _values(expected, "diff"),
        "case 2: tuple vs plain-copy difference",
    )

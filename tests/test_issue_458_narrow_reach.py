"""Which chart TA member does a single #458 call site receive?"""

import re

import pytest

from pineforge_codegen import transpile


_PREAMBLE = """//@version=6
strategy("conditional extrema")
gate = bar_index % 3 == 0
inputLen = input.int(5, "Length")
simpleLen = syminfo.mintick > 0 ? 5 : 6
seriesLen = time > 0 ? 5 : 6
float result = na
"""


def _assert_member(body, family, expected):
    cpp = transpile(_PREAMBLE + body + "\nplot(result)\n")
    types = re.findall(rf"^    (\w[\w:<>, ]*) _ta_{family}_\d+;$", cpp, re.M)
    assert len(types) == 1, (
        f"NOT COMPARED: ta.{family} members; expected=1, actual={len(types)} {types}"
    )
    assert types[0] == expected
    if "_PFExecutedExtreme" not in expected:
        assert "class _PFExecutedExtreme" not in cpp


@pytest.mark.parametrize("body", [
    pytest.param("if gate\n    result := low\nelse\n    result := CALL", id="else"),
    pytest.param("if gate\n    result := low\nelse if close > open\n    result := CALL",
                 id="else-if-body"),
    pytest.param("if gate\n    result := low\nelse if close > CALL\n    result := close",
                 id="else-if-condition"),
    pytest.param("if gate\n    if close > open\n        result := CALL", id="nested-if-body"),
    pytest.param("if gate\n    if close > CALL\n        result := close", id="nested-if-condition"),
    pytest.param("result := if gate\n    CALL\nelse\n    close", id="value-if"),
    pytest.param("v = if gate\n    result := CALL\n    result\nelse\n    close\nresult := v",
                 id="value-if-store"),
    pytest.param("if gate\n    result := close > open ? CALL : close", id="ternary-arm"),
    pytest.param("bool ok = false\nif gate\n    ok := gate and close > CALL\nresult := ok ? low : high",
                 id="and-operand"),
    pytest.param("bool ok = false\nif gate\n    ok := gate or close > CALL\nresult := ok ? low : high",
                 id="or-operand"),
    pytest.param("if gate\n    result := math.abs(CALL)", id="call-argument"),
    pytest.param("if gate\n    localValue = CALL\n    result := localValue", id="local-declaration"),
    pytest.param("if gate\n    [mid, hi, lo] = ta.bb(CALL, 3, 2)\n    result := mid",
                 id="tuple-declaration"),
    pytest.param("if gate\n    result += CALL", id="compound-assignment"),
])
def test_unprobed_position_keeps_chart_window(body):
    # A site outside the probed form is declared as every other site of the
    # call is: for a constant or input length, with the engine's checked
    # fixed-length type (tests/test_ta_fixed_length_checks.py).
    _assert_member(body.replace("CALL", "ta.lowest(low, 5)"), "lowest",
                   "pineforge::source::PineFixedLowest")


@pytest.mark.parametrize(("family", "source", "kernel"), [
    ("lowest", "low", "_PFExecutedExtreme<false>"),
    ("highest", "high", "_PFExecutedExtreme<true>"),
])
@pytest.mark.parametrize("length", ["5", "inputLen", "simpleLen", "seriesLen"])
def test_probe_458_value_windows(family, source, kernel, length):
    expected = f"pineforge::source::FirstCallBound<{kernel}>" if length == "simpleLen" else kernel
    _assert_member(f"if gate\n    result := ta.{family}({source}, {length})", family, expected)


@pytest.mark.parametrize(("family", "source", "length", "expected"), [
    ("highestbars", "high", "simpleLen", "pineforge::source::FirstCallBound<_PFExecutedExtreme<true, true>>"),
    ("highestbars", "high", "seriesLen", "_PFExecutedExtreme<true, true>"),
    ("highestbars", "high", "5", "pineforge::source::PineFixedHighestBars"),
    ("highestbars", "high", "inputLen", "pineforge::source::PineFixedHighestBars"),
    ("lowestbars", "low", "5", "pineforge::source::PineFixedLowestBars"),
    ("lowestbars", "low", "inputLen", "pineforge::source::PineFixedLowestBars"),
    ("lowestbars", "low", "simpleLen",
     "pineforge::source::FirstCallBound<pineforge::source::PineFixedLowestBars>"),
    ("lowestbars", "low", "seriesLen", "pineforge::source::SeriesLowestBars"),
], ids=["sw3-simple", "sw3-series", "highestbars-literal", "highestbars-input",
        "lowestbars-literal", "lowestbars-input", "lowestbars-simple", "lowestbars-series"])
def test_offset_window_by_function_and_length(family, source, length, expected):
    _assert_member(f"if gate\n    result := ta.{family}({source}, {length})", family, expected)


@pytest.mark.parametrize(("call", "family", "expected"), [
    ("ta.highest(5)", "highest", "pineforge::source::PineFixedHighest"),
    ("ta.lowest(length=5)", "lowest", "pineforge::source::PineFixedLowest"),
    ("ta.highestbars(seriesLen)", "highestbars", "pineforge::source::SeriesHighestBars"),
])
def test_implicit_source_keeps_chart_window(call, family, expected):
    _assert_member(f"if gate\n    result := {call}", family, expected)


@pytest.mark.parametrize(("declaration", "kernel"), [
    ("const int n = 5", "pineforge::source::PineFixedHighestBars"),
    ("n = inputLen + 1", "pineforge::source::PineFixedHighestBars"),
    ("simple int n = 5", "_PFExecutedExtreme<true, true>"),
    ("series int n = 5", "_PFExecutedExtreme<true, true>"),
])
def test_highestbars_uses_the_bound_qualifier(declaration, kernel):
    # Each length fits the ordinary constructor. Only the bound Pine
    # qualifier distinguishes these sites for SW3's window.
    _assert_member(declaration + "\nif gate\n    result := ta.highestbars(high, n)",
                   "highestbars", kernel)

"""Regression tests for timezone-only calendar built-in calls and ``var`` + ``array.from`` element type inference.

Covers two transpiler defects:
  A) Calendar built-ins (``hour``, ``minute``, ``second``, ``dayofmonth``,
     ``dayofweek``, ``month``, ``year``, ``weekofyear``) called with a
     ``timezone`` keyword argument and no positional ``time`` argument must
     bind ``time`` to the bar's time (``current_bar_.timestamp`` on the chart,
     ``bar.timestamp`` inside ``request.security``) rather than shifting the
     timezone string into the ``int64_t`` timestamp parameter.
  B) ``var x = array.from(...)`` must infer a single consistent element type
     across the declaration and first-bar initializer for every element type
     (``int``, ``float``, ``bool``, ``string``, ``color``), including integer
     constants declared in function scope or via unary sign (``-1``) and mixed
     numeric element lists.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile


CALENDAR_BUILTINS = (
    "hour",
    "minute",
    "second",
    "dayofmonth",
    "dayofweek",
    "month",
    "year",
    "weekofyear",
)


@pytest.mark.parametrize("func_name", CALENDAR_BUILTINS)
def test_calendar_builtin_timezone_only_binds_chart_bar_time(func_name: str):
    src = chr(10).join([
        "//@version=6",
        'strategy("tz only")',
        f'v = {func_name}(timezone="UTC")',
        "if v >= 0",
        '    strategy.entry("L", strategy.long)',
        "",
    ])
    cpp = transpile(src)
    assert "int64_t _pf_calendar_ts = (current_bar_.timestamp);" in cpp
    if func_name == "weekofyear":
        assert 'pine_weekofyear(_pf_calendar_ts, std::string("UTC"))' in cpp
    else:
        assert (
            f"pine_{func_name}(is_na(_pf_calendar_ts) ? int64_t(0) : "
            f'_pf_calendar_ts, std::string("UTC"))' in cpp
        )


@pytest.mark.parametrize("func_name", CALENDAR_BUILTINS)
def test_calendar_builtin_timezone_only_in_security_binds_security_bar_time(
    func_name: str,
):
    src = chr(10).join([
        "//@version=6",
        'strategy("tz only sec")',
        f'v = request.security(syminfo.tickerid, "60", {func_name}(timezone="UTC"))',
        "if v >= 0",
        '    strategy.entry("L", strategy.long)',
        "",
    ])
    cpp = transpile(src)
    assert "int64_t _pf_calendar_ts = (bar.timestamp);" in cpp
    if func_name == "weekofyear":
        assert 'pine_weekofyear(_pf_calendar_ts, std::string("UTC"))' in cpp
    else:
        assert (
            f"pine_{func_name}(is_na(_pf_calendar_ts) ? int64_t(0) : "
            f'_pf_calendar_ts, std::string("UTC"))' in cpp
        )


@pytest.mark.parametrize(
    "elem_kind, setup_lines, expected_cpp_elem",
    [
        (
            "int",
            [
                "f() =>",
                "    DIRECTIONS = 1",
                "    var directions = array.from(DIRECTIONS, -1)",
                "    array.size(directions)",
                "sz = f()",
            ],
            "int",
        ),
        (
            "int_top_level_unary",
            [
                "var directions = array.from(-1)",
                "sz = array.size(directions)",
            ],
            "int",
        ),
        (
            "float",
            [
                "DIRECTIONS = 1",
                "var directions = array.from(DIRECTIONS, 1.5)",
                "sz = array.size(directions)",
            ],
            "double",
        ),
        (
            "bool",
            [
                "B1 = true",
                "var directions = array.from(B1, not B1)",
                "sz = array.size(directions)",
            ],
            "bool",
        ),
        (
            "string",
            [
                'S1 = "long"',
                'var directions = array.from(S1, "short")',
                "sz = array.size(directions)",
            ],
            "std::string",
        ),
        (
            "color",
            [
                "C1 = color.red",
                "var directions = array.from(C1, #00ff00)",
                "sz = array.size(directions)",
            ],
            "int64_t",
        ),
    ],
)
def test_var_array_from_decl_and_assign_vector_types_match(
    elem_kind: str,
    setup_lines: list[str],
    expected_cpp_elem: str,
):
    src = chr(10).join([
        "//@version=6",
        f'strategy("var array.from {elem_kind}")',
        *setup_lines,
        "if sz > 0",
        '    strategy.entry("L", strategy.long)',
        "",
    ])
    cpp = transpile(src)
    decl_match = re.search(
        r"^\s*std::vector<([^>]+)>\s+directions\s*;",
        cpp,
        re.MULTILINE,
    )
    assign_match = re.search(
        r"directions\s*=\s*std::vector<([^>]+)>\{",
        cpp,
    )
    assert decl_match is not None, f"missing member declaration in:{chr(10)}{cpp}"
    assert assign_match is not None, f"missing initializer assignment in:{chr(10)}{cpp}"
    decl_elem = decl_match.group(1)
    assign_elem = assign_match.group(1)
    assert decl_elem == expected_cpp_elem
    assert assign_elem == expected_cpp_elem
    assert decl_elem == assign_elem

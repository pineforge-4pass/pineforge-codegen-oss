"""Regression tests for Pine's empty-array calculation semantics."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile


def _generate(body: str) -> str:
    return transpile(f'//@version=6\nstrategy("T")\n{body}\n')


@pytest.mark.parametrize(
    ("expression", "unsafe_operation"),
    [
        ("array.sum(values)", "std::accumulate"),
        ("array.avg(values)", "std::accumulate"),
        ("array.min(values)", "std::min_element"),
        ("array.max(values)", "std::max_element"),
        ("array.range(values)", "std::max_element"),
        ("array.stdev(values)", "std::sqrt"),
        ("array.stdev(values, false)", "std::sqrt"),
        ("array.variance(values)", "std::accumulate"),
        ("array.variance(values, false)", "std::accumulate"),
        ("array.median(values)", "std::sort"),
        ("array.mode(values)", "std::unordered_map"),
        ("array.percentile_linear_interpolation(values, 50)", "c.back()"),
        ("array.percentile_nearest_rank(values, 50)", "c[std::min"),
        ("array.percentrank(values, 0)", "double v=__pf_array"),
        ("array.covariance(values, peers)", "ma/=n"),
    ],
)
def test_empty_numeric_array_calculations_guard_before_use(
    expression: str,
    unsafe_operation: str,
):
    cpp = _generate(
        "values = array.new<float>(0)\n"
        "peers = array.new<float>(0)\n"
        f"ki48_result = {expression}\n"
        "plot(ki48_result)"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert "na<double>()" in assignment
    assert unsafe_operation in assignment
    assert assignment.index("na<double>()") < assignment.index(unsafe_operation)


def test_empty_int_array_aggregate_returns_typed_numeric_na():
    cpp = _generate(
        "values = array.new<int>(0)\n"
        "ki48_result = values.min()\n"
        "plot(ki48_result)"
    )

    assert "std::vector<int> values" in cpp
    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert "values.empty()?na<double>()" in assignment
    assert "*std::min_element(values.begin(),values.end())" in assignment


def test_empty_temporary_array_aggregate_evaluates_receiver_once():
    cpp = _generate(
        "values = array.new<float>(0)\n"
        "ki48_result = array.sum(array.copy(values))\n"
        "plot(ki48_result)"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert "auto&& __pf_array_receiver_" in assignment
    assert "na<double>()" in assignment
    assert assignment.count("std::vector<double>(values)") == 1


@pytest.mark.parametrize(
    ("expression", "argument_call", "guard"),
    [
        ("array.stdev(values, biased())", "biased()", "values.empty()"),
        ("array.variance(values, biased())", "biased()", "values.empty()"),
        (
            "array.percentile_linear_interpolation(values, percentage())",
            "percentage()",
            "values.empty()",
        ),
        (
            "array.percentile_nearest_rank(values, percentage())",
            "percentage()",
            "values.empty()",
        ),
    ],
)
def test_empty_calculation_evaluates_stateful_argument_once_before_guard(
    expression: str,
    argument_call: str,
    guard: str,
):
    cpp = _generate(
        "side_effects = array.new<float>(0)\n"
        "biased() =>\n"
        "    array.push(side_effects, 1.0)\n"
        "    false\n"
        "percentage() =>\n"
        "    array.push(side_effects, 1.0)\n"
        "    50.0\n"
        "index() =>\n"
        "    array.push(side_effects, 1.0)\n"
        "    0\n"
        "values = array.new<float>(0)\n"
        f"ki48_result = {expression}\n"
        "plot(ki48_result)"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert assignment.count(argument_call) == 1
    assert argument_call in assignment
    assert guard in assignment
    assert assignment.index(argument_call) < assignment.index(guard)


def test_empty_percentrank_evaluates_stateful_index_once_before_guard():
    cpp = _generate(
        "side_effects = array.new<float>(0)\n"
        "index() =>\n"
        "    array.push(side_effects, 1.0)\n"
        "    0\n"
        "values = array.new<float>(0)\n"
        "ki48_result = array.percentrank(values, index())\n"
        "plot(ki48_result)"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert assignment.count("index()") == 1
    assert "[&](auto&& __pf_array)" in assignment
    assert "return [&](auto&& __pf_raw_index_value)" in assignment
    assert "if(__pf_array.size()<=1) return na<double>()" in assignment
    assert assignment.index("}((index()))") < assignment.index("}((values))")


def test_temporary_receiver_is_bound_before_stateful_argument():
    cpp = _generate(
        "values = array.new<float>(0)\n"
        "side_effects = array.new<float>(0)\n"
        "percentage() =>\n"
        "    array.push(side_effects, 1.0)\n"
        "    50.0\n"
        "ki48_result = array.percentile_nearest_rank(array.copy(values), percentage())\n"
        "plot(ki48_result)"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    receiver = "std::vector<double>(values)"
    assert assignment.count(receiver) == 1
    assert assignment.count("percentage()") == 1
    assert assignment.index(receiver) < assignment.index("percentage()")
    assert assignment.index("percentage()") < assignment.index(".empty()")


def test_empty_standardize_remains_an_empty_array_not_scalar_na():
    cpp = _generate(
        "values = array.new<float>(0)\n"
        "ki48_result = array.standardize(values)\n"
        "plot(array.size(ki48_result))"
    )

    assignment = next(
        line
        for line in cpp.splitlines()
        if line.startswith("        ki48_result =")
    )
    assert "std::vector<double> r" in assignment
    assert "return r" in assignment
    assert "return na<double>()" not in assignment

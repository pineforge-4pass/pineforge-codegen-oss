"""Requested builtin observers retain their helper's parameter bindings."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests.test_v6_declaration_request_compilation import (
    GLOBAL_TUPLE_HELPER, GLOBAL_TUPLE_HISTORY, TIMEFRAME_HELPER, TIME_HELPER,
)


@pytest.mark.parametrize(
    "source", [TIMEFRAME_HELPER, GLOBAL_TUPLE_HELPER],
    ids=["timeframe-change", "global-tuple"],
)
@pytest.mark.parametrize(
    "definition",
    [
        "valid(x) => nz(x)",
        "valid(float x) => na(x) ? 0 : 1",
        "valid(float x) => nz(source = x, replacement = 0.0)",
        "valid(x) => x > 0 ? 1 : 0",
        "valid(x) => math.abs(x)",
    ],
    ids=["nz", "typed-na", "typed-named-nz", "comparison-control", "math-control"],
)
def test_observer_bindings_compose_with_requested_context(source, definition):
    source = source.replace("valid(x) => na(x) ? 0 : 1", definition, 1)
    compile_cpp(transpile(source, check_support=True), label="requested-observer-binding")


@pytest.mark.parametrize(
    "payload",
    ["valid(close)", 'time("W")', 'timeframe.change("W") ? 1 : 0'],
    ids=["helper-alone", "time-alone", "change-alone"],
)
def test_independent_requested_payload_parts_remain_compilable(payload):
    source = TIME_HELPER.replace('valid(close) * time("W")', payload, 1)
    compile_cpp(transpile(source, check_support=True), label="independent-requested-payload")


@pytest.mark.parametrize(
    "declaration",
    [
        '[upper, lower, ignored] = ta.macd(close, 12, 26, 9)',
        '[upper, lower] = ta.supertrend(3.0, 10)',
    ],
    ids=["macd-control", "supertrend-control"],
)
def test_builtin_tuple_member_history_keeps_requested_storage(declaration):
    source = GLOBAL_TUPLE_HISTORY.replace("[upper, lower] = channel()", declaration, 1)
    compile_cpp(transpile(source, check_support=True), label="requested-builtin-tuple-history")

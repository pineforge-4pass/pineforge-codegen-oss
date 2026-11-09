"""Callable numeric values retain the fraction in Pine's division."""

from textwrap import dedent

import pytest

from pineforge_codegen import transpile
from tests._e2e import reference_codegen, transpile_json
from tests._numeric_expected import (
    int64_store, integer_arithmetic, integer_store, require_numeric_hunk,
)
from tests.test_bool_string_inference import _messages, _run_native, native_env


HEADER = (
    '//@version=6\nstrategy("Callable quotient", currency=currency.NONE)\n'
    'n = input.int(5, "Numerator")\n'
)
CPP_REFERENCE = "c12c1877da48629b9c711f745a752ffc20ceef17"


def _source(body):
    return HEADER + dedent(body).lstrip()


@pytest.mark.parametrize("body", [
    pytest.param("""
        measure(int seed) =>
            int s = seed
            s := s / 2
            s
        observed = measure(n)
    """, id="declared_local"),
    pytest.param("""
        measure(int seed) =>
            s = seed
            s := s / 2
            s
        observed = measure(n)
    """, id="inferred_local"),
    pytest.param("""
        measure(int a, int b) => a / b
        observed = measure(n, 2)
    """, id="quotient_return"),
    pytest.param("""
        measure(seed) => seed
        observed = measure(n / 2)
    """, id="quotient_argument"),
    pytest.param("""
        measure(seed) =>
            local = seed
            local
        observed = measure(seed = n / 2)
    """, id="named_quotient_argument"),
    pytest.param("""
        measure(seed) => seed * 1
        observed = measure(n / 2)
    """, id="quotient_argument_arithmetic"),
    pytest.param("""
        measure(seed) => seed
        quotient = n / 2
        observed = measure(quotient)
    """, id="stored_quotient_argument"),
    pytest.param("""
        measure(seed) => seed
        whole = measure(n)
        observed = measure(n / 2) + (whole - n)
    """, id="mixed_argument_families"),
])
def test_native_callable_quotient_matches_pine_arithmetic(
    tmp_path, native_env, body,
):
    source = (
        _source(body)
        + "reference = 5.0 / 2.0\n"
        + 'log.info("reference {0} {1}", bar_index, reference)\n'
        + 'log.info("observed {0} {1}", bar_index, observed)\n'
    )
    stderr = _run_native(tmp_path, native_env, source)
    count = native_env[2]
    reference = {
        bar: float(value)
        for bar, value in _messages(stderr, "reference", count).items()
    }
    assert reference == {bar: 2.5 for bar in range(count)}
    observed = {
        bar: float(value)
        for bar, value in _messages(stderr, "observed", count).items()
    }
    assert observed == reference


@pytest.mark.parametrize("body", [
    pytest.param("""
        measure(int seed) =>
            int s = seed
            s := s + 2
            s
        observed = measure(n)
    """, id="integer_local"),
    pytest.param("""
        measure(int seed) =>
            int s = seed
            s := math.floor(seed / 2)
            s
        observed = measure(n)
    """, id="floor_local"),
    pytest.param("""
        measure(seed) => seed
        observed = measure(n + 2)
    """, id="integer_argument"),
    pytest.param("""
        measure(seed) => seed
        observed = measure(math.floor(n / 2))
    """, id="floor_argument"),
    pytest.param("""
        measure(seed) => seed
        observed = measure(4 / 2)
    """, id="integral_quotient_argument"),
    pytest.param("""
        measure(seed) => seed
        float observed = 0.0
        for counter = 0.0 to 1.0 by 0.25
            observed += measure(counter)
    """, id="float_counter_argument"),
])
def test_integer_callable_controls_keep_complete_reference_cpp(tmp_path, body):
    source = _source(body)
    reference = reference_codegen(CPP_REFERENCE)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before.get("diagnostics")
    cpp = transpile(source)
    # a925fb3a preserves nullable arithmetic and numeric argument boundaries;
    # 2b13f9f computes integral sums in the wide intermediate family.
    if "s := s + 2" in body:
        cpp = require_numeric_hunk(
            cpp, "s = (s + 2);",
            f"s = {integer_store(integer_arithmetic('s', '+', '2'))};",
        )
    elif "s := math.floor(seed / 2)" in body:
        # math.floor(x) is a Pine int, a 64-bit one: the local it is stored in, the
        # callable that returns it and the global that holds the result are int64_t,
        # and the local's first value (the 32-bit seed) is widened na-preservingly.
        quotient = integer_arithmetic("seed", "/", "2")
        floor_value = f"std::floor({quotient})"
        cpp = require_numeric_hunk(
            cpp,
            f"s = {integer_store('std::floor(((double)(seed) / (double)(2)))')};",
            f"s = {int64_store(floor_value)};",
        )
        cpp = require_numeric_hunk(
            cpp, "int s = seed;", f"int64_t s = {int64_store('seed')};")
        cpp = require_numeric_hunk(
            cpp, "int measure(int seed) {", "int64_t measure(int seed) {")
        cpp = require_numeric_hunk(
            cpp, "int observed = 0;", "int64_t observed = 0;")
    elif "for counter =" in body:
        argument = f"measure({integer_store('counter')})"
        cpp = require_numeric_hunk(
            cpp, "observed += measure(counter);",
            f"observed = {integer_arithmetic('observed', '+', argument)};",
        )
    assert cpp == before["cpp"]

"""TradingView omits unused loops and stops used endless loops at bar zero."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.endless_for import plan_endless_for_loops
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._endless_for_cases import (
    BASELINE, BASELINE_SOURCES, CONTROLS, FIXTURES, FUNCTION_RUNS,
    LATER_FINITE_LOOP, LATER_LOOP_SOURCES, LATER_LOOP_TARGETS,
    LIVE_REACHABILITY_CONTROLS, OMITTED_RUNS, TV, source,
)


def _assert_stop(cpp):
    assert '"Error on bar "' in cpp
    assert '": Loop takes too long to execute (> "' in cpp
    assert '" ms)"' in cpp
    assert re.search(r"constexpr int kLoopTimeLimitMs = 2000;", cpp)
    assert "std::to_string(::kLoopTimeLimitMs)" in cpp
    assert "pine_runtime_error(" in cpp
    assert "_for_start_" not in cpp


@pytest.mark.parametrize("name", [*TV["runs"], *TV["stops"]])
def test_fixtures_are_the_measured_sources(name):
    evidence = TV["runs"].get(name, TV["stops"].get(name))
    assert hashlib.sha256((FIXTURES / f"{name}.pine").read_bytes()).hexdigest() == (
        evidence["source_sha256"]
    )
    digest = evidence.get("tape_sha256", evidence.get("error_sha256"))
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


@pytest.mark.parametrize("name", OMITTED_RUNS)
def test_tradingview_omits_the_unused_loop(name):
    # TradingView runs these with 205 entries, each with quantity 1.
    cpp = transpile((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    # A receiving declaration can remain, but the loop's update cannot.
    assert not re.search(r"\bacc\s*=\s*\(acc \+ 1\)", cpp)
    assert "_for_start_" not in cpp
    assert "Loop takes too long" not in cpp
    assert re.search(r"\bacc\b.*0\.0", cpp)


@pytest.mark.parametrize("name", TV["stops"])
def test_tradingview_stops_the_used_loop_even_for_late_or_untaken_reads(name):
    # TradingView stops on bar 0 with RE10022, maxTimeMs=2000.
    cpp = transpile((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    _assert_stop(cpp)
    assert "acc + 1" not in cpp


@pytest.fixture(scope="module")
def prior_cpp_hashes():
    assert BASELINE.is_file(), (
        f"Missing prior counted-loop C++ baseline: {BASELINE}. "
        "Capture it from the counted-loop lowering that precedes the endless-loop rule."
    )
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert document.get("schema") == 1, "Expected prior C++ baseline schema 1"
    rows = document.get("sources", {})
    missing = set(BASELINE_SOURCES) - set(rows)
    unexpected = set(rows) - set(BASELINE_SOURCES)
    assert not missing and not unexpected, (
        f"Prior C++ baseline source inventory mismatch: "
        f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
    )
    for name, pine in BASELINE_SOURCES.items():
        row = rows[name]
        keys = ["source_sha256", "cpp_sha256"]
        if name in LATER_LOOP_SOURCES:
            keys.append("later_loop_sha256")
        for key in keys:
            assert re.fullmatch(r"[0-9a-f]{64}", row.get(key, "")), (
                f"Missing or invalid {key} for baseline source {name}"
            )
        assert hashlib.sha256(pine.encode("utf-8")).hexdigest() == row["source_sha256"], (
            f"Baseline source has changed: {name}"
        )
    return rows


def _assert_prior_cpp(name, prior_cpp_hashes):
    cpp = transpile(BASELINE_SOURCES[name])
    assert hashlib.sha256(cpp.encode("utf-8")).hexdigest() == (
        prior_cpp_hashes[name]["cpp_sha256"]
    ), f"C++ differs from the prior counted-loop lowering: {name}"
    return cpp


@pytest.mark.parametrize("name", FUNCTION_RUNS)
def test_function_body_loops_keep_the_prior_cpp_and_no_translation_stop(
    name, prior_cpp_hashes,
):
    cpp = _assert_prior_cpp(name, prior_cpp_hashes)
    assert "Loop takes too long" not in cpp


@pytest.mark.parametrize("name", CONTROLS)
def test_out_of_class_loops_keep_every_cpp_byte(
    name, prior_cpp_hashes,
):
    _assert_prior_cpp(name, prior_cpp_hashes)


@pytest.mark.parametrize("header", [
    "0 to walkIndex",
    "0 to walkIndex - 1",
    "5 to walkIndex",
    "0 to 2 * walkIndex + 3",
    "0.0 to walkIndex + 2.5 by (1.0 / 4)",
    "(3 - 3) to ((2 + 2) / 2) * walkIndex + (9 - 6) by (4 - 2)",
])
def test_affine_rule_is_independent_of_counter_spelling_and_constant_syntax(header):
    # These are consequences of the measured fixed-direction continuation rule.
    pine = source(
        f"float acc = 0.0\nfor walkIndex = {header}\n    acc += 1\n",
    )
    _assert_stop(transpile(pine))


@pytest.mark.parametrize("tail", [
    "plot(acc)\n",
    "firstCopy = acc + 1\nsecondCopy = firstCopy + 1\nplot(secondCopy)\n",
    "measure() => acc\nplot(measure())\n",
    "unusedCopy = acc + math.random(0.0, 1.0)\n",
])
def test_a_transitive_path_to_an_effect_keeps_the_loop_live(tail):
    _assert_stop(transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
    """, tail)))


@pytest.mark.parametrize("name", LIVE_REACHABILITY_CONTROLS)
def test_a_chain_reaching_order_data_or_its_condition_keeps_the_loop_live(name):
    _assert_stop(transpile(LIVE_REACHABILITY_CONTROLS[name]))


@pytest.mark.parametrize("tail", [
    "firstCopy = acc + 1\nsecondCopy = firstCopy + 1\n",
    "firstCopy = acc + 1\nsecondCopy = 0.0\nsecondCopy := firstCopy\n",
    "measure() => acc\nunusedCopy = measure()\n",
    "measure() => acc\n",
    "measure() =>\n    unusedCopy = acc + 1\n    1.0\nplot(measure())\n",
    "if acc > 0\n    unusedCopy = acc + 1\n",
    "firstCopy = 0.0\nfirstCopy := acc + 1\n",
    "unusedCopy = acc + 1\nunusedCopy := 0.0\n",
    "firstCopy = acc + 1\nsecondCopy = acc + 2\n",
])
def test_unmeasured_reads_stop_even_without_an_effect(tail):
    cpp = transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
    """, tail))
    _assert_stop(cpp)


def test_a_parameter_shadow_does_not_read_the_written_binding():
    # Only the separate parameter binding is read; the chart write is unread.
    cpp = transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
    """, "measure(float acc) => acc + 1\nplot(measure(1.0))\n"))
    assert "_for_start_" not in cpp
    assert "Loop takes too long" not in cpp


@pytest.mark.parametrize("tail", [
    "measure(float sampleValue = acc) => sampleValue\n",
    "method measure(float sampleValue, float increment = acc) => sampleValue + increment\n",
    "unusedCopy = acc + 1\nmeasure(float sampleValue = unusedCopy) => sampleValue\n",
])
def test_default_reads_preclude_omission_before_support_checks(tail):
    # Defaults are annotation nodes. Check the planner without claiming that
    # TradingView accepts a variable in a parameter default.
    program = Parser(Lexer(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
    """, tail)).tokenize()).parse()
    assert set(plan_endless_for_loops(program).values()) == {"stop"}


@pytest.mark.parametrize("tail", [
    'switch\n    acc > 0 => strategy.entry("L", strategy.long, qty=1)\n',
    'for controlIndex = 0 to int(acc)\n'
    '    strategy.entry("L", strategy.long, qty=1)\n',
    'while acc > 0\n    strategy.entry("L", strategy.long, qty=1)\n    break\n',
    'float quantityValue = 1.0\nif acc > 0\n    quantityValue := 2.0\n'
    'strategy.entry("L", strategy.long, qty=quantityValue)\n',
])
def test_control_headers_reach_an_effect_or_a_live_definition(tail):
    cpp = transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
    """, tail))
    assert "Loop takes too long to execute" in cpp
    assert "int walkIndex =" not in cpp


@pytest.mark.parametrize("body", [
    'strategy.entry("L", strategy.long, qty=1)',
    "array.push(values, float(walkIndex))",
    'alert("Loop body")',
    "label.new(walkIndex, close)",
    "var float savedValue = 0.0",
    "array.push(values, 1.0)",
])
def test_body_effects_keep_the_loop_live_without_a_scalar_reader(body):
    pine = source(
        "values = array.new_float()\n"
        "for walkIndex = 0 to walkIndex\n"
        f"    {body}\n",
        "",
    )
    _assert_stop(transpile(pine))


@pytest.mark.parametrize("callee", ["unknownCall", "array.unknownCall", "math.unknownCall"])
def test_a_call_without_a_purity_proof_is_an_effect(callee):
    # Exercise the planner before the support checker rejects unknown calls.
    program = Parser(Lexer(source(
        "for walkIndex = 0 to walkIndex\n"
        f"    {callee}(walkIndex)\n",
        "",
    )).tokenize()).parse()
    assert set(plan_endless_for_loops(program).values()) == {"stop"}


def test_an_unknown_effect_in_an_unused_statement_keeps_its_other_inputs_live():
    program = Parser(Lexer(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
        unusedCopy = acc + unknownCall()
    """, "")).tokenize()).parse()
    assert set(plan_endless_for_loops(program).values()) == {"stop"}


def test_pure_math_and_string_calls_in_an_unused_loop_stop():
    cpp = transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += math.abs(walkIndex)
            rendered = str.tostring(walkIndex)
    """, ""))
    _assert_stop(cpp)


def test_an_unmeasured_unused_body_stops_even_with_a_break():
    _assert_stop(transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += math.abs(walkIndex)
            if acc > 10
                break
    """, "")))


def test_loop_expression_result_is_live():
    # A value-producing if can return its final loop's value.
    _assert_stop(transpile(source("""
        acc = if true
            for walkIndex = 0 to walkIndex
                walkIndex + 1
    """)))


def test_an_unused_loop_expression_result_stops():
    cpp = transpile(source("""
        unusedTotal = if true
            for walkIndex = 0 to walkIndex
                walkIndex + 1
    """, "plot(close)\n"))
    _assert_stop(cpp)


def test_nested_chart_loop_is_classified_at_its_own_position():
    cpp = transpile(source("""
        float acc = 0.0
        if bar_index >= 0
            for outerIndex = 0 to 1
                for walkIndex = 0 to walkIndex
                    acc += 1
    """))
    assert "Loop takes too long" in cpp
    assert cpp.count("constexpr int kLoopTimeLimitMs = 2000;") == 1
    assert re.search(r"for \(int outerIndex =", cpp)
    assert "int walkIndex =" not in cpp


def test_a_loop_returned_by_an_unused_branch_stops_despite_a_sibling_shadow():
    cpp = transpile(source("""
        if bar_index >= 0
            float subtotal = 0.0
            for walkIndex = 0 to walkIndex
                subtotal += 1
        float subtotal = 5.0
    """, "plot(subtotal)\n"))
    _assert_stop(cpp)


def test_a_chain_in_an_unused_branch_stops_despite_a_sibling_shadow():
    cpp = transpile(source("""
        if bar_index >= 0
            float subtotal = 0.0
            for walkIndex = 0 to walkIndex
                subtotal += 1
            unusedCopy = subtotal + 1
        float subtotal = 5.0
        firstCopy = subtotal + 1
        secondCopy = firstCopy + 1
    """, "plot(secondCopy)\n"))
    _assert_stop(cpp)


@pytest.mark.parametrize("name,target", LATER_LOOP_TARGETS.items())
def test_replacing_a_loop_keeps_the_later_finite_loops_cpp(
    name, target, prior_cpp_hashes,
):
    cpp = transpile(BASELINE_SOURCES[name])
    # The preceding endless loop changes, so the full C++ must differ.
    assert hashlib.sha256(cpp.encode("utf-8")).hexdigest() != (
        prior_cpp_hashes[name]["cpp_sha256"]
    ), f"The endless loop was not replaced: {name}"
    actual_loop = LATER_FINITE_LOOP.search(cpp)
    assert actual_loop is not None, f"Later finite loop missing from C++: {name}"
    assert hashlib.sha256(actual_loop.group().encode("utf-8")).hexdigest() == (
        prior_cpp_hashes[name]["later_loop_sha256"]
    ), f"Later finite loop differs from the prior counted-loop lowering: {name}"
    if target == "acc":
        assert "Loop takes too long to execute" in cpp
    else:
        assert "Loop takes too long to execute" not in cpp
    assert not re.search(
        rf"\b{target}\s*(?:\+=\s*1|=\s*\({target}\s*\+\s*1\))\s*;",
        cpp,
    )


def test_multiple_live_loops_share_the_named_message_limit():
    cpp = transpile(source("""
        float acc = 0.0
        for firstIndex = 0 to firstIndex
            acc += 1
        for secondIndex = 0 to secondIndex + 1
            acc += 1
    """))
    assert cpp.count("constexpr int kLoopTimeLimitMs = 2000;") == 1
    assert cpp.count('": Loop takes too long to execute (> "') == 2


def test_pure_helper_calls_in_an_unused_reduction_stop():
    cpp = transpile(source("""
        magnitude(float sampleValue) => math.abs(sampleValue)
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += magnitude(walkIndex)
    """, ""))
    _assert_stop(cpp)


def test_reading_a_collection_in_an_unused_loop_stops():
    cpp = transpile(source("""
        values = array.new_float(1, 1.0)
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += array.get(values, 0)
    """, ""))
    _assert_stop(cpp)


def test_an_unreachable_break_does_not_exempt_a_live_endless_loop():
    _assert_stop(transpile(source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
            if false
                break
    """)))

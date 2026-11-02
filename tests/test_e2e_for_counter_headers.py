"""Counter-dependent headers run with TradingView's measured quantities.

The fixture value probes have TradingView measurements.
Renamings and compositions below are derived semantic controls, not additional
TradingView tapes. Every assertion reads the compiled strategy's trade output.
"""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
from textwrap import dedent

import pytest

from tests import _compile as compile_env
from pineforge_codegen import transpile
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, execute_all, ok,
    skip_unless_e2e_env,
)
from tests._for_counter_header_cases import STOP_SOURCES
from tests._for_fractional_carriers import CARRIER_SOURCES, KNOWN_CARRIER_GAPS


FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers"
TV = json.loads((FIXTURES / "tradingview_quantities.json").read_text(encoding="utf-8"))
MEASURED = tuple(TV["entry_quantities"])
PATTERN_MEASURED = tuple(TV["entry_quantity_patterns"])
FRACTIONAL_COUNTER_TAPES = (
    "fractional_descending", "fractional_array_index", "counter_string",
)
REASSIGNMENT_TAPES = (
    "reassign_int_hint", "reassign_plain_qty", "reassign_literal_qty",
    "reassign_series_qty", "reassign_untyped", "reassign_typed",
)
CALLABLE_QUANTITY_TAPES = ("callable_var_qty", "callable_local_qty")
# Each twin is an engine-vs-engine control, not TradingView evidence.
REASSIGNMENT_TAPE_TWINS = {
    name: ("s := s / 2", "s := s * 0.5") for name in REASSIGNMENT_TAPES
}
# These twins change authored Pine expressions, never emitted C++ or a tape.
# Each twin is an engine-vs-engine control, not TradingView evidence.
DIVISION_TAPE_TWINS = {
    "division_start_no_step": ("for i = 5 / 2 to", "for i = 2.5 to"),
    "division_start_int_step": ("for i = 7 / 2 to", "for i = 3.5 to"),
    "division_named_start": ("s = n / 2", "s = 2.5"),
    "division_end_only": ("for i = 0 to 7 / 2", "for i = 0 to 3.5"),
    "division_counter_value": ("for i = 9 / 2 to", "for i = 4.5 to"),
    "fractional_input": ("for i = n / 2 to", "for i = n * 0.5 to"),
    "fractional_series": ("for i = n / 2 to", "for i = n * 0.5 to"),
    "fractional_descending": ("for i = n / 2 to", "for i = n * 0.5 to"),
    "fractional_array_index": ("for i = n / 2 to", "for i = n * 0.5 to"),
    "counter_string": ("for i = n / 2 to", "for i = n * 0.5 to"),
}
# Shared parameter inference still truncates this float counter. Run it as
# a documented discrepancy, retaining TradingView's measured 5 in the quantities file.
HELPER_VALUE_GAP = "float_counter_helper_value"
HELPER_PARAMETER_GAP = "helper_param"
MATCHING_MEASURED = tuple(
    name for name in MEASURED
    if name not in (HELPER_VALUE_GAP, HELPER_PARAMETER_GAP)
)
# These measured rows distinguish accumulated rounding near 0.3 from an
# end shortfall at a larger scale. Both use TradingView's recorded quantities.
END_DIFFERENCE_PROBES = (
    "float_counter_tenth_step_to_three_tenths",
    "float_counter_mid_step_short_end",
)
FEED_BARS = 1443
ZERO_ENTRY_PROBES = tuple(TV["zero_entry_probes"])
FIRST_END_READ_PROBES = (
    "first_end_read_start_two_down",
    "first_end_read_start_two_up",
    *ZERO_ENTRY_PROBES,
)
REAL_SCRIPTS = ("open_counter_header_end", "open_counter_header_step")
# Derived compositions of measured chart counts, without new TradingView tapes.
FLOAT_CONTINUATION_PROBES = (
    "float_counter_tenth_step_to_three_tenths",
    "float_counter_fifth_step_to_three_fifths",
    "float_counter_descending_tenth_step",
    "float_counter_end_between_steps",
    "float_counter_end_short_by_ten_billionth",
    "float_counter_large_magnitude",
)
TRADE_TAIL = """\
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""


def _source(body: str) -> str:
    return (
        '//@version=6\nstrategy("Counter header composition", '
        'initial_capital=1000000, currency=currency.NONE)\n'
        + dedent(body).lstrip() + TRADE_TAIL
    )


# Controls exercise the same operation with different expression/scope shapes.
# Expected values here follow the measured end/step evaluation rules.
CONTROLS = {
    "helper_end": (_source("""
        endBound(int counterValue) => counterValue == 0 ? 3 : 1
        float acc = 0.0
        for walkIndex = 0 to endBound(walkIndex)
            acc += walkIndex + 1
    """), {"default": 3}),
    # DERIVED from the measured first-test rule. Passing the counter as a
    # helper argument must also re-read the end after assigning the start.
    # The previous head instead enters twice and produces quantity 6.1.
    "helper_end_start_two": (_source("""
        endBound(int counterValue) => counterValue == 2 ? 3 : 1
        float acc = 1.1
        for walkIndex = 2 to endBound(walkIndex)
            acc := acc + walkIndex + 1
    """), {"default": 1.1}),
    "nested_end_start_two": (_source("""
        float acc = 1.1
        for walkIndex = 2 to (math.max(walkIndex, 0) == 2 ? 3 : 1)
            acc := acc + walkIndex + 1
    """), {"default": 1.1}),
    "both_header_slots": (_source("""
        float acc = 0.0
        for walkIndex = 0 to (walkIndex == 0 ? 3 : 1) by (walkIndex == 0 ? 1 : 2)
            acc += walkIndex + 1
    """), {"default": 3}),
    "function_scope": (_source("""
        measure() =>
            float subtotal = 0.0
            for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
                subtotal += walkIndex + 1
            subtotal
        float acc = measure()
    """), {"default": 3}),
    "function_loop_value": (_source("""
        measure() =>
            for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
                walkIndex + 1
        float acc = measure()
    """), {"default": 2}),
    "block_scope": (_source("""
        float acc = 0.0
        if bar_index >= 0
            for walkIndex = 0 to (walkIndex == 0 ? 1 : 0)
                acc += walkIndex + 1
    """), {"default": 1}),
    "nested_and_sibling_scopes": (_source("""
        float acc = 0.0
        for outerIndex = 0 to 1
            for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
                acc += walkIndex + 1
        for walkIndex = 0 to (walkIndex == 0 ? 1 : 0)
            acc += walkIndex + 1
    """), {"default": 7}),
    "break_and_continue": (_source("""
        float acc = 0.0
        for walkIndex = 0 to (walkIndex == 0 ? 5 : 3)
            if walkIndex == 0
                continue
            if walkIndex == 2
                break
            acc += walkIndex + 1
    """), {"default": 2}),
    "nonzero_input_start": (_source("""
        first = input.int(2, "First")
        float acc = 0.0
        for walkIndex = first to first + 4 by (walkIndex == first ? 1 : 2)
            acc += walkIndex + 1
    """), {"default": 15, "override": 21}),
    "dynamic_body_end": (_source("""
        int limit = 3
        float acc = 0.0
        for walkIndex = 0 to (walkIndex >= 0 ? limit : 0)
            limit -= 1
            acc += 1
    """), {"default": 2}),
    "descending_positive_step": (_source("""
        float acc = 0.0
        for walkIndex = 5 to 0 by (walkIndex > 3 ? 1 : 2)
            acc += walkIndex + 1
    """), {"default": 12}),
    "outer_block_storage": (_source("""
        float acc = 0.0
        if bar_index >= 0
            var int walkIndex = 7
            for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
                acc += walkIndex + 1
    """), {"default": 3}),
    "series_parameter": (_source("""
        measure(series float walkIndex) =>
            float subtotal = 0.0
            for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
                subtotal += walkIndex + 1
            subtotal + nz(walkIndex[1] - walkIndex[1])
        float acc = measure(close)
    """), {"default": 3}),
}


@pytest.fixture(scope="module")
def run_environment(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("for_counter_headers")
    # The seven-bar schedule closes all 205 entries by bar 1441. The eight-bar
    # schedule closes its final entry at bar 1442, so include that bar too.
    feed = chart_feed_head(engine, base, FEED_BARS)
    return engine, base, feed


@pytest.fixture(scope="module")
def runs(run_environment):
    engine, base, feed = run_environment
    builds = {}
    for name in (*MEASURED, *PATTERN_MEASURED, *ZERO_ENTRY_PROBES):
        # Includes the float-start probes with TradingView's measured
        # quantities and export hashes in tradingview_quantities.json.
        pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
        builds[name] = Build(pine)
        counters = re.findall(r"(?m)^\s*for (\w+) =", pine)
        writes = re.findall(r"(?m)^(\w+) :=", pine)
        rename = counters[-1] if counters else writes[0] if writes else "i"
        renamed = re.sub(
            rf"\b{rename}\b", "sampleIndex", pine,
        )
        builds[f"{name}_renamed"] = Build(renamed)
        twin = DIVISION_TAPE_TWINS.get(name) or REASSIGNMENT_TAPE_TWINS.get(name)
        if twin is not None:
            before, after = twin
            assert pine.count(before) == 1, name
            builds[f"{name}_twin"] = Build(pine.replace(before, after))
        if name in FIRST_END_READ_PROBES:
            # A requested-context composition of the measured chart source;
            # this is derived coverage, not an additional TradingView tape.
            builds[f"{name}_in_security"] = Build(_in_security(pine))
        if name in FLOAT_CONTINUATION_PROBES:
            bound = _with_counter_bound_step(pine)
            builds[f"{name}_bound"] = Build(bound)
            builds[f"{name}_in_security"] = Build(_in_security(pine))
            builds[f"{name}_bound_in_security"] = Build(_in_security(bound))
            builds[f"{name}_inputs"] = Build(_with_input_header(pine))
            builds[f"{name}_siblings"] = Build(_with_sibling_loop(pine))
            for position in ("before", "after"):
                builds[f"{name}_omitted_{position}"] = Build(
                    _with_omitted_loop(pine, position),
                )
    for name in REAL_SCRIPTS:
        builds[name] = Build((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    for name, (pine, _) in CONTROLS.items():
        overrides = {"First": 4} if name == "nonzero_input_start" else None
        builds[name] = Build(pine, overrides=overrides)
    for name, pine in CARRIER_SOURCES.items():
        builds[f"carrier_{name}"] = Build(pine)
    for name, pine in DERIVED_FLOAT_SOURCES.items():
        builds[name] = Build(pine)
    for name, pine in NONFINITE_FLOAT_SOURCES.items():
        builds[name] = Build(pine)
    return execute_all(engine, feed, base, builds)


def _assert_quantities(
    outcome, tag: str, expected: int | float, *, probe: str | None = None,
) -> None:
    rows = csv.DictReader(outcome.trades[tag].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    expected_entries = TV["entry_counts"].get(probe, TV["entries_per_probe"])
    schedule = TV["trade_schedules"].get(probe)
    if schedule is not None:
        # Orders fill on the next bar. Only entries whose close can fill
        # within this feed contribute closed trades, irrespective of tape length.
        expected_entries = len(range(
            schedule["first_entry_bar"], FEED_BARS - schedule["close_delay"] - 1,
            schedule["period"],
        ))
    assert len(entries) == expected_entries, (
        f"{tag}: expected {expected_entries} closed entries, got {len(entries)}"
    )
    wrong = [
        (row["Trade #"], row["Qty"])
        for row in entries if float(row["Qty"]) != expected
    ]
    assert not wrong, f"{tag}: expected every entry quantity to be {expected}; got {wrong[:10]}"


def test_tradingview_evidence_has_complete_sha256():
    for name in (*MEASURED, *PATTERN_MEASURED, *ZERO_ENTRY_PROBES, *TV["runtime_errors"]):
        for field in ("sha256", "source_sha256"):
            assert re.fullmatch(
                r"[0-9a-fA-F]{64}", TV["evidence"][name][field] or ""
            ), (name, field)
    for name, refusal in TV["compile_refusals"].items():
        for field in ("error_json_sha256", "source_sha256"):
            assert re.fullmatch(
                r"[0-9a-fA-F]{64}", refusal[field] or ""
            ), (name, field)
    assert re.fullmatch(r"[0-9a-fA-F]{64}", TV["ta_counter_type_receipt"]["sha256"])


@pytest.mark.parametrize("name", (
    *REASSIGNMENT_TAPES, *CALLABLE_QUANTITY_TAPES,
    HELPER_PARAMETER_GAP, *DIVISION_TAPE_TWINS,
))
def test_division_fixture_is_the_measured_source(name):
    evidence = TV["evidence"][name]
    assert evidence["source"] == f"{name}.pine"
    assert hashlib.sha256((FIXTURES / evidence["source"]).read_bytes()).hexdigest() == (
        evidence["source_sha256"]
    )


@pytest.mark.parametrize("name", FRACTIONAL_COUNTER_TAPES)
def test_fractional_counter_tape_translates_with_a_double_counter(name):
    # TradingView accepted each exact fixture. This check has no native skip.
    cpp = transpile((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    assert re.search(r"\bfor \(double i = ", cpp), cpp
    assert not re.search(r"\bfor \(int i = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\bdouble _for_{slot}_\d+ = ", cpp), cpp


@pytest.mark.parametrize("name", tuple(
    name for name in MATCHING_MEASURED
    if name not in END_DIFFERENCE_PROBES
))
def test_tradingview_entry_quantities(runs, name):
    _assert_quantities(
        ok(runs, name), "default", TV["entry_quantities"][name], probe=name,
    )


@pytest.mark.parametrize("name", END_DIFFERENCE_PROBES)
def test_tradingview_end_differences(runs, name):
    _assert_quantities(
        ok(runs, name), "default", TV["entry_quantities"][name], probe=name,
    )


@pytest.mark.parametrize("name", MATCHING_MEASURED)
def test_counter_spelling_does_not_change_values(runs, name):
    _assert_quantities(ok(runs, f"{name}_renamed"), "default",
                       TV["entry_quantities"][name], probe=name)


@pytest.mark.parametrize("suffix", ["", "_renamed"])
def test_documented_untyped_helper_fraction_gap(runs, suffix):
    # This asserts the remaining discrepancy, not TradingView parity.
    assert TV["entry_quantities"][HELPER_VALUE_GAP] == 5
    _assert_quantities(ok(runs, f"{HELPER_VALUE_GAP}{suffix}"), "default", 2)


@pytest.mark.parametrize("name", FLOAT_CONTINUATION_PROBES)
@pytest.mark.parametrize("suffix", [
    "_bound", "_in_security", "_bound_in_security", "_inputs", "_siblings",
    "_omitted_before", "_omitted_after",
])
def test_float_continuation_routes_follow_measured_counts(runs, name, suffix):
    _assert_quantities(ok(runs, f"{name}{suffix}"), "default",
                       TV["entry_quantities"][name])


@pytest.mark.parametrize("name", FIRST_END_READ_PROBES[:2])
def test_first_end_read_in_security_follows_measured_chart_rule(runs, name):
    # DERIVED request.security composition of the measured 1.1 chart rows.
    _assert_quantities(ok(runs, f"{name}_in_security"), "default",
                       TV["entry_quantities"][name])


@pytest.mark.parametrize("name", ZERO_ENTRY_PROBES)
@pytest.mark.parametrize("suffix", ["", "_renamed", "_in_security"])
def test_tradingview_zero_entry_probes(runs, name, suffix):
    outcome = ok(runs, f"{name}{suffix}")
    rows = csv.DictReader(outcome.trades["default"].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    assert len(entries) == TV["zero_entry_probes"][name]["entries"]


@pytest.mark.parametrize("name", ZERO_ENTRY_PROBES)
def test_zero_entry_probe_accumulator_stays_zero(name):
    # Check the value as well as the header-only trade output: na or an
    # unevaluated trade carrier must not stand in for zero loop iterations.
    compile_env.skip_if_no_engine_lib()
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    strategy.run(&bar, 1, "1", "1");
    assert(strategy.last_error().empty());
    std::cout << strategy.acc;
}
'''
    assert compile_env.run_emitted_tu(
        transpile(pine), driver, opt="-O2", label=name,
    ) == str(TV["zero_entry_probes"][name]["acc"])


@pytest.mark.parametrize("name", REAL_SCRIPTS)
def test_real_scripts_build_and_run_check(runs, name):
    # Their orders use literal qty=1: this is a run check, while the probes
    # above measure the loop's value.
    _assert_quantities(ok(runs, name), "default", 1)


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_counter_binding_compositions(runs, name):
    outcome = ok(runs, name)
    for tag, expected in CONTROLS[name][1].items():
        _assert_quantities(outcome, tag, expected)


@pytest.mark.parametrize("name", tuple(DIVISION_TAPE_TWINS))
def test_division_tape_fixture_keeps_its_counter_family_and_twin(runs, name):
    """engine-vs-engine control, not TradingView evidence."""
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    cpp = transpile(pine)
    family = "int" if name == "division_end_only" else "double"
    assert re.search(rf"\bfor \({family} i = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\b{family} _for_{slot}_\d+ = ", cpp), cpp
    # Scalar tape quantities are checked by test_tradingview_entry_quantities;
    # the series tape's ordered quantities are checked separately below.
    assert_same_runs(ok(runs, name), ok(runs, name + "_twin"))


@pytest.mark.parametrize("name", REASSIGNMENT_TAPES)
def test_reassignment_tape_matches_its_fraction_preserving_twin(runs, name):
    """engine-vs-engine control, not TradingView evidence."""
    assert_same_runs(ok(runs, name), ok(runs, name + "_twin"))


@pytest.mark.parametrize("name", PATTERN_MEASURED)
@pytest.mark.parametrize("suffix", ["", "_renamed", "_twin"])
def test_tradingview_alternating_entry_quantities(runs, name, suffix):
    # The _twin suffix is an engine-vs-engine control, not TradingView evidence.
    outcome = ok(runs, name + suffix)
    rows = csv.DictReader(outcome.trades["default"].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    pattern = TV["entry_quantity_patterns"][name]
    quantities = [float(row["Qty"]) for row in entries]
    assert len(quantities) == TV["entries_per_probe"]
    assert quantities == [
        pattern["quantities"][index % len(pattern["quantities"])]
        for index in range(TV["entries_per_probe"])
    ], TV["evidence"][name]["sha256"]
    assert [quantities.count(qty) for qty in pattern["quantities"]] == pattern["counts"]


@pytest.mark.parametrize("suffix", ["", "_twin"])
def test_known_gap_counter_tostring(runs, run_environment, suffix):
    """TV prints 2.500000000000000000; we print 2.500000000000000 (15 places).

    Quantity and entry counts match the tape; ids are a known formatter gap.
    This row must flip to the recorded TV ids when the formatter is fixed.
    """
    # The _twin suffix is an engine-vs-engine control, not TradingView evidence.
    gap = TV["known_gaps"]["counter_tostring"]
    name = gap["probe"]
    pattern = TV["entry_id_patterns"][name]
    evidence = TV["evidence"][name]["sha256"]
    outcome = ok(runs, name + suffix)
    _assert_quantities(outcome, "default", TV["entry_quantities"][name], probe=name)
    engine, base, feed = run_environment
    # engine_trades.csv has no ids. Read the same built strategy through the
    # existing native report helper, which exposes each closed trade's id.
    entries = closed_trades(engine, base / (name + suffix), feed)
    assert len(entries) == TV["entry_counts"][name], evidence
    assert all(
        entry["qty"] == TV["entry_quantities"][name] for entry in entries
    ), evidence
    recorded = Counter(dict(zip(pattern["ids"], pattern["counts"])))
    current = Counter(dict(zip(gap["current_entry_ids"], pattern["counts"])))
    actual = Counter(entry["entry_id"] for entry in entries)
    assert actual == current, (evidence, actual, current)
    assert actual != recorded, (evidence, "formatter gap closed; assert the TV ids")


@pytest.mark.parametrize("name", sorted(CARRIER_SOURCES.keys() - KNOWN_CARRIER_GAPS))
def test_fractional_start_carrier_matches_the_measured_loop_shape(runs, name):
    """Derived from fractional_input's tape for the same default-step sum."""
    _assert_quantities(
        ok(runs, f"carrier_{name}"), "default",
        TV["entry_quantities"]["fractional_input"], probe="fractional_input",
    )


@pytest.mark.parametrize("name", (
    "carrier_helper_parameter", HELPER_PARAMETER_GAP, HELPER_PARAMETER_GAP + "_renamed",
))
def test_known_gap_helper_parameter_fraction(runs, name):
    """TradingView gives 10.5 and we give 9; flips when a helper parameter keeps the fraction."""
    gap = TV["known_gaps"]["helper_parameter_fraction"]
    assert TV["entry_quantities"][HELPER_PARAMETER_GAP] == gap["tradingview_entry_quantity"]
    _assert_quantities(ok(runs, name), "default", gap["current_entry_quantity"])


STOP_DRIVER = r'''
#include <cassert>
#include <iostream>
int main() {
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1, "1", "1");
    assert(strategy.last_error().find("'step' in loop must be greater than zero")
           != std::string::npos);
    assert(strategy.trade_count() == 0);
    std::cout << "stopped before trades";
}
'''


def _in_security(pine: str) -> str:
    """Run the same loop in a requested helper, keeping its trade carrier."""
    lines = pine.splitlines()
    tail = next(i for i, line in enumerate(lines) if line.startswith("if bar_index"))
    return "\n".join([
        *lines[:2],
        "total() =>",
        *("    " + line for line in lines[2:tail]),
        "    acc",
        "tradeQty = request.security(syminfo.tickerid, timeframe.period, total())",
        *(line.replace("qty = acc", "qty = tradeQty") for line in lines[tail:]),
        "",
    ])


def _with_counter_bound_step(pine: str) -> str:
    def bind(match):
        counter, step = match["counter"], match["step"]
        return match["header"] + f"({counter} >= 0 ? {step} : {step})"

    return re.sub(
        r"(?m)^(?P<header>for (?P<counter>\w+) = [^\n]+ by )(?P<step>[^\n]+)$",
        bind, pine,
    )


def _with_input_header(pine: str) -> str:
    def bind(match):
        return (
            f'first = input.float({match["start"]}, "First")\n'
            f'last = input.float({match["end"]}, "Last")\n'
            f'stride = input.float({match["step"]}, "Stride")\n'
            f'for {match["counter"]} = first to last by stride'
        )

    result, count = re.subn(
        r"(?m)^for (?P<counter>\w+) = (?P<start>.+?) to (?P<end>.+?)"
        r" by (?P<step>[^\n]+)$",
        bind, pine,
    )
    assert count == 1
    return result


def _with_sibling_loop(pine: str) -> str:
    lines = pine.splitlines()
    loop = next(i for i, line in enumerate(lines) if line.startswith("for "))
    tail = next(i for i, line in enumerate(lines) if line.startswith("if bar_index"))
    # Run the same measured loop twice; average the two counts into one order.
    return "\n".join([
        *lines[:tail], *lines[loop:tail],
        *(line.replace("qty = acc", "qty = acc / 2") for line in lines[tail:]),
        "",
    ])


def _with_omitted_loop(pine: str, position: str) -> str:
    lines = pine.splitlines()
    tail = next(i for i, line in enumerate(lines) if line.startswith("if bar_index"))
    at = 2 if position == "before" else tail
    return "\n".join([
        *lines[:at],
        "float unused = 0.0",
        "for unusedIndex = 0.0 to unusedIndex by 0.1",
        "    unused += 1",
        *lines[at:], "",
    ])


# DERIVED, no TradingView tape: exact binary step 0.25 gives
# (1.0 - 0.0) / 0.25 + 1 = 5 iterations, each adding 1.0.
_FLOAT_COUNTER_SOURCE = _source("""
    float acc = 0.0
    for x = 0.0 to 1.0 by 0.25
        acc += 1.0
""")
_FLOAT_COUNTER_END_SOURCE = _source("""
    float acc = 0.0
    for x = 0.0 to (x >= 0.0 ? 1.0 : 1.0) by 0.25
        acc += 1.0
""")
DERIVED_FLOAT_SOURCES = {
    "derived_float_counter_in_security": _in_security(_FLOAT_COUNTER_SOURCE),
    "derived_float_counter_bound_end": _FLOAT_COUNTER_END_SOURCE,
    "derived_float_counter_bound_end_in_security": _in_security(_FLOAT_COUNTER_END_SOURCE),
}


# DERIVED, without a TradingView tape: math.exp lowers to std::exp and
# overflows to +inf. A finite bound admits the initial counter once, then
# the difference to the end rejects the infinite counter after its first
# increment.
NONFINITE_FLOAT_SOURCES = {}
for _direction, _start, _end in (("up", "0.0", "1.0"), ("down", "1.0", "0.0")):
    _pine = _source(f"""
        float acc = 0.0
        for walkIndex = {_start} to {_end} by math.exp(1000.0)
            acc += 1.0
    """)
    NONFINITE_FLOAT_SOURCES[f"infinite_step_{_direction}"] = _pine
    NONFINITE_FLOAT_SOURCES[f"infinite_step_{_direction}_bound"] = (
        _with_counter_bound_step(_pine)
    )
    NONFINITE_FLOAT_SOURCES[f"infinite_step_{_direction}_in_security"] = (
        _in_security(_pine)
    )
    NONFINITE_FLOAT_SOURCES[f"infinite_step_{_direction}_bound_in_security"] = _in_security(
        _with_counter_bound_step(_pine),
    )


@pytest.mark.parametrize("name", sorted(DERIVED_FLOAT_SOURCES))
def test_derived_float_counter_quantities(runs, name):
    _assert_quantities(ok(runs, name), "default", 5)


@pytest.mark.parametrize("name", sorted(NONFINITE_FLOAT_SOURCES))
def test_infinite_float_step_terminates_after_one_iteration(runs, name):
    _assert_quantities(ok(runs, name), "default", 1)


@pytest.mark.parametrize("tail", ["", "plot(acc)\n"], ids=["omitted", "stopped"])
@pytest.mark.parametrize("finite_loop_at", [None, "before", "after"])
def test_removed_float_loops_keep_their_runtime_behavior(tail, finite_loop_at):
    compile_env.skip_if_no_engine_lib()
    removed = (
        "float acc = 0.0\n"
        "for i = 0.0 to i by 0.1\n"
        "    acc += 1\n"
        + tail
    )
    finite = (
        "float count = 0.0\n"
        "for j = 0.0 to 0.3 by 0.1\n"
        "    count += 1\n"
        "plot(count)\n"
    )
    body = (
        finite + removed if finite_loop_at == "before"
        else removed + finite if finite_loop_at == "after"
        else removed
    )
    driver = r'''
#include <cassert>
#include <iostream>
#include <string>
int main() {
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1, "1", "1");
    if (@STOPPED@) {
        assert(strategy.last_error().find("Loop takes too long") != std::string::npos);
    } else {
        assert(strategy.last_error().empty());
    }
    @FINITE_COUNT@
    std::cout << "matched loop behavior";
}
'''
    finite_count = ""
    if finite_loop_at is not None and not tail:
        expected = TV["entry_quantities"]["float_counter_tenth_step_to_three_tenths"]
        finite_count = f"assert(strategy.count == {expected});"
    driver = driver.replace("@STOPPED@", "true" if tail else "false")
    driver = driver.replace("@FINITE_COUNT@", finite_count)
    pine = '//@version=6\nstrategy("Removed float loop", currency=currency.NONE)\n' + body
    assert compile_env.run_emitted_tu(
        transpile(pine), driver, opt="-O2", label="removed_float_loop",
    ) == "matched loop behavior"


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("name", tuple(TV["runtime_errors"]))
def test_nonpositive_step_stops_without_trades(name, context):
    compile_env.skip_if_no_engine_lib()
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    if context == "security":
        pine = _in_security(pine)
    assert compile_env.run_emitted_tu(
        transpile(pine), STOP_DRIVER, opt="-O2", label=f"{name}/{context}",
    ) == "stopped before trades"


@pytest.mark.parametrize("name", sorted(STOP_SOURCES))
def test_baseline_nonpositive_steps_now_stop(name):
    compile_env.skip_if_no_engine_lib()
    # These two forms use TV's stop behavior instead of magnitude or
    # zero-to-one conversion.
    assert compile_env.run_emitted_tu(
        transpile(STOP_SOURCES[name]), STOP_DRIVER, opt="-O2", label=name,
    ) == "stopped before trades"


@pytest.mark.parametrize("header,setup,body,expected", [
    # DERIVED engine controls, without TradingView tapes. All v5 headers
    # use independent bounds and steps: the counter is not in scope there.
    pytest.param("5 to 1 by -2", "", "", sum(range(5, 1 - 1, -2)),
                 id="negative_step_magnitude"),
    pytest.param("0 to 3 by 0", "", "", sum(range(0, 3 + 1)),
                 id="zero_step_defaults_to_one"),
    pytest.param("0 to 4 by 2", "", "", sum(range(0, 4 + 1, 2)),
                 id="positive_literal_step"),
    # The initial stride is cached once even though the body changes it.
    pytest.param(
        "0 to 4 by stride", "    int stride = 1\n", "        stride := 2\n",
        sum(range(0, 4 + 1)),
        id="variable_step_read_once_engine_only",
    ),
])
def test_v5_library_steps_retain_their_existing_handling(header, setup, body, expected):
    """Derived engine checks of valid v5 sources; no TradingView value claim."""
    compile_env.skip_if_no_engine_lib()
    library = (
        '//@version=5\nlibrary("Stride")\n'
        'export total() =>\n    float acc = 0.0\n'
        f'{setup}'
        f'    for walkIndex = {header}\n        acc += walkIndex\n'
        f'{body}'
        '    acc\n'
    )
    pine = (
        '//@version=6\nstrategy("v5 loop step")\n'
        'import pftest/Stride/1 as S\nacc = S.total()\nplot(acc)\n'
    )
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    strategy.run(&bar, 1, "1", "1");
    assert(strategy.last_error().empty());
    std::cout << strategy.acc;
}
'''
    cpp = transpile(pine, libraries={"pftest/Stride/1": library})
    assert compile_env.run_emitted_tu(
        cpp, driver, opt="-O2", label=f"v5/{header}",
    ) == str(expected)

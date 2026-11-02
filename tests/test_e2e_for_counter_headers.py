"""Counter-dependent headers run with TradingView's measured quantities.

The fixture value probes have TradingView measurements.
Renamings and compositions below are derived semantic controls, not additional
TradingView tapes. Every assertion reads the compiled strategy's trade output.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from textwrap import dedent

import pytest

from tests import _compile as compile_env
from pineforge_codegen import transpile
from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env
from tests._for_counter_header_cases import STOP_SOURCES


FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers"
TV = json.loads((FIXTURES / "tradingview_quantities.json").read_text(encoding="utf-8"))
MEASURED = tuple(TV["entry_quantities"])
# Shared parameter inference still truncates this float counter. Run it as
# a documented discrepancy, retaining TradingView's measured 5 in the quantities file.
HELPER_VALUE_GAP = "float_counter_helper_value"
MATCHING_MEASURED = tuple(name for name in MEASURED if name != HELPER_VALUE_GAP)
# These existing measured rows pin the absolute bracket about
# [5.6e-17, 9.98e-11]: 1e-17 loses the fourth 0.3-by-0.1 iteration;
# 1e-10 wrongly admits the fourth 2999.9999999999-by-1000.0 iteration.
ABSOLUTE_TOLERANCE_BOUNDARY_PROBES = (
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
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("for_counter_headers")
    # The seven-bar schedule closes all 205 entries by bar 1441. The eight-bar
    # schedule closes its final entry at bar 1442, so include that bar too.
    feed = chart_feed_head(engine, base, FEED_BARS)
    builds = {}
    for name in (*MEASURED, *ZERO_ENTRY_PROBES):
        # Includes the float-start probes with TradingView's measured
        # quantities and export hashes in tradingview_quantities.json.
        pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
        builds[name] = Build(pine)
        counters = re.findall(r"(?m)^\s*for (\w+) =", pine)
        renamed = re.sub(
            rf"\b{counters[-1] if counters else 'i'}\b", "sampleIndex", pine,
        )
        builds[f"{name}_renamed"] = Build(renamed)
        if name in FIRST_END_READ_PROBES:
            # A requested-context composition of the measured chart source;
            # this is derived coverage, not an additional TradingView tape.
            builds[f"{name}_in_security"] = Build(_in_security(pine))
        if name in FLOAT_CONTINUATION_PROBES:
            bound = _with_counter_bound_step(pine)
            builds[f"{name}_bound"] = Build(bound)
            builds[f"{name}_in_security"] = Build(_in_security(pine))
            builds[f"{name}_bound_in_security"] = Build(_in_security(bound))
    for name in REAL_SCRIPTS:
        builds[name] = Build((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    for name, (pine, _) in CONTROLS.items():
        overrides = {"First": 4} if name == "nonzero_input_start" else None
        builds[name] = Build(pine, overrides=overrides)
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
    expected_entries = TV["entries_per_probe"]
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
    for name in (*MEASURED, *ZERO_ENTRY_PROBES, *TV["runtime_errors"]):
        for field in ("sha256", "source_sha256"):
            assert re.fullmatch(
                r"[0-9a-fA-F]{64}", TV["evidence"][name][field] or ""
            ), (name, field)
    for name, refusal in TV["compile_refusals"].items():
        for field in ("error_json_sha256", "source_sha256"):
            assert re.fullmatch(
                r"[0-9a-fA-F]{64}", refusal[field] or ""
            ), (name, field)


@pytest.mark.parametrize("name", tuple(
    name for name in MATCHING_MEASURED
    if name not in ABSOLUTE_TOLERANCE_BOUNDARY_PROBES
))
def test_tradingview_entry_quantities(runs, name):
    _assert_quantities(
        ok(runs, name), "default", TV["entry_quantities"][name], probe=name,
    )


@pytest.mark.parametrize("name", ABSOLUTE_TOLERANCE_BOUNDARY_PROBES)
def test_tradingview_absolute_tolerance_bracket(runs, name):
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
@pytest.mark.parametrize("suffix", ["_bound", "_in_security", "_bound_in_security"])
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
# the absolute allowance leaves end +/- allowance finite and rejects the
# infinite counter after its first increment.
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

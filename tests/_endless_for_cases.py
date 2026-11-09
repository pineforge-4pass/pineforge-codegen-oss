"""Measured loop outcomes and finite/scope controls."""

import json
import re
from pathlib import Path
from textwrap import dedent


FIXTURES = Path(__file__).parent / "fixtures" / "endless_for"
BASELINE = FIXTURES / "prior_cpp_baseline.json"
TV = json.loads((FIXTURES / "tradingview.json").read_text(encoding="utf-8"))
OMITTED_RUNS = [
    name for name, row in TV["runs"].items() if row.get("loop_action", "omit") == "omit"
]
FUNCTION_RUNS = [
    name for name, row in TV["runs"].items() if row.get("loop_action") == "unchanged"
]


def source(body, tail="plot(acc)\n"):
    return (
        '//@version=6\nstrategy("Loop bounds", currency=currency.NONE)\n'
        + dedent(body).lstrip() + tail
    )


LIVE_REACHABILITY_CONTROLS = {
    "chain_to_order": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
        float chained = acc + 1
    """, 'strategy.entry("L", strategy.long, qty=chained)\n'),
    "condition_to_order": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
        float chained = acc + 1
        if chained > 0
            strategy.entry("L", strategy.long, qty=1)
    """, ""),
}


CONTROLS = {
    "fixed_end": source("""
        float acc = 0.0
        for walkIndex = 0 to 10
            acc += 1
    """),
    "changing_ternary": source("""
        float acc = 0.0
        for walkIndex = 0 to (walkIndex == 0 ? 3 : 1)
            acc += walkIndex + 1
    """),
    "slope_below_one": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex / 2 + 5
            acc += 1
    """),
    "first_read_disagreement": source("""
        float acc = 0.0
        for walkIndex = -20 to walkIndex - 5
            acc += 1
    """),
    "reachable_break": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            acc += 1
            if acc > 1000
                break
    """),
    "function": source("""
        measure() =>
            float subtotal = 0.0
            for walkIndex = 0 to walkIndex
                subtotal += 1
            subtotal
        float acc = measure()
    """),
    "method": source("""
        method measure(float sampleValue) =>
            float subtotal = sampleValue
            for walkIndex = 0 to walkIndex
                subtotal += 1
            subtotal
        float initialValue = 0.0
        float acc = initialValue.measure()
    """),
    "security_helper": source("""
        measure() =>
            float subtotal = 0.0
            for walkIndex = 0 to walkIndex
                subtotal += 1
            subtotal
        float acc = request.security(syminfo.tickerid, timeframe.period, measure())
    """),
    "variable_coefficient": source("""
        slope = input.float(1.0)
        float acc = 0.0
        for walkIndex = 0 to slope * walkIndex
            acc += 1
    """),
    "variable_start": source("""
        first = input.int(0)
        float acc = 0.0
        for walkIndex = first to walkIndex
            acc += 1
    """),
    "variable_step": source("""
        stride = input.int(1)
        float acc = 0.0
        for walkIndex = 0 to walkIndex by stride
            acc += 1
    """),
    "nonpositive_step": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex by 0
            acc += 1
    """),
    "runtime_error": source("""
        float acc = 0.0
        for walkIndex = 0 to walkIndex
            runtime.error("Stopped by the body")
            acc += 1
    """),
}


LATER_LOOP_TARGETS = {
    "later_finite_loop_live": "acc",
    "later_finite_loop_dead": "unusedTotal",
}
LATER_LOOP_SOURCES = {
    name: source(
        "float acc = 0.0\n"
        "float unusedTotal = 0.0\n"
        "for counterValue = 0 to counterValue\n"
        f"    {target} += 1\n"
        "for controlIndex = 0 to 10\n"
        "    acc += controlIndex\n",
    )
    for name, target in LATER_LOOP_TARGETS.items()
}

# Freeze the exact inputs shared by the comparison tests and baseline capture.
BASELINE_SOURCES = {
    **{
        name: (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
        for name in FUNCTION_RUNS
    },
    **CONTROLS,
    **LATER_LOOP_SOURCES,
}

# Keep the whole later loop, including its saved bounds, step and indentation.
LATER_FINITE_LOOP = re.compile(
    r"^(?P<indent>[ \t]*)int _for_start_1 = [^\n]*;\n"
    r".*?^(?P=indent)for \(int controlIndex = [^\n]*\) \{\n"
    r"[ \t]+acc \+= controlIndex;\n"
    r"(?P=indent)\}\n",
    re.MULTILINE | re.DOTALL,
)

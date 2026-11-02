"""Requested selections must use the current requested bar's values."""

import math
import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


BLOCK_IF = """chosen = if bar_index % 2 == 0
    step_v = bar_index * 3
    step_v + 1
else
    bar_index * 2
"""
SINGLE_IF = """chosen = if bar_index % 2 == 0
    bar_index * 3 + 1
else
    bar_index * 2
"""
REQUEST = (
    "got_v = request.security(syminfo.tickerid, timeframe.period, chosen)\n"
)


def alternating_value(bar):
    return 3 * bar + 1 if bar % 2 == 0 else 2 * bar


ROWS = (
    # The even arm yields 3*b+1; the odd arm yields 2*b. Expected red.
    ("block-if", BLOCK_IF + REQUEST, alternating_value),
    # The ternary has the same two formulas. Preservation control.
    ("ternary", "chosen = bar_index % 2 == 0 ? bar_index * 3 + 1 : bar_index * 2\n"
     + REQUEST, alternating_value),
    # Removing the local leaves the same if value; its chart fallback also lags.
    ("expression-if", SINGLE_IF + REQUEST, alternating_value),
    # A direct payload yields 3*b+1 on both parities. Preservation control.
    ("expression", "got_v = request.security(syminfo.tickerid, timeframe.period, "
     "bar_index * 3 + 1)\n", lambda bar: 3 * bar + 1),
    # Without a request the declaration computes the current chart value.
    ("plain-block-if", BLOCK_IF + "got_v = chosen\n", alternating_value),
    # Selector 1 yields (3*b)+2 through two locals. Preservation control.
    ("block-switch", 'pick_n = input.int(1, "Pick", minval = 1, maxval = 2)\n'
     "chosen = switch pick_n\n"
     "    1 =>\n"
     "        base_v = bar_index * 3\n"
     "        shifted_v = base_v + 2\n"
     "        shifted_v\n"
     "    => bar_index\n" + REQUEST, lambda bar: 3 * bar + 2),
    # b adds one to a's selected current value, through a second global.
    ("global-chain", BLOCK_IF.replace("chosen =", "first_v =")
     + "chosen = first_v + 1\n" + REQUEST,
     lambda bar: alternating_value(bar) + 1),
    # A nested tail still selects 3*b+1 on even bars and 2*b otherwise.
    ("nested-if", """chosen = if bar_index % 2 == 0
    step_v = bar_index * 3
    if step_v >= 0
        step_v + 1
    else
        0
else
    bar_index * 2
""" + REQUEST, alternating_value),
    # A helper's terminal if has exactly the same selected value.
    ("helper-if", """selected_v() =>
    if bar_index % 2 == 0
        step_v = bar_index * 3
        step_v + 1
    else
        bar_index * 2
chosen = selected_v()
""" + REQUEST, alternating_value),
    # The selected helper computes 3*b+1 inside its arm; the other arm is 2*b.
    ("helper-arm", """shifted_v(int source_v) =>
    scaled_v = source_v * 3
    scaled_v + 1
chosen = if bar_index % 2 == 0
    shifted_v(bar_index)
else
    bar_index * 2
""" + REQUEST, alternating_value),
)


@pytest.mark.parametrize("name,declarations,value_at", ROWS,
                         ids=[row[0] for row in ROWS])
def test_requested_selection_uses_current_bar(name, declarations, value_at):
    pine = (
        '//@version=6\nstrategy("Current selection", currency = currency.NONE, '
        "initial_capital = 1000000)\n"
        + declarations
        + "qty_v = 1 + got_v % 5\n"
        "if bar_index > 6 and bar_index % 7 == 3\n"
        '    strategy.entry("L", strategy.long, qty = qty_v)\n'
        "if bar_index > 6 and bar_index % 7 == 5\n"
        '    strategy.close("L")\n'
    )
    driver = r'''
#include <iostream>
int main() {
    Bar bars[60];
    for (int i = 0; i < 60; ++i)
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    for (int count = 1; count <= 60; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "15", "15");
        if (!strategy.last_error().empty()) return 1;
        std::cout << strategy.got_v << " " << strategy.qty_v << "\n";
    }
}
'''
    actual = [
        tuple(map(float, line.split()))
        for line in run_emitted_tu(
            transpile(pine), driver, opt="-O2", label=name
        ).splitlines()
    ]
    # Check every bar, including the quantities used on entry bars 10+7*k.
    expected = [(value_at(bar), 1 + value_at(bar) % 5) for bar in range(60)]
    assert actual == expected
    if value_at is alternating_value:
        # Substituting 10,17,24,31,38,45,52 into 1+chosen%5 gives the tape.
        assert [actual[bar][1] for bar in range(10, 53, 7)] == [2, 5, 4, 3, 1, 1, 3]


@pytest.mark.parametrize("payload,expected", (
    # The first requested bar selects these values; the chart's index is still -1.
    # OHLC/time leaves already map to bar; their enclosing condition was wrong.
    ("close", 203.0),
    ("high - low", 17.0),
    ("time", 900000.0),
    # Requested 60-minute bars expose their own timeframe, not the chart's 15.
    ("timeframe.multiplier", 60.0),
))
def test_if_arm_reads_the_requested_context(payload, expected):
    pine = (
        '//@version=6\nstrategy("Requested leaves")\n'
        "chosen = if bar_index == 0\n"
        f"    arm_v = {payload}\n"
        "    arm_v\n"
        "else\n"
        "    0\n"
        'got_v = request.security(syminfo.tickerid, "60", chosen)\n'
    )
    # Exercise the actual evaluator with a bar distinct from the chart's default bar.
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar requested{200.0, 211.0, 194.0, 203.0, 1.0, 900000LL};
    strategy.evaluate_security(0, requested, true);
    std::cout << std::setprecision(17) << strategy._req_sec_0 << "\n";
}
'''
    assert float(run_emitted_tu(
        transpile(pine), driver, opt="-O2", label="arm context")) == expected


@pytest.mark.parametrize("condition", ("bar_index >= 0", "bar_index % 2 == 0"))
def test_if_arm_sma_uses_requested_closes(condition):
    pine = '''//@version=6
strategy("Requested average")
chosen = if bar_index >= 0
    averaged_v = ta.sma(close, 3)
    averaged_v
else
    0.0
got_v = request.security(syminfo.tickerid, timeframe.period, chosen)
'''
    pine = pine.replace("bar_index >= 0", condition)
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[8];
    for (int i = 0; i < 8; ++i)
        bars[i] = Bar{100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 1.0,
                      1743465600000LL + i * 900000LL};
    std::cout << std::setprecision(17);
    for (int count = 1; count <= 8; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "15", "15");
        if (!strategy.last_error().empty()) return 1;
        std::cout << strategy.got_v << "\n";
    }
}
'''
    actual = list(map(float, run_emitted_tu(
        transpile(pine), driver, opt="-O2", label="arm sma").splitlines()))
    assert len(actual) == 8
    # The average consumes only taken-arm closes: all bars, or even bars alone.
    samples = []
    for bar, observed in enumerate(actual):
        taken = condition == "bar_index >= 0" or bar % 2 == 0
        if taken:
            samples.append(101.0 + bar)
        expected = (sum(samples[-3:]) / 3 if len(samples) >= 3 else math.nan) if taken else 0.0
        assert math.isnan(observed) if math.isnan(expected) else observed == expected


def test_request_arm_local_does_not_write_chart_storage():
    pine = (
        '//@version=6\nstrategy("Separate arm storage")\n'
        + BLOCK_IF + REQUEST
        + "float chart_v = na\n"
        "if bar_index >= 0\n"
        "    step_v = bar_index + 100\n"
        "    chart_v := step_v\n"
    )
    cpp = transpile(pine)
    evaluator = cpp.split("void _eval_security_0(", 1)[1].split(
        "void evaluate_security(", 1)[0]
    # A bare assignment would write a chart member; a typed local shadows it.
    assert not re.search(r"(?m)^\s*step_v\s*=", evaluator)
    driver = r'''
#include <iostream>
int main() {
    Bar bars[4];
    for (int i = 0; i < 4; ++i)
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    for (int count = 1; count <= 4; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "15", "15");
        if (!strategy.last_error().empty()) return 1;
        std::cout << strategy.chart_v << "\n";
    }
}
'''
    # The later chart block binds its own step_v to b+100, independently of the arm.
    assert list(map(float, run_emitted_tu(
            cpp, driver, opt="-O2", label="chart block").splitlines())) == [
        100.0, 101.0, 102.0, 103.0,
    ]

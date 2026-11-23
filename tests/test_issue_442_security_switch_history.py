"""Requested helper switch cases must collect built-in bar history (#442)."""

import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '''//@version=6
strategy("history of a built-in series in a switch arm of a request helper")
g() =>
'''
REQUEST = '''result = request.security(syminfo.tickerid, "60", g())
plot(result)
'''

DRIVER = r'''
#include <algorithm>
#include <iostream>

struct RequestedHistoryObserver : GeneratedStrategy {
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (sec_id == 0 && complete) std::cout << _req_sec_0 << '\n';
    }
};

int main() {
    // The last 15-minute close sets the hourly body, including flat hours.
    const double hourly_bodies[] = {4, 0, -6, 7, 8, 0, -10, 11};
    Bar bars[32];
    for (int i = 0; i < 32; ++i) {
        const int hour = i / 4;
        const int quarter = i % 4;
        const double hourly_open = 100.0 + 10.0 * hour;
        const double open = hourly_open + quarter;
        const double body = quarter == 3 ? hourly_bodies[hour] : quarter + 1;
        const double close = hourly_open + body;
        bars[i] = Bar{open, std::max(open, close), std::min(open, close), close,
                      1.0, i * 900000LL};
    }
    RequestedHistoryObserver strategy;
    strategy.run(bars, 32, "15", "15");
    return strategy.last_error().empty() ? 0 : 1;
}
'''


def _completed_values(body, label):
    cpp = transpile(HEADER + body + REQUEST)
    output = run_emitted_tu(cpp, DRIVER, opt="-O2", label=label)
    values = [float(value) for value in output.split()]
    assert len(values) == 8
    return [None if math.isnan(value) else value for value in values]


@pytest.mark.parametrize(
    "switch_body,control_body,expected",
    [
        pytest.param(
            """    switch bar_index % 2
        0 => math.abs(close[2] - open[2])
        => na
""",
            "    bar_index % 2 == 0 ? math.abs(close[2] - open[2]) : na\n",
            [4.0, None, 6.0, None, 8.0, None],
            id="arm",
        ),
        pytest.param(
            """    switch
        bar_index % 2 == 0 => 1.0
        math.abs(close[2] - open[2]) > 0 => 2.0
        => 3.0
""",
            "    bar_index % 2 == 0 ? 1.0 : (math.abs(close[2] - open[2]) > 0 ? 2.0 : 3.0)\n",
            [1.0, 3.0, 1.0, 2.0, 1.0, 3.0],
            id="later-case-condition",
        ),
    ],
)
def test_requested_switch_history_matches_ternary(switch_body, control_body, expected):
    # After two completed hours, [2] has requested history in both spellings.
    control = _completed_values(control_body, "ternary history control")[2:]
    assert control == expected
    actual = _completed_values(switch_body, "switch history in requested helper")[2:]
    assert actual == control

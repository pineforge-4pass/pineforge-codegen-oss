"""A named TA source reads built-in history on the requested timeframe."""

import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


PINE = '''//@version=6
strategy("history of a built-in series in a keyword argument of a request payload")
v = request.security(syminfo.tickerid, "60", ta.sma(source = close[2], length = 3))
plot(v)
control = request.security(syminfo.tickerid, "60", ta.sma(close[2], 3))
plot(control)
'''

DRIVER = r'''
#include <iomanip>
#include <iostream>

struct Observer : GeneratedStrategy {
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (complete) {
            std::cout << sec_id << ' ' << bar.close << ' '
                      << (sec_id == 0 ? _req_sec_0 : _req_sec_1) << '\n';
        }
    }
};

int main() {
    const double hourly_closes[] = {105, 126, 99, 120, 111, 132, 102};
    Bar bars[28];
    for (int i = 0; i < 28; ++i) {
        const double close = hourly_closes[i / 4] + 10 * (3 - i % 4);
        bars[i] = Bar{close, close + 1, close - 1, close, 1.0,
                      1743465600000LL + i * 900000LL};
    }
    std::cout << std::setprecision(17);
    Observer strategy;
    strategy.run(bars, 28, "15", "15");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
}
'''


def test_named_sma_source_reads_requested_close_history():
    output = run_emitted_tu(transpile(PINE), DRIVER, opt="-O2", label="issue #441")
    closes = {0: [], 1: []}
    values = {0: [], 1: []}
    for line in output.splitlines():
        sec_id, close, value = line.split()
        closes[int(sec_id)].append(float(close))
        values[int(sec_id)].append(float(value))

    for sec_id in (0, 1):
        assert closes[sec_id] == [105, 126, 99, 120, 111, 132, 102]
        assert all(math.isnan(value) for value in values[sec_id][:4])
    # At hour 4 (zero based), close[2] first supplies the third SMA
    # sample: the closes of hours 0, 1 and 2. The window then rolls.
    assert values[1][4:] == pytest.approx([110, 115, 110])
    assert values[0][4:] == pytest.approx(values[1][4:])

"""Requested history reached through an offset, switch pair, or named argument."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


DRIVER = r'''
#include <algorithm>
#include <iostream>

struct Observer : GeneratedStrategy {
    void evaluate_security(int id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(id, bar, complete);
        if (id == 0 && complete) std::cout << _req_sec_0 << '\n';
    }
};

int main() {
    const double closes[] = {20, 20, 20, 44, 80, 14, 32, 68};
    Bar bars[32];
    for (int i = 0; i < 32; ++i) {
        const int hour = i / 4;
        const double open = i % 4 == 0 && (hour == 3 || hour == 5) ? -5 : 5;
        const double close = closes[hour] + 10 * (3 - i % 4);
        bars[i] = Bar{open, std::max(open, close), std::min(open, close), close,
                      1.0, 1743465600000LL + i * 900000LL};
    }
    Observer strategy;
    strategy.run(bars, 32, "15", "15");
    return strategy.last_error().empty() ? 0 : 1;
}
'''


def _hours(payload, helper=""):
    pine = ('//@version=6\nstrategy("requested history")\n' + helper
            + f'v = request.security(syminfo.tickerid, "60", {payload})\nplot(v)\n')
    out = run_emitted_tu(transpile(pine), DRIVER, opt="-O2", label=payload)
    hours = [float(v) for v in out.split()]
    assert len(hours) == 8
    return hours


def test_dynamic_offset_in_named_source():
    hours = _hours("ta.sma(source = close[open[1] > 0 ? 1 : 2], length = 3)")
    # From hour 1, the selected prior closes are 20, 20, 20, 20, 80, 80, 32.
    assert hours[3:] == pytest.approx([20, 20, 40, 60, 64])


def test_dynamic_offset_in_helper_scope():
    hours = _hours("readAt(close, open)",
                   "readAt(src, signSrc) => src[signSrc[1] > 0 ? 1 : 2]\n")
    assert hours[1:] == [20, 20, 20, 20, 80, 80, 32]


@pytest.mark.parametrize("payload,helper", [
    pytest.param("prior()", """prior() =>
    switch
        close > 0 => ta.ema(close, 3)[1]
        => -1.0
""", id="switch-arm"),
    # The TA binder keeps this source in kwargs; math.abs(number=...) is
    # normalized to positional syntax before the collector sees it.
    pytest.param("ta.sma(source = ta.ema(close, 3)[1], length = 1)", "", id="keyword"),
])
def test_ta_history_in_container(payload, helper):
    # Three identical hourly closes seed EMA(3) at 20. Each later
    # completed close gets weight 1/2; [1] returns the previous EMA.
    assert _hours(payload, helper)[3:] == [20, 32, 56, 35, 33.5]

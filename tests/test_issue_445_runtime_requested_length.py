"""A daily SMA created at runtime on a 15-minute chart uses the daily length."""

import math

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


def test_open_timeframe_sma_matches_daily_five():
    pine = '''//@version=6
strategy("run-time timeframe length")
chosen_tf = bar_index >= 0 ? "D" : str.tostring(bar_index)
x = request.security(syminfo.tickerid, chosen_tf, ta.sma(close, timeframe.isdaily ? 5 : 3))
plot(x)
five = request.security(syminfo.tickerid, "D", ta.sma(close, 5))
three = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
'''
    cpp = transpile(pine)
    assert "_runtime_request_0(chosen_tf" in cpp, "NOT COMPARED: no runtime request"
    driver = r'''
#include <iomanip>
#include <iostream>
struct Observer : GeneratedStrategy {
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << x << ' ' << five << ' ' << three << '\n';
    }
};
int main() {
    Observer strategy;
    Bar bars[12 * 96];
    std::cout << std::setprecision(17);
    for (int run = 0; run < 2; ++run) {
        for (int i = 0; i < 12 * 96; ++i) {
            const double price = 100 + run * 1000 + i / 96;
            bars[i] = Bar{price, price + 1, price - 1, price, 1.0,
                          1743465600000LL + i * 900000LL};
        }
        strategy.run(bars, 12 * 96, "15", "15");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
    }
}
'''
    rows = [tuple(map(float, line.split())) for line in run_emitted_tu(
        cpp, driver, opt="-O2", label="issue 445 daily SMA").splitlines()]
    bars = 12 * 96
    assert len(rows) == 2 * bars, "NOT COMPARED: incomplete chart runs"
    for start in (0, bars):
        warm = rows[start + 7 * 96:start + bars]
        assert all(math.isfinite(v) for row in warm for v in row), (
            "NOT COMPARED: a request did not warm up")
        assert len({x for x, _, _ in warm}) > 1, "NOT COMPARED: no changing runtime result"
        assert len({five for _, five, _ in warm}) > 1 and all(
            five != three for _, five, three in warm
        ), "NOT COMPARED: daily controls did not distinguish the lengths"
        n5 = sum(x == five for x, five, _ in warm)
        n3 = sum(x == three for x, _, three in warm)
        assert n5 == len(warm), f"of {len(warm)} bars: D/5 matches={n5}, D/3 matches={n3}"

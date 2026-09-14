"""Runtime pins for generated source-host risk setter behaviour.

The text tests in :mod:`tests.test_source_host_config_codegen` establish the
Pine-to-host argument encoding. These probes compile and run generated C++
against the engine so the host's interpretation of those arguments remains
observable at the strategy boundary.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


_SHORT_ONLY_SOURCE = '''//@version=6
strategy("short-only risk direction", initial_capital=1000,
    default_qty_type=strategy.fixed, default_qty_value=1)
strategy.risk.allow_entry_in(strategy.direction.short)
if bar_index == 0
    strategy.entry("long", strategy.long)
'''


def test_generated_short_only_direction_drops_a_long_entry() -> None:
    cpp = transpile(_SHORT_ONLY_SOURCE)
    driver = r'''
#include <iostream>

int main() {
    Bar bars[] = {
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 120000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 180000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.live_position_size() != 0.0) return 3;
    if (strategy.trade_count() != 0) return 4;
    std::cout << "short-direction-ok\n";
}
'''
    assert _compile_and_run(cpp + driver) == "short-direction-ok\n"


_STICKY_PERCENT_SOURCE = '''//@version=6
strategy("sticky max drawdown percent flag", initial_capital=1000,
    default_qty_type=strategy.fixed, default_qty_value=10)
strategy.risk.max_drawdown(10, strategy.percent_of_equity)
strategy.risk.max_drawdown(500, strategy.cash)
if bar_index == 0
    strategy.entry("loss", strategy.long)
if bar_index == 1
    strategy.close("loss")
if bar_index == 3
    strategy.entry("after", strategy.long)
'''


def test_generated_percent_drawdown_flag_stays_sticky_after_cash_call() -> None:
    cpp = transpile(_STICKY_PERCENT_SOURCE)
    driver = r'''
#include <iostream>

int main() {
    // The first entry fills at 100, the deferred close fills at 40, and its
    // 600-cash loss is above the later 500 value. If that later value clears
    // the percent flag, the risk halt drops the bar-three entry. A sticky
    // percent flag instead interprets 500 as 500% of peak equity, allowing it.
    Bar bars[] = {
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 120000},
        Bar{40.0, 40.0, 40.0, 40.0, 1.0, 180000},
        Bar{40.0, 40.0, 40.0, 40.0, 1.0, 240000},
        Bar{40.0, 40.0, 40.0, 40.0, 1.0, 300000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 5);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.trade_count() != 1) return 3;
    if (strategy.live_position_size() != 10.0) return 4;
    std::cout << "sticky-percent-ok\n";
}
'''
    assert _compile_and_run(cpp + driver) == "sticky-percent-ok\n"

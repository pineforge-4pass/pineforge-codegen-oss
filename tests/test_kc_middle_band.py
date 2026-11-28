"""KC's middle band follows TradingView's warm-up EMA basis.

The covered TradingView tape in ``/tmp/CG-NABOOL-scratch/tv-kc`` records both
the KC middle and an independent EMA as ``na`` on bar 0, first finite at bar
19. The generated KC shim applies that warm-up to its internal basis and
leaves the ambient seeding as it found it. The standalone ``ta.ema`` follows
the engine's own chart rule, which is the same since pineforge-engine 3c2fc33a
(#1059): ``na`` until ``length`` inputs exist, then their simple average, as
on TradingView (in the engine at 9f900bbf: ``CHANGELOG.md:42``,
``include/pineforge/source/pine_strategy_host.hpp:1615-1621``,
``src/ta_moving_averages.cpp:307-328``). The driver below pins both readings:
``na`` after one bar, finite after twenty. With closes 100..119 and length 20
the twentieth bar is the first with 20 inputs, and the EMA starts there at
their mean, 109.5.
"""

from __future__ import annotations

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import Level

from tests._compile import run_emitted_tu


_PINE = """//@version=6
strategy("kc middle")
[m, u, l] = ta.kc(close, 20, 1.5)
var float mid = na
var float ema = na
mid := m
ema := ta.ema(close, 20)
"""

_DRIVER = r"""
#include <cmath>
#include <cstdio>
int main() {
    pineforge::Bar bars[20];
    for (int i = 0; i < 20; ++i) {
        bars[i] = pineforge::Bar{100.0 + i, 101.0 + i, 99.0 + i,
                                 100.0 + i, 1.0,
                                 1743466500000LL + i * 900000};
    }
    GeneratedStrategy first;
    first.run(bars, 1);
    GeneratedStrategy warm;
    warm.run(bars, 20);
    std::printf("%d %d %d %d\n", std::isnan(first.mid), std::isnan(first.ema),
                std::isnan(warm.mid), std::isnan(warm.ema));
}
"""


def test_kc_middle_warms_without_changing_standalone_ema() -> None:
    out = run_emitted_tu(
        transpile(_PINE), _DRIVER, opt="-O0", label="kc-middle-band"
    )
    first_m, first_e, warm_m, warm_e = out.strip().split()
    assert first_m == "1"
    # One bar is 1 of the 20 inputs the standalone EMA needs: na, as the middle.
    assert first_e == "1"
    assert warm_m == "0"
    assert warm_e == "0"
    assert warm_m == warm_e


def test_standalone_ema_warmup_gap_is_a_note() -> None:
    diagnostics = transpile_full(_PINE)["diagnostics"]
    assert any(d.code == "PF-W1505" and d.level == Level.NOTE
               and "first value" in d.message
               for d in diagnostics)

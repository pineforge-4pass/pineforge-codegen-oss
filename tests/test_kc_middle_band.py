"""KC's middle band follows TradingView's warm-up EMA basis.

The covered TradingView tape in ``/tmp/CG-NABOOL-scratch/tv-kc`` records both
the KC middle and an independent EMA as ``na`` on bar 0, first finite at bar
19. The generated KC shim applies that warm-up to its internal basis. The
standalone engine EMA still has its historical finite bar-0 behavior; the
driver below pins that limited scope, not TradingView parity for standalone
EMA.
"""

from __future__ import annotations

from pineforge_codegen import transpile, transpile_full

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
    assert first_e == "0"
    assert warm_m == "0"
    assert warm_e == "0"
    assert warm_m == warm_e


def test_standalone_ema_warmup_gap_warns() -> None:
    diagnostics = transpile_full(_PINE)["diagnostics"]
    assert any("ta.ema initial warmup is approximated" in d.message
               for d in diagnostics)

"""KC's middle band is the EMA basis from the first chart bar.

The TradingView tape in ``/tmp/CG-NABOOL-scratch/tv-kc`` records both
``ta.kc(...).middle`` and an independent ``ta.ema`` as finite at the same
first-value bar.  This runtime witness keeps the paired engine/codegen path
from regressing to the old all-bands-warm-up approximation.
"""

from __future__ import annotations

from pineforge_codegen import transpile

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
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %.17g %.17g\n", std::isnan(s.mid), s.mid, s.ema);
}
"""


def test_kc_middle_matches_ema_on_bar_zero() -> None:
    out = run_emitted_tu(
        transpile(_PINE), _DRIVER, opt="-O0", label="kc-middle-band"
    )
    missing, middle, ema = out.strip().split()
    assert missing == "0"
    assert middle == ema

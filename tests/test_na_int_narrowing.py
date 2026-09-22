"""na-preserving ``double`` -> ``int`` narrowing in the emitted C++.

Pine's ``na`` is a NaN at the ``double`` level and the engine's integer
sentinel (``na<int>() == INT_MIN``, ``include/pineforge/na.hpp``) at the
integer level. Whenever codegen puts a ``double``-valued expression into an
``int``-typed slot it must translate between the two: an *implicit* narrowing
of a NaN is undefined behaviour ([conv.fpint]), and the compilers disagree
about it — AppleClang arm64 and g++ aarch64 yield 0 at every ``-O``, g++
x86-64 yields ``INT_MIN`` at ``-O0``/``-O1`` and 0 at ``-O2``/``-O3``. One
strategy therefore booked 2412 trades built at ``-O3`` and 2411 (TradingView's
count) at ``-O1`` from the same source.

The three checks here are layered:

* the unit witness pins the emitted *text* for the minimal reproducer;
* the class check asks the compiler for every implicit narrowing left in a
  battery of emissions — the same ``-Wfloat-conversion`` oracle that found
  the population-wide site list, so a new emission path that reintroduces a
  raw narrowing fails here rather than in a backtest;
* the behavioural witness compiles and *runs* the emitted translation unit
  against the real engine at two optimisation levels and reads the integer
  back, which is the only check that can see a UB resolution.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile

from ._compile import narrowing_diagnostics, run_emitted_tu


# Minimal reproducer: ``ta.sma`` is ``na`` through its warm-up window, so the
# ``int`` variable is fed a NaN on the first bars. Shape of closed bench slot
# 181's ``t5Score = math.round(math.max(-100, math.min(100, <na-able sum>)))``.
NA_INT_PINE = """//@version=6
strategy("na int narrowing", overlay=true)
naSrc = ta.sma(close, 20)
naInt = math.round(naSrc)
"""


def _assignment_line(cpp: str, target: str) -> str:
    """The single emitted statement assigning ``target``."""
    hits = [
        ln for ln in cpp.splitlines()
        if re.match(rf"\s*{re.escape(target)} = ", ln)
    ]
    assert len(hits) == 1, f"expected one assignment to {target}, got {hits}"
    return hits[0]


def test_int_var_from_math_round_uses_na_preserving_cast():
    """``int`` <- ``math.round(<na-able>)`` goes through the na-preserving cast.

    Fails before the fix with the raw ``naInt = std::round(naSrc);``.
    """
    cpp = transpile(NA_INT_PINE)
    assert "    int naInt = 0;" in cpp, "naInt must still be an int-typed slot"
    line = _assignment_line(cpp, "naInt")
    assert "is_na(" in line and "na<int>()" in line, (
        "int-typed assignment of a double expression must preserve na:\n" + line
    )
    assert not re.search(r"naInt = std::round\(", line), (
        "raw double->int narrowing (UB for NaN) still emitted:\n" + line
    )


# One Pine snippet per emission site that can put a double-valued expression
# into an int slot. Each must emit a translation unit with no implicit
# narrowing left.
NARROWING_SITES = {
    "assignment": """//@version=6
strategy("s", overlay=true)
var int slot = 0
slot := math.round(ta.sma(close, 20))
""",
    "var_decl": """//@version=6
strategy("s", overlay=true)
slot = math.round(ta.sma(close, 20))
plot(slot)
""",
    "na_reassign": """//@version=6
strategy("s", overlay=true)
var int slot = 0
if close > open
    slot := na
plot(slot)
""",
    "series_push": """//@version=6
strategy("s", overlay=true)
slot = math.floor(ta.sma(close, 20))
prev = slot[1]
plot(prev)
""",
    "function_argument": """//@version=6
strategy("s", overlay=true)
f(int n) => n * 2
plot(f(math.ceil(ta.sma(close, 20))))
""",
    "function_return": """//@version=6
strategy("s", overlay=true)
f() =>
    int r = 0
    r := math.round(ta.sma(close, 20))
    r
plot(f())
""",
    "ternary": """//@version=6
strategy("s", overlay=true)
slot = close > open ? math.round(ta.sma(close, 20)) : 0
plot(slot)
""",
    "for_bounds": """//@version=6
strategy("s", overlay=true)
n = math.round(ta.sma(close, 20))
total = 0.0
for i = 0 to n
    total += 1
plot(total)
""",
    "int_input": """//@version=6
strategy("s", overlay=true)
len = input.int(14, "len")
slot = math.round(ta.sma(close, len))
plot(slot)
""",
    "compound_assign": """//@version=6
strategy("s", overlay=true)
var int acc = 0
acc += math.round(ta.sma(close, 20))
plot(acc)
""",
    "for_bounds_from_array_size": """//@version=6
strategy("s", overlay=true)
var a = array.new<float>(0)
a.push(close)
total = 0.0
for i = 0 to a.size() - 1
    total += a.get(i)
plot(total)
""",
    "runtime_var_init": """//@version=6
strategy("s", overlay=true)
var int anchor = bar_index - ta.highestbars(high, 20)
plot(anchor)
""",
    "color_parameter": """//@version=6
strategy("s", overlay=true)
paint(color c) => na(c) ? 0 : 1
plot(paint(na))
""",
    "trade_accessor_index": """//@version=6
strategy("s", overlay=true)
idx = math.round(ta.sma(close, 20))
p = strategy.closedtrades > 0 ? strategy.closedtrades.profit(idx) : 0.0
plot(p)
""",
    "udt_int_field": """//@version=6
strategy("s", overlay=true)
type Zone
    int strength
var z = Zone.new(0)
z.strength := math.round(ta.sma(close, 20))
plot(z.strength)
""",
    "int_series_history": """//@version=6
strategy("s", overlay=true)
var int state = 0
state := math.round(ta.sma(close, 20))
prev = state[1]
plot(prev)
""",
    "timestamp_calendar_fields": """//@version=6
strategy("s", overlay=true)
h = math.round(ta.sma(close, 20))
ts = timestamp(2024, 1, 1, h, 0, 0)
plot(ts > 0 ? 1 : 0)
""",
}


@pytest.mark.parametrize("site", sorted(NARROWING_SITES))
def test_no_implicit_double_to_int_narrowing(site):
    """The emitted TU contains no implicit floating-point -> integer conversion.

    The compiler is the oracle (``-Wfloat-conversion``); an explicit cast —
    which every na-preserving lowering is — is silent.
    """
    hits = narrowing_diagnostics(transpile(NARROWING_SITES[site]), label=f"{site}.cpp")
    assert hits == [], (
        f"{site}: implicit double->int narrowing left in the emitted C++:\n"
        + "\n".join(hits)
    )


# Reads the int back out of the emitted class after one bar. ``ta.sma(close,
# 20)`` is na on bar 1, so the engine contract says the int reads na<int>().
_NA_INT_DRIVER = """
#include <cstdio>
#include <limits>

int main() {
    GeneratedStrategy s;
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.5, 10.0, 1700000000000LL};
    s.on_source_bar(bar);
    std::printf("naInt=%d expected=%d\\n", s.naInt,
                std::numeric_limits<int>::min());
    return 0;
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_emitted_tu_reads_na_int_at_every_optimisation_level(opt):
    """The *running* TU yields ``na<int>()`` for an na-fed int, at every -O.

    Fails before the fix: the implicit narrowing reads 0 on AppleClang arm64 at
    every level and on g++ x86-64 at -O2/-O3, which is the wrong value at every
    is_na() test downstream.
    """
    out = run_emitted_tu(
        transpile(NA_INT_PINE), _NA_INT_DRIVER, opt=opt, label="na-int"
    )
    assert out.strip() == "naInt=-2147483648 expected=-2147483648", (
        f"emitted TU at {opt} did not preserve na through the int slot: {out!r}"
    )

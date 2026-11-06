"""A typed ``array.from`` keeps one element type in every context it can be declared in.

``array<int> s = array.from(time, time[1])`` and ``array<float> g = array.from(1, time)``
are declared with an explicit element type. The member the generated class
declares and the constructor that fills it must agree, in every place the
declaration can sit: global and ``var``, inside a function (``var`` and ``varip``),
and not ``var`` at all. An epoch in milliseconds does not
fit 32 bits, so an ``int`` array of timestamps is stored as 64-bit and a narrow
constructor either does not build or stores the wrong number.

Each row translates a real Pine source, builds the emitted C++ against the engine
and runs four bars at two different first-bar timestamps (both beyond 2**31).
The expected numbers are the timestamps the driver puts into the bars, not
values read back from the code under test.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu

HEAD = '//@version=6\nstrategy("typed array contexts")\n'

# name -> (Pine body, whether the source also defines o1, expected (o0, o1) as a function of the four bar times)
SHAPES = {
    # Declared at global scope, filled once on the first bar.
    "global_var_int": (
        "var array<int> s = array.from(time, time[1])\n"
        "o0 = array.get(s, 0)\n",
        False,
        lambda t: (t[0], None),
    ),
    # Inside a function, filled once on the first call: the width has to survive the function scope.
    "function_var_int": (
        "stampsFirst() =>\n"
        "    var array<int> s = array.from(time, time[1])\n"
        "    array.get(s, 0)\n"
        "o0 = stampsFirst()\n",
        False,
        lambda t: (t[0], None),
    ),
    "function_varip_int": (
        "stampsFirst() =>\n"
        "    varip array<int> s = array.from(time, time[1])\n"
        "    array.get(s, 0)\n"
        "o0 = stampsFirst()\n",
        False,
        lambda t: (t[0], None),
    ),
    # Not var: rebuilt on every bar, so the second element is a real previous timestamp.
    "nonvar_int": (
        "array<int> s = array.from(time, time[1])\n"
        "o0 = array.get(s, 0)\n"
        "o1 = array.get(s, 1)\n",
        True,
        lambda t: (t[3], t[2]),
    ),
    # Float arrays built from an int and an epoch.
    "global_var_float": (
        "var array<float> g = array.from(1, time)\n"
        "o0 = array.get(g, 1)\n",
        False,
        lambda t: (t[0], None),
    ),
    "function_var_float": (
        "pickEpoch() =>\n"
        "    var array<float> g = array.from(1, time)\n"
        "    array.get(g, 1)\n"
        "o0 = pickEpoch()\n",
        False,
        lambda t: (t[0], None),
    ),
    "function_varip_float": (
        "pickEpoch() =>\n"
        "    varip array<float> g = array.from(1, time)\n"
        "    array.get(g, 1)\n"
        "o0 = pickEpoch()\n",
        False,
        lambda t: (t[0], None),
    ),
}

FIRST_BAR_TIMES = (1700000000000, 4102444800000)
BAR_STEP_MS = 60000


def _driver(reads_second: bool) -> str:
    second = ' << " " << strategy.o1' if reads_second else ""
    return rf"""
#include <iomanip>
#include <iostream>
int main() {{
    const long long first_times[] = {{{FIRST_BAR_TIMES[0]}LL, {FIRST_BAR_TIMES[1]}LL}};
    for (long long t0 : first_times) {{
        GeneratedStrategy strategy;
        Bar bars[4] = {{
            {{100.0, 100.0, 100.0, 100.0, 1.0, t0}},
            {{100.0, 100.0, 100.0, 100.0, 1.0, t0 + {BAR_STEP_MS}LL}},
            {{100.0, 100.0, 100.0, 100.0, 1.0, t0 + 2 * {BAR_STEP_MS}LL}},
            {{100.0, 100.0, 100.0, 100.0, 1.0, t0 + 3 * {BAR_STEP_MS}LL}},
        }};
        strategy.run(bars, 4);
        std::cout << std::fixed << std::setprecision(0) << strategy.o0{second} << "\n";
    }}
}}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_typed_array_from_builds_and_holds_the_timestamps(shape: str, opt: str):
    body, reads_second, expected_of = SHAPES[shape]
    cpp = transpile(HEAD + body)
    out = run_emitted_tu(cpp, _driver(reads_second), opt=opt, label=f"typed-array-from {shape}")
    expected_lines = []
    for t0 in FIRST_BAR_TIMES:
        times = [t0 + i * BAR_STEP_MS for i in range(4)]
        first, second = expected_of(times)
        expected_lines.append(f"{first}" + (f" {second}" if reads_second else ""))
    assert out == "\n".join(expected_lines) + "\n"

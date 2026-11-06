"""A 32-bit integer na that meets a 64-bit integer context stays na.

``int(<floating value>)`` and a stored ``math.floor`` / ``ceil`` / ``round`` are 64-bit
integers (Pine's int is 64-bit), while ``int(na)``, ``bar_index`` and the other narrow
sources are 32-bit ``int`` in the emitted C++. Where the two meet in one construct, C++
widens the narrow one implicitly and its na pattern (INT32_MIN) becomes the finite
number -2147483648: a missing value that is no longer missing. Each row below puts both
widths into one construct of the emitted code, runs it on bars that select the missing
side and bars that select the value side (the value is ``int(close)``, with closes beyond
32 bits, a fraction and a negative close), and compares what the global ``outv`` holds
with what Pine gives.

Where the expected numbers come from: Pine's own semantics, computed here from the bars
(a selection returns the selected arm, ``nz`` replaces na, ``fixnan`` carries the last
value forward, arithmetic with na is na, and a store keeps na). For ``math.max`` /
``math.min`` / ``math.abs`` the na-ness is that of the same construct over two 32-bit
sources (``twin``), printed by the same run, and the value is the function of
``int(close)``. No expected number is read from the code under test. No TradingView
capture covers these sources: the label of every row is ARITHMETIC.

The rows run the emitted C++ against the engine library, so they skip where it is absent.
"""

import pytest

from pineforge_codegen import transpile

HEAD = '//@version=6\nstrategy("integer na widths")\n'

# (open, close). A bar is "up" when close > open: then the missing side is selected.
BARS = [
    (100.0, 105.0),     # up
    (100.0, 95.75),     # down: int() truncates to 95
    (100.0, 101.0),     # up
    (4.0e9, 3.0e9),     # down: a value beyond 32 bits
    (100.0, 90.0),      # down
    (100.0, 3.5e9),     # up: a close beyond 32 bits on the missing side
    (4.0e9, 2.5e9),     # down: beyond 32 bits again
    (100.0, -7.5),      # down: int() truncates toward zero to -7
]

# The callables read ``close`` and ``open`` themselves: a float PARAMETER cast by int() is
# not resolved as floating yet (a limit of the 64-bit int() change, listed in its notes).
PICK_BRANCHES = (
    "pick() =>\n"
    "    int held = 0\n"
    "    if close > open\n"
    "        held := int(na)\n"
    "    else\n"
    "        held := int(close)\n"
    "    held\n"
)
PICK_IF_RESULT = (
    "pick() =>\n"
    "    if close > open\n"
    "        int(na)\n"
    "    else\n"
    "        int(close)\n"
)
PICK_TERNARY = "pick() => close > open ? int(na) : int(close)\n"
# A tuple-valued selection joins each position of its arms: an arm that gives a 32-bit ``int``
# (a local typed int, here na on the bars that select it) and an arm that gives an ``int64_t``
# (``int(close)``) make the position ``int64_t``. A literal ``[int(na), 1]`` is NOT this shape:
# its position joins to ``double``, which the translator already converts na-preservingly. The
# arms end in tuple literals (if, and switch with the wide arm first) or in a call of a
# tuple-returning function.
PICK_TUPLE_IF = (
    "pick() =>\n"
    "    int held = int(na)\n"
    "    if close > open\n"
    "        [held, 1]\n"
    "    else\n"
    "        [int(close), 2]\n"
)
# The narrow local is na on the up bars and a present 32-bit value (the bar index) on the others,
# and the arm is chosen by the bar index, so the bars cover a narrow na, a narrow value and a
# value beyond 32 bits.
PICK_TUPLE_IF_MIXED = (
    "pick() =>\n"
    "    int held = close > open ? int(na) : bar_index\n"
    "    if bar_index % 2 == 0\n"
    "        [held, 1]\n"
    "    else\n"
    "        [int(close), 2]\n"
)
PICK_TUPLE_SWITCH = (
    "pick() =>\n"
    "    int held = int(na)\n"
    "    switch\n"
    "        close <= open => [int(close), 2]\n"
    "        => [held, 1]\n"
)
PICK_TUPLE_CALL = (
    "narrow() =>\n"
    "    int held = int(na)\n"
    "    [held, 1]\n"
    "pick() =>\n"
    "    if close > open\n"
    "        narrow()\n"
    "    else\n"
    "        [int(close), 2]\n"
)
SECURITY = 'request.security(syminfo.tickerid, "1", {})'

# name -> (Pine body defining ``outv`` (and ``twin`` where the kind needs it), kind of expectation)
ROWS = {
    "ternary": (
        "int outv = close > open ? int(na) : int(close)\n", "select"),
    "if_expression": (
        "int outv = if close > open\n    int(na)\nelse\n    int(close)\n", "select"),
    "switch_expression": (
        "int outv = switch\n    close > open => int(na)\n    => int(close)\n", "select"),
    "nz_wide_first": (
        "int outv = nz(close > open ? int(na) : int(close), 7)\n", "nz_seven"),
    "nz_narrow_first": (
        "int outv = nz(close > open ? int(na) : bar_index, int(close))\n", "nz_narrow_first"),
    "fixnan_carry": (
        "int outv = fixnan(close > open ? int(na) : int(close))\n", "fixnan"),
    "math_max": (
        "int outv = math.max(close > open ? int(na) : int(close), 1)\n"
        "int twin = math.max(close > open ? int(na) : bar_index, 1)\n", "max_one"),
    "math_max_reversed": (
        "int outv = math.max(1, close > open ? int(na) : int(close))\n"
        "int twin = math.max(1, close > open ? int(na) : bar_index)\n", "max_one"),
    "math_min": (
        "int outv = math.min(close > open ? int(na) : int(close), 100)\n"
        "int twin = math.min(close > open ? int(na) : bar_index, 100)\n", "min_hundred"),
    "math_abs": (
        "int outv = math.abs(close > open ? int(na) : int(close))\n"
        "int twin = math.abs(close > open ? int(na) : bar_index)\n", "absolute"),
    "compound_add": (
        "int outv = int(close)\noutv += close > open ? int(na) : int(close)\n", "doubled"),
    "slot_reassign": (
        "var int outv = 0\noutv := close > open ? int(na) : int(close)\n", "select"),
    "slot_narrow_assign": (
        "int outv = int(close)\noutv := close > open ? int(na) : bar_index\n", "narrow_select"),
    "array_set": (
        "a = array.new<int>(1, 0)\n"
        "array.set(a, 0, close > open ? int(na) : int(close))\n"
        "int outv = array.get(a, 0)\n", "select"),
    "array_push": (
        "a = array.new<int>()\n"
        "array.push(a, close > open ? int(na) : int(close))\n"
        "int outv = array.get(a, 0)\n", "select"),
    "array_narrow_into_wide": (
        "a = array.new<int>(2, 0)\n"
        "array.set(a, 0, int(close))\n"
        "array.set(a, 1, close > open ? int(na) : bar_index)\n"
        "int outv = array.get(a, 1)\n", "narrow_select"),
    "function_branches": (
        PICK_BRANCHES + "int outv = pick()\n", "select"),
    "function_if_result": (
        PICK_IF_RESULT + "int outv = pick()\n", "select"),
    "function_ternary_result": (
        PICK_TERNARY + "int outv = pick()\n", "select"),
    # The joined position of a tuple-valued selection: ``outv`` is the first element, which is
    # na on the up bars (the narrow local's arm) and ``int(close)`` on the others.
    "tuple_function_if": (
        PICK_TUPLE_IF + "[outv, other] = pick()\n", "select"),
    "tuple_function_if_mixed": (
        PICK_TUPLE_IF_MIXED + "[outv, other] = pick()\n", "tuple_mixed"),
    "tuple_function_switch": (
        PICK_TUPLE_SWITCH + "[outv, other] = pick()\n", "select"),
    "tuple_function_call_arm": (
        PICK_TUPLE_CALL + "[outv, other] = pick()\n", "select"),
    "tuple_top_level_if": (
        "int held = int(na)\n"
        "[outv, other] = if close > open\n    [held, 1]\nelse\n    [int(close), 2]\n",
        "select"),
    "security_ternary": (
        "float outv = " + SECURITY.format("close > open ? int(na) : int(close)") + "\n",
        "select"),
    "security_nz": (
        "float outv = " + SECURITY.format("nz(close > open ? int(na) : int(close), 7)") + "\n",
        "nz_seven"),
    "security_math_max": (
        "float outv = " + SECURITY.format("math.max(close > open ? int(na) : int(close), 1)")
        + "\nint twin = math.max(close > open ? int(na) : bar_index, 1)\n", "max_one"),
    "security_function": (
        PICK_BRANCHES + "float outv = " + SECURITY.format("pick()") + "\n",
        "select"),
    # The payload's own copy of a script variable that is int64_t on the chart: a value
    # beyond 32 bits through the copy, and the missing value through the copy.
    "security_scalar_copy": (
        "var int held = 0\nheld := int(close)\n"
        "float outv = " + SECURITY.format("held + 1") + "\n", "plus_one"),
    "security_scalar_copy_selected": (
        "var int held = 0\nheld := close > open ? int(na) : int(close)\n"
        "float outv = " + SECURITY.format("held + 1") + "\n", "plus_one_select"),
}

DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

template <class T> void show(char tag, unsigned count, const T& value) {
    if constexpr (std::is_floating_point_v<T>) {
        if (std::isnan(value)) { std::printf("%c %u na\n", tag, count); return; }
        std::printf("%c %u %.17g\n", tag, count, static_cast<double>(value));
    } else {
        if (pineforge::is_na(value)) { std::printf("%c %u na\n", tag, count); return; }
        std::printf("%c %u %lld\n", tag, count, static_cast<long long>(value));
    }
}

int main() {
    const double opens[] = {OPENS};
    const double closes[] = {CLOSES};
    constexpr unsigned size = sizeof(closes) / sizeof(closes[0]);
    pineforge::Bar bars[size];
    for (unsigned index = 0; index < size; ++index) {
        const double high = (opens[index] > closes[index] ? opens[index] : closes[index]) + 1.0;
        const double low = (opens[index] < closes[index] ? opens[index] : closes[index]) - 1.0;
        bars[index] = pineforge::Bar{opens[index], high, low, closes[index], 10.0,
                                     1704067200000LL + index * 60000LL};
    }
    // A fresh strategy per prefix: the value after the last bar of the prefix.
    for (unsigned count = 1; count <= size; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "1", "1");
        if (!strategy.last_error().empty()) return 1;
        show('O', count, strategy.outv);
        TWIN_LINE
    }
}
"""


def _missing(bar):
    return bar[1] > bar[0]


def _value(bar):
    return int(bar[1])  # truncation toward zero, as Pine's int()


def _expected(kind, twin_missing):
    """The expected ``outv`` per prefix: an int, or None for na."""
    out = []
    carried = None
    for index, bar in enumerate(BARS):
        value = _value(bar)
        if kind == "select":
            expected = None if _missing(bar) else value
        elif kind == "narrow_select":
            expected = None if _missing(bar) else index
        elif kind == "tuple_mixed":
            # even bars take the narrow local (na on an up bar, the bar index otherwise),
            # odd bars take int(close)
            expected = (None if _missing(bar) else index) if index % 2 == 0 else value
        elif kind == "nz_seven":
            expected = 7 if _missing(bar) else value
        elif kind == "nz_narrow_first":
            expected = value if _missing(bar) else index
        elif kind == "fixnan":
            expected = carried if _missing(bar) else value
            carried = expected
        elif kind == "doubled":
            expected = None if _missing(bar) else 2 * value
        elif kind == "plus_one":
            expected = value + 1
        elif kind == "plus_one_select":
            expected = None if _missing(bar) else value + 1
        elif kind in ("max_one", "min_hundred", "absolute"):
            apply = {"max_one": lambda v: max(v, 1), "min_hundred": lambda v: min(v, 100),
                     "absolute": abs}[kind]
            expected = None if twin_missing[index] else apply(value)
        else:
            raise AssertionError(kind)
        out.append(expected)
    return out


def _parse(stdout, tag):
    rows = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == tag:
            rows[int(parts[1])] = parts[2]
    return [rows[count] for count in range(1, len(BARS) + 1)]


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("name", sorted(ROWS))
def test_a_32_bit_integer_na_meets_a_64_bit_context_and_stays_na(name, opt):
    from tests._compile import run_emitted_tu

    body, kind = ROWS[name]
    has_twin = "int twin" in body
    driver = DRIVER.replace(
        "OPENS", ", ".join(repr(bar[0]) for bar in BARS)).replace(
        "CLOSES", ", ".join(repr(bar[1]) for bar in BARS)).replace(
        "TWIN_LINE", "show('T', count, strategy.twin);" if has_twin else "")
    stdout = run_emitted_tu(transpile(HEAD + body), driver, opt=opt,
                            label=f"int-na-widths-{name}")
    twin = _parse(stdout, "T") if has_twin else None
    twin_missing = [cell == "na" for cell in twin] if twin else None
    if twin_missing is not None:
        # The comparison is empty if the 32-bit twin never or always reads na.
        assert any(twin_missing) and not all(twin_missing), twin
    expected = [("na" if value is None else str(value))
                for value in _expected(kind, twin_missing)]
    assert _parse(stdout, "O") == expected

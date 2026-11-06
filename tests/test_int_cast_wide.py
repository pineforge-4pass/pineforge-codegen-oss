"""``int(x)`` of a floating value is a 64-bit integer, whatever its magnitude.

TradingView's ``int`` is 64-bit: ``int(1.7e12)`` and ``int(math.floor(1.7e12))`` are
1700000000000 (exported by a probe whose entry and close comments print the
values), and a trade list built on ``int(math.floor(stamp / base)) % 1000`` with
``stamp = 1706655600000.0`` encodes the digit chunks 1 | 706 | 655 | 600 | 0.
The generated C++ used to narrow a floating value outside the 32-bit range to the
integer ``na`` (``-2147483648``), which then flowed into arithmetic as that number.

Each row translates a real Pine source, builds the emitted C++ against the engine and
runs it at ``-O0`` and ``-O2``: the conversion of a double that does not fit an integer
is undefined behaviour in C++ and resolves differently per compiler and architecture
(x86-64 gives the minimum integer, arm64 saturates), so a behavioural check is only
meaningful when it pins more than one level and runs on more than one architecture.

Where each expected number comes from is stated per group:

* MEASURED  - exported by TradingView (the integer witness, the UTC+25 trade list and a
  stored math.floor / ceil / round of +-1005000000000.75).
* ARITHMETIC - what a 64-bit integer gives; no TradingView capture exists yet.
* DEFINED   - this implementation's choice for an input that does not fit 64 bits
  (``na``); not a TradingView measurement.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu

HEAD = '//@version=6\nstrategy("int cast")\n'

# name -> (kind, Pine body, [(output member, expected integer)])
ROWS = {
    # MEASURED: the integer witness, source lines as exported (variables, not just expressions).
    "witness_floor_int": (
        "MEASURED",
        "floor_int = int(math.floor(1.7e12))\n"
        "plus_one = floor_int + 1\n"
        "o0 = floor_int\n"
        "o1 = plus_one\n"
        "o2 = na(floor_int) ? 1 : 0\n",
        [("o0", 1700000000000), ("o1", 1700000000001), ("o2", 0)],
    ),
    "witness_direct_int": (
        "MEASURED",
        "direct_int = int(1.7e12)\n"
        "o0 = direct_int\n",
        [("o0", 1700000000000)],
    ),
    # MEASURED: the UTC+25 trade list. stampMagnitude = 1706655600000.0 split in chunks of 1e12, 1e9, 1e6, 1e3 and 1.
    "utc25_chunks": (
        "MEASURED",
        "stampMagnitude = 1706655600000.0 + 0 * bar_index\n"
        "o0 = int(math.floor(stampMagnitude / 1000000000000.0))\n"
        "o1 = int(math.floor(stampMagnitude / 1000000000.0)) % 1000\n"
        "o2 = int(math.floor(stampMagnitude / 1000000.0)) % 1000\n"
        "o3 = int(math.floor(stampMagnitude / 1000.0)) % 1000\n"
        "o4 = int(math.floor(stampMagnitude / 1.0)) % 1000\n",
        [("o0", 1), ("o1", 706), ("o2", 655), ("o3", 600), ("o4", 0)],
    ),
    # ARITHMETIC: the edges of the 32-bit range and the first values beyond it, series float arguments.
    "edges_as_variables": (
        "ARITHMETIC",
        "z = 0.0 * bar_index\n"
        "e0 = int(2147483647.0 + z)\n"
        "e1 = int(2147483648.0 + z)\n"
        "e2 = int(-2147483648.0 + z)\n"
        "e3 = int(-2147483649.0 + z)\n"
        "e4 = int(4294967296.0 + z)\n"
        "e5 = int(1.0e15 + z)\n"
        "o0 = e0\n"
        "o1 = e1\n"
        "o2 = e2\n"
        "o3 = e3\n"
        "o4 = e4\n"
        "o5 = e5\n",
        [("o0", 2147483647), ("o1", 2147483648), ("o2", -2147483648),
         ("o3", -2147483649), ("o4", 4294967296), ("o5", 1000000000000000)],
    ),
    "edges_in_arithmetic": (
        "ARITHMETIC",
        "z = 0.0 * bar_index\n"
        "o0 = int(2147483647.0 + z) + 1\n"
        "o1 = int(-2147483648.0 + z) - 1\n"
        "o2 = int(3.0e9 + z) * 2\n"
        "o3 = int(-3.0e9 + z)\n"
        "o4 = int(1706655600000.0 + z) % 1000\n",
        [("o0", 2147483648), ("o1", -2147483649), ("o2", 6000000000),
         ("o3", -3000000000), ("o4", 0)],
    ),
    # The receiving slot: a value held by an array, a function result, a parameter and a record field.
    "slot_array_element": (
        "MEASURED",
        "a = array.new<int>()\n"
        "array.push(a, int(1.7e12))\n"
        "o0 = array.get(a, 0)\n",
        [("o0", 1700000000000)],
    ),
    "slot_function_result": (
        "MEASURED",
        "big() => int(1.7e12)\n"
        "o0 = big()\n",
        [("o0", 1700000000000)],
    ),
    "slot_function_parameter": (
        "MEASURED",
        "twice(x) => x * 2\n"
        "o0 = twice(int(1.7e12))\n",
        [("o0", 3400000000000)],
    ),
    "slot_record_field": (
        "MEASURED",
        "type Box\n"
        "    int n\n"
        "b = Box.new(int(1.7e12))\n"
        "o0 = b.n\n",
        [("o0", 1700000000000)],
    ),
    # The argument is a user variable that holds a floating value (close is 100.5 in the driver).
    "user_variable_argument": (
        "ARITHMETIC",
        "scaled = close * 1.0e10\n"
        "n = int(scaled)\n"
        "o0 = n\n"
        "o1 = n % 1000\n"
        "o2 = n + 1\n",
        [("o0", 1005000000000), ("o1", 0), ("o2", 1005000000001)],
    ),
    # The argument is a float input.
    "input_float_argument": (
        "ARITHMETIC",
        "n = int(input.float(3000000000.0, \"n\"))\n"
        "o0 = n\n"
        "o1 = n + 1\n",
        [("o0", 3000000000), ("o1", 3000000001)],
    ),
    # math.floor / ceil / round of one value are ints in Pine (64-bit): the result is held whole when it is stored.
    # MEASURED: TradingView exports floor 1005000000000, ceil 1005000000001, round 1005000000001, floor + 1
    # 1005000000001 and na() false for 1005000000000.75, and -1005000000001, -1005000000000, -1005000000001
    # for -1005000000000.75 (the expressions below use close = 100.5 times 1.0e10, the same two values).
    "stored_math_floor_ceil_round": (
        "MEASURED",
        "big = close * 1.0e10 + 0.75\n"
        "f = math.floor(big)\n"
        "c = math.ceil(big)\n"
        "r = math.round(big)\n"
        "o0 = f\n"
        "o1 = c\n"
        "o2 = r\n"
        "o3 = f + 1\n"
        "o4 = na(f) ? 1 : 0\n",
        [("o0", 1005000000000), ("o1", 1005000000001), ("o2", 1005000000001),
         ("o3", 1005000000001), ("o4", 0)],
    ),
    "stored_math_negative": (
        "MEASURED",
        "big = -close * 1.0e10 - 0.75\n"
        "f = math.floor(big)\n"
        "c = math.ceil(big)\n"
        "r = math.round(big)\n"
        "o0 = f\n"
        "o1 = c\n"
        "o2 = r\n",
        [("o0", -1005000000001), ("o1", -1005000000000), ("o2", -1005000000001)],
    ),
    # Values that fit stay what they were: truncation toward zero, na stays na.
    "small_values_unchanged": (
        "ARITHMETIC",
        "o0 = int(3.7)\n"
        "o1 = int(-3.7)\n"
        "o2 = int(0.9999999)\n"
        "o3 = int(close)\n"
        "o4 = int(close * 10.0)\n",
        [("o0", 3), ("o1", -3), ("o2", 0), ("o3", 100), ("o4", 1005)],
    ),
    "na_stays_na": (
        "MEASURED",
        "z = 0.0 * bar_index\n"
        "o0 = na(int(na)) ? 1 : 0\n"
        "o1 = na(int(0.0 / (0.0 + z))) ? 1 : 0\n"
        "o2 = nz(int(na), 7)\n",
        [("o0", 1), ("o1", 1), ("o2", 7)],
    ),
    # DEFINED: beyond 64 bits the result is na on every compiler and architecture (the cast is range-checked).
    "beyond_64_bits_is_na": (
        "DEFINED",
        "z = 0.0 * bar_index\n"
        "o0 = na(int(1.0e19 + z)) ? 1 : 0\n"
        "o1 = na(int(-1.0e19 + z)) ? 1 : 0\n"
        "o2 = na(int(1.0e300 + z)) ? 1 : 0\n"
        "o3 = na(int(9.0e18 + z)) ? 1 : 0\n"
        "o4 = int(9.0e18 + z) / 1000000000\n",
        [("o0", 1), ("o1", 1), ("o2", 1), ("o3", 0), ("o4", 9000000000)],
    ),
}


def _driver(outputs):
    prints = " << \" \" ".join(f"<< static_cast<double>(strategy.{name})" for name, _ in outputs)
    return rf"""
#include <iomanip>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bar{{100.0, 101.0, 99.0, 100.5, 1.0, 1700000000000LL}};
    strategy.run(&bar, 1);
    std::cout << std::fixed << std::setprecision(0) {prints} << "\n";
}}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("row", sorted(ROWS))
def test_int_of_a_float_is_a_64_bit_integer(row: str, opt: str):
    kind, body, expected = ROWS[row]
    cpp = transpile(HEAD + body)
    out = run_emitted_tu(cpp, _driver(expected), opt=opt, label=f"int cast {row} ({kind})")
    assert out == " ".join(str(value) for _, value in expected) + "\n"


def test_a_quotient_of_two_ints_keeps_the_32_bit_cast():
    # The quotient of two 32-bit ints is no larger than its numerator, so
    # int(n / 2) keeps the 32-bit cast and the 32-bit slot it always had.
    cpp = transpile(HEAD + 'n = input.int(7)\no0 = int(n / 2)\n')
    assert "? (int)_pf_v : na<int>()" in cpp
    assert "int64_t o0" not in cpp


def test_a_variable_halved_through_int_translates_and_stays_32_bit():
    # x feeds itself through int(x / 2): the walk over its writes must end.
    cpp = transpile(HEAD + "var x = 1000\nx := int(x / 2)\no0 = x\n")
    assert "? (int)_pf_v : na<int>()" in cpp
    assert "int64_t x" not in cpp

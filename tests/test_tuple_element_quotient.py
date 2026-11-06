"""A tuple element that is a quotient keeps its fraction in the helper's result and in the global member it lands in.

`pair() => [n / 2, 1]` with `n = input.int(5)` returns 2.5 and 1 in Pine: the
division of two ints is a float. The analyzer's coarse family calls an int-by-int
quotient INT, and the tuple typing let that annotation replace the double the
expression has, so the helper returned `std::tuple<int, int>` and the global
member `a` was an int: `for i = a to a + 2` then summed 2 + 3 + 4 = 9 where
TradingView's 2.5 + 3.5 + 4.5 = 10.5 (the engine row is
tests/test_e2e_for_counter_headers.py::test_fractional_start_carrier_matches_the_measured_loop_shape[tuple_element]).
These rows read the translation, which is where the width is decided: the engine
row reads the value.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("Tuple quotient")\nn = input.int(5, "Numerator")\n'


def test_a_quotient_element_is_a_double_in_the_helper_and_the_member():
    cpp = transpile(HEADER + (
        "pair() => [n / 2, 1]\n"
        "[a, b] = pair()\n"
        "float acc = 0.0\n"
        "for i = a to a + 2\n"
        "    acc += i\n"
        "plot(acc)\n"))
    assert "std::tuple<double, int> pair()" in cpp
    assert re.search(r"^    double a = 0\.0;$", cpp, re.M), "the member a must be a double"
    assert re.search(r"^    int b = 0;$", cpp, re.M), "the integer element stays an int"


@pytest.mark.parametrize("element", ["n / 2", "n / 4 + 1", "(n + 1) / 2"])
def test_every_int_by_int_quotient_element_is_a_double(element):
    cpp = transpile(HEADER + f"pair() => [{element}, 1]\n[a, b] = pair()\nplot(a)\n")
    assert "std::tuple<double, int> pair()" in cpp, element


def test_an_integer_element_stays_an_int():
    cpp = transpile(HEADER + "pair() => [n + 2, n * 3]\n[a, b] = pair()\nplot(a)\n")
    assert "std::tuple<int, int> pair()" in cpp

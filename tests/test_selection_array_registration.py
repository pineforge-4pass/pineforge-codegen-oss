"""A global array variable declared by a selection is an array variable.

``a = switch mode ...`` (or an ``if`` or a ternary) whose every arm is an array
of one type was declared as a ``std::vector`` member but not registered as an
array variable, so ``a.get(0)`` was emitted as a raw member call
(``a.get(0)``, which std::vector has not: a g++ error) instead of the bounds
checked array lowering. The declaration is now registered like the same
declaration with one constructor.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp

HEADER = '//@version=6\nstrategy("selection array", initial_capital = 100000)\n'
ENTRY = 'if v > 0\n    strategy.entry("L", strategy.long)\n'


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(
            "a = switch mode\n"
            "    0 => array.new<float>(size=3, initial_value=1.0)\n"
            "    1 => array.new<float>(size=2, initial_value=2.0)\n"
            "    => array.new<float>(size=1, initial_value=3.0)\n",
            id="switch",
        ),
        pytest.param(
            "a = mode == 0 ? array.new<float>(3, 1.0) : array.new<float>(2, 2.0)\n",
            id="ternary",
        ),
        pytest.param(
            "events = array.new<int>()\n"
            "nextSize() =>\n"
            "    events.push(1)\n"
            "    3\n"
            "nextValue() =>\n"
            "    events.push(2)\n"
            "    1.0\n"
            "a = switch mode\n"
            "    0 => array.new<float>(size=nextSize(), initial_value=nextValue())\n"
            "    1 => array.new<float>(initial_value=nextValue(), size=nextSize())\n"
            "    => array.new<float>(nextSize(), nextValue())\n",
            id="switch-with-user-calls-in-the-arms",
        ),
        pytest.param(
            "a = if mode == 0\n"
            "    array.new<float>(3, 1.0)\n"
            "else\n"
            "    array.new<float>(2, 2.0)\n",
            id="if",
        ),
    ],
)
def test_a_selection_of_arrays_takes_the_array_lowering(declaration):
    cpp = transpile(
        HEADER
        + 'int mode = input.int(1, "Mode", minval = 0, maxval = 3)\n'
        + declaration
        + "float v = a.get(0)\n"
        + ENTRY
    )
    assert ".get(0)" not in cpp, [l for l in cpp.splitlines() if ".get(0)" in l]
    assert "index_out_of_bounds" in cpp
    # The text above can be matched by a lowering that does not build: the
    # generated program must compile too (its value is read in
    # test_e2e_array_element_oracles.py).
    compile_cpp(cpp, label="selection-array")


def test_the_same_array_with_one_constructor_is_unchanged():
    cpp = transpile(
        HEADER
        + "a = array.new<float>(3, 1.0)\n"
        + "float v = a.get(0)\n"
        + ENTRY
    )
    assert ".get(0)" not in cpp
    assert "index_out_of_bounds" in cpp
    compile_cpp(cpp, label="selection-array-control")


def test_a_selection_of_scalars_is_not_registered_as_an_array():
    cpp = transpile(
        HEADER
        + 'int mode = input.int(1, "Mode", minval = 0, maxval = 3)\n'
        + "a = switch mode\n"
        "    0 => 1.0\n"
        "    => 2.0\n"
        + "float v = a\n"
        + ENTRY
    )
    assert "std::vector<double> a" not in cpp

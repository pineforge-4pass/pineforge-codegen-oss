"""array.new: the omitted fill of an int array, and the order of size and fill.

Pine's omitted initial_value is na: for array<int> as for array<float> (C1
emitted 0 for the int array). Pine evaluates a call's arguments as they are
written; C++ leaves the order of a call's arguments unspecified and g++ runs the
right one first, so an array.new whose size or fill calls a user function binds
the argument written first (the size, unless the fill is written before it)
before the other is evaluated. Every other array.new keeps its emitted text.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("array.new defaults", initial_capital = 100000)\n'


def _emitted_line(cpp: str, needle: str) -> str:
    lines = [line for line in cpp.splitlines() if needle in line]
    assert len(lines) == 1, (needle, lines)
    return lines[0]


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("c = array.new<int>(size=2)", id="generic-int"),
        pytest.param("c = array.new_int(size=2)", id="new-int"),
        pytest.param("c = array.new<int>(2)", id="generic-int-positional"),
    ],
)
def test_omitted_int_fill_is_na(statement):
    cpp = transpile(HEADER + statement + "\nplot(c.size())\n")
    line = _emitted_line(cpp, "c = [&](auto _pf_array_new_size")
    assert line.endswith("}((2), (na<int>()));")


def test_omitted_float_fill_stays_na():
    cpp = transpile(HEADER + "a = array.new<float>(size=2)\nplot(a.size())\n")
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_size")
    assert line.endswith("}((2), (na<double>()));")


@pytest.mark.parametrize(
    "statement, name",
    [
        pytest.param("c = array.new_int(2, 5)", "c", id="explicit-int-fill"),
        pytest.param("b = array.new_bool(2)", "b", id="omitted-bool-fill"),
        pytest.param("s = array.new_string(2)", "s", id="omitted-string-fill"),
    ],
)
def test_other_fills_keep_their_spelling(statement, name):
    cpp = transpile(HEADER + statement + f"\nplot({name}.size())\n")
    line = _emitted_line(cpp, f"{name} = [&](auto _pf_array_new_size")
    assert "na<int>()" not in line


ORDER_SOURCE = (
    HEADER
    + "events = array.new<int>()\n"
    "nextSize() =>\n"
    "    events.push(1)\n"
    "    3\n"
    "nextValue() =>\n"
    "    events.push(2)\n"
    "    1.0\n"
)


@pytest.mark.parametrize(
    "statement, size, fill",
    [
        pytest.param(
            "a = array.new<float>(nextSize(), initial_value=nextValue())",
            "nextSize()", "nextValue()", id="size-and-fill-call",
        ),
        pytest.param(
            "a = array.new<float>(nextSize(), 1.0)",
            "nextSize()", "1.0", id="size-call",
        ),
        pytest.param(
            "a = array.new<float>(3, nextValue())",
            "3", "nextValue()", id="fill-call",
        ),
    ],
)
def test_a_user_call_in_size_or_fill_binds_the_size_first(statement, size, fill):
    cpp = transpile(ORDER_SOURCE + statement + "\nplot(a.size())\n")
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_size")
    assert line.startswith(
        "        a = [&](auto _pf_array_new_size){ "
        "return [&](auto _pf_array_new_value){ ")
    assert line.endswith(f"}}(({fill})); }}(({size}));")


def test_a_fill_written_before_the_size_is_bound_first():
    cpp = transpile(
        ORDER_SOURCE
        + "a = array.new<float>(initial_value=nextValue(), size=nextSize())\n"
        "plot(a.size())\n"
    )
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_value")
    assert line.startswith(
        "        a = [&](auto _pf_array_new_value){ "
        "return [&](auto _pf_array_new_size){ ")
    assert line.endswith("}((nextSize())); }((nextValue()));")


def test_array_new_without_a_user_call_keeps_its_two_argument_call():
    cpp = transpile(
        ORDER_SOURCE + "a = array.new<float>(3, 1.0)\nplot(a.size())\n")
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_size")
    assert line.startswith(
        "        a = [&](auto _pf_array_new_size, auto _pf_array_new_value){ ")
    assert line.endswith("}((3), (1.0));")

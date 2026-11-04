"""array.new: the omitted fill of a color array, and a missing size.

A color array's omitted initial_value is na, like a float or int array's:
TradingView's tape of ``array.new<color>(size=2)`` reads na from its first
element, where C1 filled 0 (a valid packed color). A missing size
(``int n = na`` passed as ``size``) builds an empty array and the script goes
on: TradingView's tape of that probe is an empty array on every bar, where the
generated code stopped the run with the coded size_invalid error. A size that is
present but invalid (not finite, negative, beyond size_t, beyond max_size) keeps
its coded stop; a present fill of any family keeps its emitted text.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("array.new color and size", initial_capital = 100000)\n'


def _emitted_line(cpp: str, needle: str) -> str:
    lines = [line for line in cpp.splitlines() if needle in line]
    assert len(lines) == 1, (needle, lines)
    return lines[0]


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("c = array.new<color>(size=2)", id="generic-named"),
        pytest.param("c = array.new<color>(2)", id="generic-positional"),
    ],
)
def test_the_omitted_fill_of_a_color_array_is_na(statement):
    cpp = transpile(HEADER + statement + "\nplot(c.size())\n")
    line = _emitted_line(cpp, "c = [&](auto _pf_array_new_size")
    assert line.endswith("}((2), (na<int64_t>()));")


@pytest.mark.parametrize(
    "statement, name",
    [
        pytest.param("c = array.new<color>(2, color.red)", "c", id="explicit-color-fill"),
        pytest.param("b = array.new_bool(2)", "b", id="omitted-bool-fill"),
        pytest.param("s = array.new_string(2)", "s", id="omitted-string-fill"),
        pytest.param("f = array.new_float(2)", "f", id="omitted-float-fill"),
    ],
)
def test_other_fills_keep_their_spelling(statement, name):
    cpp = transpile(HEADER + statement + f"\nplot({name}.size())\n")
    line = _emitted_line(cpp, f"{name} = [&](auto _pf_array_new_size")
    if name == "f":
        assert line.endswith("}((2), (na<double>()));")
    else:
        assert "na<int64_t>()" not in line


def test_a_missing_size_builds_an_empty_array():
    cpp = transpile(
        HEADER
        + "int n = na\n"
        "a = array.new<float>(size=n, initial_value=7.0)\n"
        "plot(a.size())\n"
    )
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_size")
    assert "if(is_na(_pf_array_new_size)) return std::vector<double>(); " in line
    # The na test comes before the finiteness test, so a missing size never
    # reaches the stop.
    assert line.index("if(is_na(_pf_array_new_size))") < line.index("std::isfinite")


def test_an_invalid_size_keeps_its_coded_stop():
    cpp = transpile(
        HEADER + "a = array.new<float>(size=3, initial_value=1.0)\nplot(a.size())\n")
    line = _emitted_line(cpp, "a = [&](auto _pf_array_new_size")
    # Two stops remain: a size that is not finite, negative or beyond size_t,
    # and a std::length_error from the allocation itself.
    assert line.count('_PF_ARRAY_STOP("size_invalid"') == 2, line
    assert "_pf_array_new_size<0" in line
    assert "std::numeric_limits<size_t>::max()" in line


def test_a_missing_size_with_a_color_fill_is_empty_and_typed():
    cpp = transpile(
        HEADER
        + "int n = na\n"
        "c = array.new<color>(size=n)\n"
        "plot(c.size())\n"
    )
    line = _emitted_line(cpp, "c = [&](auto _pf_array_new_size")
    assert "if(is_na(_pf_array_new_size)) return std::vector<int64_t>(); " in line
    assert line.endswith("}((n), (na<int64_t>()));")

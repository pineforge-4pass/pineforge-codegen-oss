"""A function-local string whose history is read is a series of strings.

``string tag = na`` inside a user function, written in a branch and read back
as ``tag[1]``, was emitted as a member ``Series<double> tag`` and every push of
the string failed g++ ("cannot convert 'std::string' to 'double'"); a global
string with history already declared ``Series<std::string>``. The member now
takes the string type from the function-local declaration when no global of
the name decides it. A float local with history keeps its series of doubles.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("callable string series", initial_capital = 100000)\n'
ENTRY = 'if v > 0\n    strategy.entry("L", strategy.long)\n'


def test_a_string_local_with_history_is_a_series_of_strings():
    cpp = transpile(
        HEADER
        + "check() =>\n"
        "    string tag = na\n"
        "    if bar_index % 2 == 0\n"
        '        tag := "x"\n'
        "    (na(tag) ? 1.0 : 0.0) + (na(tag[1]) ? 2.0 : 0.0)\n"
        "float v = check()\n"
        + ENTRY
    )
    members = [line for line in cpp.splitlines() if line.strip().endswith(" tag;")]
    assert members == ["    Series<std::string> tag;"], members


def test_a_string_local_with_history_pushes_a_string_na():
    cpp = transpile(
        HEADER
        + "check() =>\n"
        "    string tag = na\n"
        "    if bar_index % 2 == 0\n"
        '        tag := "x"\n'
        "    na(tag[1]) ? 1.0 : 0.0\n"
        "float v = check()\n"
        + ENTRY
    )
    assert "tag.push(na<std::string>())" in cpp or "tag.update(na<std::string>())" in cpp
    assert "Series<double> tag" not in cpp


@pytest.mark.parametrize(
    "declaration, member",
    [
        pytest.param("float tag = na\n", "    Series<double> tag;", id="float"),
        pytest.param("float tag = 0.0\n", "    Series<double> tag;", id="float-literal"),
    ],
)
def test_a_float_local_with_history_keeps_its_series_of_doubles(declaration, member):
    cpp = transpile(
        HEADER
        + "check() =>\n"
        f"    {declaration}"
        "    if bar_index % 2 == 0\n"
        "        tag := close\n"
        "    na(tag[1]) ? 1.0 : 0.0\n"
        "float v = check()\n"
        + ENTRY
    )
    members = [line for line in cpp.splitlines() if line.strip().endswith(" tag;")]
    assert members == [member], members

"""A bare na written to a UDT string field is the typed string na.

``Cell.new(na)``, ``Cell.new(tag=na)`` and a field default ``string tag = na``
were emitted as the numeric ``na<double>()`` in the record's designated
initializer (a g++ error), and ``cell.tag := na`` assigned that double to the
``std::string`` (a char conversion, leaving a one-character string that is not
missing). Each is now ``na<std::string>()``.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("udt string na", initial_capital = 100000)\n'
READ = "float observed = na(cell.tag) ? 1.0 : 0.0\nplot(observed)\n"


def _emitted_line(cpp: str, needle: str) -> str:
    lines = [line for line in cpp.splitlines() if needle in line]
    assert len(lines) == 1, (needle, lines)
    return lines[0]


@pytest.mark.parametrize(
    "type_decl, construct",
    [
        pytest.param("type Cell\n    string tag\n", "Cell.new(na)", id="positional"),
        pytest.param("type Cell\n    string tag\n", "Cell.new(tag=na)", id="named"),
        pytest.param("type Cell\n    string tag = na\n", "Cell.new()", id="default"),
    ],
)
def test_bare_na_constructor_argument_is_the_string_na(type_decl, construct):
    cpp = transpile(HEADER + type_decl + f"Cell cell = {construct}\n" + READ)
    line = _emitted_line(cpp, "cell = _pf_udt_Cell.create(")
    assert ".tag = na<std::string>()" in line
    assert "na<double>()" not in line


def test_bare_na_assigned_to_a_string_field_is_the_string_na():
    cpp = transpile(
        HEADER
        + 'type Cell\n    string tag\nCell cell = Cell.new("x")\ncell.tag := na\n'
        + READ
    )
    line = _emitted_line(cpp, ".tag = na")
    assert line.strip() == "_pf_udt_Cell.get(cell).tag = na<std::string>();"


def test_string_values_keep_their_spelling():
    cpp = transpile(
        HEADER
        + 'type Cell\n    string tag\nCell cell = Cell.new("x")\ncell.tag := "y"\n'
        + READ
    )
    assert '.tag = std::string("x")' in _emitted_line(cpp, "cell = _pf_udt_Cell.create(")
    assert _emitted_line(cpp, 'tag = std::string("y")').strip() == (
        '_pf_udt_Cell.get(cell).tag = std::string("y");'
    )

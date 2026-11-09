"""A declared enum written as a collection's generic argument is that enum.

``array.new<Choice>(...)`` typed its element as a udt named ``Choice`` while
``Choice.second`` is the enum named ``Choice``; the operator check joins the two
sides of ``==`` only when their kinds agree, so a script TradingView runs was
refused with ``Operator '==' cannot combine Choice (c.get(0)) and Choice.``.
The element is now the enum, in the generic argument of ``array.new``,
``matrix.new`` and a declared ``array<Choice>`` / ``map<string, Choice>`` hint.
An enum still compares only with itself, and a user type keeps its udt identity.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp

HEADER = (
    '//@version=6\nstrategy("enum generic", initial_capital = 100000)\n'
    "enum Choice\n    first\n    second\n"
    "enum Other\n    one\n    two\n"
)
ENTRY = 'if ok\n    strategy.entry("L", strategy.long)\n'


@pytest.mark.parametrize(
    "declaration, member",
    [
        pytest.param(
            "c = array.new<Choice>(size=2, initial_value=Choice.second)\n"
            "ok = c.get(0) == Choice.second\n",
            "Choice_second", id="array-new-second",
        ),
        pytest.param(
            "c = array.new<Choice>(size=2, initial_value=Choice.first)\n"
            "ok = c.get(0) == Choice.first\n",
            "Choice_first", id="array-new-first",
        ),
        pytest.param(
            "array<Choice> c = array.new<Choice>(2, Choice.second)\n"
            "ok = c.get(0) != Choice.first\n",
            "Choice_first", id="array-hint-not-equal",
        ),
    ],
)
def test_an_enum_array_element_compares_with_a_member(declaration, member):
    cpp = transpile(HEADER + declaration + ENTRY)
    # An enum is stored as its ordinal, as an enum variable is.
    assert "std::vector<double> c;" in cpp
    assert member in cpp


def test_the_omitted_fill_of_an_enum_array_stays_na():
    cpp = transpile(
        HEADER + "b = array.new<Choice>(size=2)\nok = na(b.get(0))\n" + ENTRY)
    lines = [line for line in cpp.splitlines() if "b = [&](auto _pf_array_new_size" in line]
    assert len(lines) == 1, lines
    assert lines[0].endswith("}((2), (na<double>()));")


def test_an_enum_still_compares_only_with_itself():
    with pytest.raises(CompileError) as raised:
        transpile(
            HEADER
            + "c = array.new<Choice>(size=2, initial_value=Choice.second)\n"
            "ok = c.get(0) == Other.one\n" + ENTRY
        )
    assert "cannot combine" in raised.value.diagnostics[0].message


def test_a_user_type_array_keeps_its_udt_identity():
    cpp = transpile(
        '//@version=6\nstrategy("udt array", initial_capital = 100000)\n'
        "type Cell\n    float value = 0.0\n"
        "cells = array.new<Cell>(size=1)\n"
        "cell = cells.get(0)\n"
        "ok = cell.value > 0\n" + ENTRY
    )
    assert "cells" in cpp
    # The field read `cell.value` compiles only when the elements are the user
    # type's objects; an array typed as scalars would not build. The values are
    # read natively in test_e2e_array_element_oracles.py.
    compile_cpp(cpp, label="udt-array-identity")

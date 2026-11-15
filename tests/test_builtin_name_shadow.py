"""A user variable named like a builtin variable is read as the user variable.

``float second = na`` declares a script variable; its reads were emitted as the
builtin ``pine_second(...)`` because the builtin lookup came before the user's
declaration binding. A read bound to a user declaration is the user's; a read
of the builtin itself keeps its emitted text.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.tables import BAR_BUILTINS, BAR_FIELDS

HEADER = '//@version=6\nstrategy("builtin name shadow", initial_capital = 100000)\n'


def test_user_variable_named_like_a_builtin_is_read_as_the_user_variable():
    cpp = transpile(
        HEADER
        + "float second = na\n"
        "float minute = na\n"
        "for i = 1 to 2\n"
        "    float observed = close + i\n"
        "    if i == 1\n"
        "        second := observed\n"
        "    else\n"
        "        minute := observed\n"
        "float code = math.abs(second - minute) < 0.5 ? 1.0 : 2.0\n"
        "plot(code)\n"
    )
    assert "pine_second(" not in cpp
    assert "pine_minute(" not in cpp
    assignments = [line for line in cpp.splitlines() if line.startswith("        code = ")]
    assert len(assignments) == 1
    assert "std::abs((second - minute))" in assignments[0]


def test_read_of_the_builtin_variable_keeps_its_emitted_text():
    cpp = transpile(HEADER + "float s = second + minute\nplot(s)\n")
    assert "pine_second(current_bar_.timestamp, syminfo_.timezone)" in cpp
    assert "pine_minute(current_bar_.timestamp, syminfo_.timezone)" in cpp


@pytest.mark.parametrize("name", sorted(BAR_FIELDS.keys() | BAR_BUILTINS.keys()))
def test_string_binding_types_repeated_constructor_and_scalar_operands(name):
    cpp = transpile(
        HEADER
        + 'parts = str.split("first|second", "|")\n'
        + f"{name} = array.get(parts, bar_index % 2)\n"
        + f"values = array.from({name}, {name})\n"
        + f'joined = {name} + ":"\n'
        + "plot(1)\n"
    )
    # Both elements must read the inferred string declaration, including
    # builtin names whose C++ spelling needs escaping.
    initializer = re.search(
        r"values = std::vector<std::string>\{([A-Za-z_]\w*), \1\};",
        cpp,
    )
    assert initializer is not None, cpp
    binding = initializer.group(1)
    assert f"std::string {binding}" in cpp
    assignments = [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("joined = ")
    ]
    assert len(assignments) == 1
    assert binding in assignments[0]
    # Correct spelling with a numeric inferred type would still emit an
    # invalid std::to_string call around the string operand.
    assert "std::to_string(" not in assignments[0]


@pytest.mark.parametrize("name, builtin", sorted(BAR_FIELDS.items()))
def test_bar_read_before_global_declaration_keeps_its_source_binding(name, builtin):
    cpp = transpile(
        HEADER
        + f"before = {name}\n"
        + f"{name} = str.tostring(bar_index)\n"
        + f'after = {name} + ":"\n'
        + "plot(before)\n"
    )
    assert f"before = {builtin};" in cpp
    assignments = [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("after = ")
    ]
    assert len(assignments) == 1
    assert builtin not in assignments[0]
    assert "std::to_string(" not in assignments[0]


@pytest.mark.parametrize("name, builtin", sorted(BAR_FIELDS.items()))
@pytest.mark.parametrize("block", ["if", "while", "switch"])
def test_bar_builtin_returns_after_a_string_block_binding_expires(name, builtin, block):
    body = (
        f"    {name} = str.tostring(bar_index)\n"
        f'    seen := {name} + ":"\n'
    )
    if block == "switch":
        branch = "switch\n    bar_index >= 0 =>\n" + "".join(
            "    " + line for line in body.splitlines(keepends=True)
        )
    elif block == "while":
        branch = "while bar_index >= 0\n" + body + "    break\n"
    else:
        branch = "if bar_index >= 0\n" + body
    cpp = transpile(
        HEADER
        + f"before = {name}\n"
        + 'seen = ""\n'
        + branch
        + f"after = {name}\n"
        + "plot(before + after)\n"
    )
    assert "double before" in cpp
    assert "double after" in cpp
    assert f"before = {builtin};" in cpp
    assert f"after = {builtin};" in cpp
    assignments = [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("seen = ") and 'std::string("")' not in line
    ]
    assert len(assignments) == 1
    assert builtin not in assignments[0]
    assert "std::to_string(" not in assignments[0]


def test_qualified_member_and_other_bar_builtin_keep_their_bindings():
    cpp = transpile(
        HEADER
        + "type Holder\n    string high\n"
        + 'holder = Holder.new("member")\n'
        + 'parts = str.split("first|second", "|")\n'
        + "high = array.get(parts, bar_index % 2)\n"
        + "seen = holder.high + high\n"
        + "builtin = low\nplot(builtin)\n"
    )
    assignments = [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("seen = ")
    ]
    assert len(assignments) == 1
    assert ".high" in assignments[0]
    assert "current_bar_." not in assignments[0]
    assert "std::to_string(" not in assignments[0]
    assert "builtin = current_bar_.low;" in cpp

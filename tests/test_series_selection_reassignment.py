"""Statement-valued reassignment writes the current slot of a Series."""

import re

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("name", ["held", "threshold"])
@pytest.mark.parametrize("selection", [
    "if rising\n    close\nelse\n    {name}[1]",
    "switch\n    rising => close\n    => {name}[1]",
])
def test_series_selection_reassignment_matches_branch_assignments(name, selection):
    prefix = (
        '//@version=6\nstrategy("Series selections")\n'
        'rising = close > open\n'
        f'float {name} = na\n'
    )
    cpp = transpile(prefix + f"{name} := " + selection.format(name=name)
                    + f"\nplot({name})\n")
    control = transpile(
        prefix + f"if rising\n    {name} := close\nelse\n    {name} := {name}[1]\n"
        + f"plot({name})\n")
    writes = lambda text: [
        line.strip() for line in text.splitlines()
        if line.strip().startswith(name + ".update(")
    ]
    assert writes(cpp) == writes(control)
    assert f"{name}.update(current_bar_.close);" in cpp
    assert f"{name}.update({name}[1]);" in cpp
    assert not re.search(rf"^\s*{name} = ", cpp, re.MULTILINE)

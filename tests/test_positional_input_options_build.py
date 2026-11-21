"""Compile the numeric options overload against the checked settings header."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


@pytest.mark.parametrize("body", [
    'choice = input.float(1.5, "Choice", [1, 1.5, 2])',
    'choice = input.int(5, "Choice", [5])',
    '''enabled = input.bool(true, "Enabled")
choice = input.int(5, "Choice", [10, 5], "Help", "r", "G", true, display.none, enabled)''',
], ids=["mixed-float", "singleton-int", "active-dropdown"])
def test_positional_numeric_options_build(body):
    pine = f'//@version=6\nstrategy("Dropdown")\n{body}\nplot(choice)\n'
    compile_cpp(transpile(pine), label="positional numeric options")

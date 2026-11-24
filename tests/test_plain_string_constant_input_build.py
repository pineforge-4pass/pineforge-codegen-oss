"""Compile bare input() with a string read as its positional or named defval."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


@pytest.mark.parametrize("declaration,call", [
    ('const string seed = "Alpha"', 'input(seed, title="Mode")'),
    ('const string seed = "Alpha"', 'input(defval=seed, title="Mode")'),
    ('seed = "Alpha"', 'input(seed, title="Mode")'),
], ids=["positional", "named", "inferred-const"])
def test_bare_string_constant_input_builds(declaration, call):
    pine = (f'//@version=6\nstrategy("String default")\n{declaration}\n'
            f'mode = {call}\nplot(str.length(mode))\n')
    compile_cpp(transpile(pine), label="bare input with a constant string default")

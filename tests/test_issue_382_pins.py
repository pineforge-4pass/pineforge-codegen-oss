"""Issue #382 item 7: pin the string ordering operators beyond the existing < pin."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase


@pytest.mark.parametrize("operator", ["<=", ">", ">="])
def test_other_string_ordering_operators_are_refused(operator):
    source = (
        '//@version=6\nstrategy("issue 382 string ordering")\n'
        'string a = "a"\n'
        'string b = "b"\n'
        f'value = a {operator} b\n'
        'plot(close)\n'
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == "PF-E2052"
    assert diagnostic.message == f"Cannot order string values with {operator}."

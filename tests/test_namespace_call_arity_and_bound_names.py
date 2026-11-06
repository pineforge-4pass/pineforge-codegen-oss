"""A namespace function is checked as the namespace's function only when no
binding of the script shares the namespace's name.

``math.abs()`` with no argument is refused: ``math.abs(number)`` takes one.
When the script binds a value named ``math`` (a variable, a function
parameter or a local) the same spelling can be a method call on that value, as
in ``test_method_call_receiver.py``, and is not checked as the namespace's
function.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

HEADER = '//@version=6\nstrategy("namespace arity")\n'
ENTRY = 'if observed > 0\n    strategy.entry("L", strategy.long)\n'
METHOD = "method abs(float this) => this * 100.0\n"


@pytest.mark.parametrize("call", ["math.abs()", "math.sign()", "math.floor()"])
def test_a_namespace_call_without_its_argument_is_still_refused(call):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + f"float observed = {call}\n" + ENTRY)
    messages = [diagnostic.message for diagnostic in caught.value.diagnostics]
    assert any(
        message.startswith(f"{call[:-2]} is missing its argument 'number'")
        for message in messages
    ), messages


def test_a_namespace_call_with_its_argument_is_unchanged():
    cpp = transpile(HEADER + "float observed = math.abs(close - open)\n" + ENTRY)
    assert "std::abs" in cpp


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            METHOD + "float math = open - close\nfloat observed = math.abs()\n",
            id="global-variable",
        ),
        pytest.param(
            METHOD + "shadow(float math) => math.abs()\nfloat observed = shadow(close)\n",
            id="function-parameter",
        ),
        pytest.param(
            METHOD + "shadow(float value) =>\n    float math = value\n    math.abs()\n"
            "float observed = shadow(close)\n",
            id="function-local",
        ),
    ],
)
def test_a_bound_name_spelled_like_the_namespace_is_not_checked_as_the_namespace(body):
    # The call is a method call on the bound value: no "missing its argument".
    transpile(HEADER + body + ENTRY)

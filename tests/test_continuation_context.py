"""Continuation restrictions distinguish script statements from callable bodies."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests._compile import run_emitted_tu


_CASES = [
    pytest.param(
        "evaluate() =>\n    1.0 +\n    2.0\nactual = evaluate()\n",
        "3",
        # The aligned operand belongs to 1 + 2, so the function returns 3.
        id="aligned_function_operand",
    ),
    pytest.param(
        "evaluate() =>\n    1.0 +\n        2.0\nactual = evaluate()\n",
        "3",
        # The deeper operand still belongs to 1 + 2, giving 3.
        id="deeper_function_operand",
    ),
    pytest.param(
        "evaluate(float price) =>\n"
        "    if price > 0\n"
        "        price +\n"
        "            3.0\n"
        "    else\n"
        "        0.0\n"
        "actual = evaluate(4.0)\n",
        "7",
        # The true branch returns 4 + 3, which is 7.
        id="nested_block_operand",
    ),
    pytest.param(
        "evaluate(bool enabled) =>\n"
        "    enabled ? 7.0 :\n"
        "        2.0\n"
        "actual = evaluate(false)\n",
        "2",
        # The false ternary arm is the continued literal 2.
        id="block_ternary",
    ),
    pytest.param(
        "evaluate(float price) =>\n"
        "    ready = price > 0 and\n"
        "        price < 10\n"
        "    ready ? 9.0 : 0.0\n"
        "actual = evaluate(4.0)\n",
        "9",
        # Both comparisons are true for 4, selecting 9.
        id="block_logical_chain",
    ),
    pytest.param(
        "evaluate(int value) => value + 1, value * 2,\n"
        "    value * 3\n"
        "actual = evaluate(4)\n",
        "12",
        # The comma body returns its last statement, 4 * 3 = 12.
        id="inline_comma_body",
    ),
    pytest.param(
        "evaluate(float price) => price +\n"
        "    3.0\n"
        "actual = evaluate(4.0)\n",
        "7",
        # The arrow opens a callable body whose expression is 4 + 3 = 7.
        id="inline_arithmetic_body",
    ),
    pytest.param(
        "evaluate(float price) =>\n"
        "    math.max(price,\n"
        "        1.0) +\n"
        "        3.0\n"
        "actual = evaluate(4.0)\n",
        "7",
        # The closed call leaves a pending plus in the block: max(4, 1) + 3 = 7.
        id="closed_call_in_block",
    ),
]


_DRIVER = r"""
#include <iostream>

int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << strategy.actual << '\n';
}
"""


@pytest.mark.parametrize("body, expected", _CASES)
def test_callable_wraps_keep_their_hand_derived_value(body: str, expected: str) -> None:
    source = '//@version=6\nstrategy("Callable continuation")\n' + body
    output = run_emitted_tu(
        transpile(source), _DRIVER, opt="-O0", label="callable_continuation",
    )
    assert output.strip() == expected


@pytest.mark.parametrize(
    "operand",
    [
        pytest.param("2.0", id="column_zero"),  # Column zero cannot continue the top-level plus.
        pytest.param("    2.0", id="four_spaces"),  # Four spaces cannot continue the top-level plus.
    ],
)
def test_callable_wrap_does_not_exempt_the_next_script_statement(operand: str) -> None:
    source = (
        '//@version=6\nstrategy("Continuation boundary")\n'
        "evaluate() =>\n    1.0 +\n        2.0\n"
        "q = 1.0 +\n" + operand + "\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.code == "PF-E0038"
    # The callable wrap is accepted; only the script's plus ends at 6:10.
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 10)

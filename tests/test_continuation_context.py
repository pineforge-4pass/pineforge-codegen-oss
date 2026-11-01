"""Operand indentation has the same meaning in script and callable bodies."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests._compile import run_emitted_tu


_REFUSED_CASES = [
    pytest.param(
        "evaluate() =>\n    1.0 +\n    2.0\nactual = evaluate()\n",
        (4, 10),
        # fn_body_4/error.json: four spaces refuse after the plus at 4:10.
        id="aligned_function_operand",
    ),
    pytest.param(
        "evaluate() =>\n    1.0 +\n        2.0\nactual = evaluate()\n",
        (4, 10),
        # fn_body_8/error.json: eight spaces refuse after the plus at 4:10.
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
        (5, 16),
        # Hand derivation: nested width 12 refuses; eight spaces + "price +" end at 5:16.
        id="nested_block_operand",
    ),
    pytest.param(
        "evaluate(bool enabled) =>\n"
        "    enabled ? 7.0 :\n"
        "        2.0\n"
        "actual = evaluate(false)\n",
        (4, 20),
        # fn_ternary_8/error.json: width 8 refuses; this longer colon line ends at 4:20.
        id="block_ternary",
    ),
    pytest.param(
        "evaluate(float price) =>\n"
        "    ready = price > 0 and\n"
        "        price < 10\n"
        "    ready ? 9.0 : 0.0\n"
        "actual = evaluate(4.0)\n",
        (4, 26),
        # Hand derivation: width 8 refuses; four spaces + "ready = price > 0 and" end at 4:26.
        id="block_logical_chain",
    ),
    pytest.param(
        "evaluate(float price) => price +\n"
        "    3.0\n"
        "actual = evaluate(4.0)\n",
        (3, 33),
        # fn_arrow_4/error.json: width 4 refuses; this longer inline body ends at 3:33.
        id="inline_arithmetic_body",
    ),
    pytest.param(
        "evaluate(float price) =>\n"
        "    math.max(price,\n"
        "        1.0) +\n"
        "        3.0\n"
        "actual = evaluate(4.0)\n",
        (5, 15),
        # Hand derivation: after ")" closes the call, width 8 refuses at the plus end, 5:15.
        id="closed_call_in_block",
    ),
    pytest.param(
        "method doubled(float self) => self *\n"
        "        2.0\n"
        "actual = close.doubled()\n",
        (3, 37),
        # Hand derivation: inline method width 8 refuses after its trailing star at 3:37.
        id="inline_method_operand",
    ),
]

# Unchanged acceptance pins for nonmultiple operand widths and statement commas.
_CASES = [
    pytest.param(
        "evaluate() =>\n    1.0 +\n     2.0\nactual = evaluate()\n",
        "3",
        # Five spaces continue 1 + 2 in the function's block, giving 3.
        id="five_space_function_operand",
    ),
    pytest.param(
        "evaluate(float price) => price +\n"
        "      3.0\n"
        "actual = evaluate(4.0)\n",
        "7",
        # Six spaces continue the inline body's 4 + 3, giving 7.
        id="six_space_inline_operand",
    ),
    pytest.param(
        "evaluate(int value) => value + 1, value * 2,\n"
        "    value * 3\n"
        "actual = evaluate(4)\n",
        "12",
        # Unchanged pin: the comma body returns its last statement, 4 * 3 = 12.
        id="inline_comma_body",
    ),
]


@pytest.mark.parametrize("body, position", _REFUSED_CASES)
def test_callable_operand_requires_nonmultiple_indentation(
    body: str, position: tuple[int, int],
) -> None:
    source = '//@version=6\nstrategy("Callable continuation")\n' + body
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.code == "PF-E0038"
    assert (diagnostic.location.line, diagnostic.location.col) == position


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
        "evaluate() =>\n    1.0 +\n      2.0\n"
        "q = 1.0 +\n" + operand + "\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.code == "PF-E0038"
    # Hand derivation from cont_top_4/error.json: the next script plus ends at 6:10.
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 10)

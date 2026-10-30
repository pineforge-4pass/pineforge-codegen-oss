"""Function body indentation and the independent block indentation rule."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import FuncDef, MethodDef
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


_ACCEPTED_SCRIPTS = [
    pytest.param(
        """//@version=6
strategy("Tab blocks", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

referenceUnits = input.int(4, "Reference quantity")
unitsFor(int units) =>
\tunits

units = unitsFor(referenceUnits)
if bar_index % 20 == 5
\tstrategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
\tstrategy.close("L")
""",
        (1,),  # Accepted tab body contains the single parameter expression.
        id="tab_blocks",
    ),
    pytest.param(
        """//@version=6
strategy("Short function body", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
  5

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (1,),  # Accepted two-space body contains only the returned literal.
        id="two_space_function",
    ),
    pytest.param(
        """//@version=6
strategy("Independent function widths", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

westUnits() =>
  2
eastUnits() =>
    4

units = westUnits() + eastUnits()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (1, 1),  # Accepted independent bodies each contain one literal.
        id="independent_function_widths",
    ),
    pytest.param(
        """//@version=6
strategy("Equivalent tab and spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
\tint units = 1
    units += 39
    units

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (3,),  # Accepted tab/four-space body declares, increments and returns.
        id="tab_and_four_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Plain function")
quantity() =>
  5
strategy.entry("L", strategy.long, qty = quantity())
""",
        (1,),  # The same accepted no-parameter form returns one literal.
        id="plain_function",
    ),
    pytest.param(
        """//@version=6
strategy("Function parameters")
quantity(int value) =>
  value
strategy.entry("L", strategy.long, qty = quantity(5))
""",
        (1,),  # Parameters do not change the accepted one-statement body.
        id="function_with_parameters",
    ),
    pytest.param(
        """//@version=6
strategy("Method body")
type Position
    int units
method quantity(Position self) =>
  self.units
position = Position.new(5)
strategy.entry("L", strategy.long, qty = position.quantity())
""",
        (1,),  # A method shares the function body rule and returns one field.
        id="method",
    ),
    pytest.param(
        """//@version=6
strategy("Function after type")
type Position
    int units
quantity(Position position) =>
  position.units
position = Position.new(5)
strategy.entry("L", strategy.long, qty = quantity(position))
""",
        (1,),  # A preceding type block leaves one expression in the function.
        id="function_after_type",
    ),
    pytest.param(
        """//@version=6
strategy("Function after conditional")
int units = 0
if true
    units := 1
quantity() =>
  5
units := quantity()
strategy.entry("L", strategy.long, qty = units)
""",
        (1,),  # A preceding conditional leaves the same one-literal body.
        id="function_after_conditional",
    ),
    pytest.param(
        """//@version=6
strategy("Function dedent")
quantity() =>
  5
units = quantity()
extra = units + 1
strategy.entry("L", strategy.long, qty = extra)
""",
        (1,),  # Column zero ends the body before either global declaration.
        id="dedent_to_global",
    ),
    pytest.param(
        """//@version=6
strategy("Several function statements")
quantity() =>
  int units = 1
  units += 4
  units
strategy.entry("L", strategy.long, qty = quantity())
""",
        (3,),  # Equal two-space lines declare, increment and return in order.
        id="several_statements",
    ),
]


def _transpile_checked(source: str, body_lengths: tuple[int, ...]) -> str:
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    functions = [
        node
        for node in program.body
        if isinstance(node, (FuncDef, MethodDef))
    ]
    assert tuple(len(node.body) for node in functions) == body_lengths
    assert all(
        node.is_single_expr is False for node in functions
    )  # Every accepted body is written below its arrow and uses block semantics.
    return transpile(source)


@pytest.mark.parametrize("source, body_lengths", _ACCEPTED_SCRIPTS)
def test_function_bodies_transpile(source: str, body_lengths: tuple[int, ...]) -> None:
    assert _transpile_checked(source, body_lengths)


@pytest.mark.parametrize("source, body_lengths", _ACCEPTED_SCRIPTS)
def test_function_bodies_compile(source: str, body_lengths: tuple[int, ...]) -> None:
    compile_cpp(_transpile_checked(source, body_lengths), label="function_body_indent")


_REFUSED_SCRIPTS = [
    pytest.param(
        """//@version=6
strategy("Two space conditional", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

referenceUnits = input.int(2, "Reference quantity")
unitsFor(int units) =>
  units

units = unitsFor(referenceUnits)
if bar_index % 20 == 5
  strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
  strategy.close("L")
""",
        (6, 10),  # Refused at the old function boundary or the two-space if body.
        id="two_space_conditional",
    ),
    pytest.param(
        """//@version=6
strategy("Three space conditional", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

referenceUnits = input.int(3, "Reference quantity")
unitsFor(int units) =>
   units

units = unitsFor(referenceUnits)
if bar_index % 20 == 5
   strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
   strategy.close("L")
""",
        (6, 10),  # Refused at the old function boundary or the three-space if body.
        id="three_space_conditional",
    ),
    pytest.param(
        """//@version=6
strategy("Nested conditional width", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor(bool selected) =>
    int units = 1
    if selected
      units := 2
      if bar_index % 20 < 10
          units := 7
    units

units = unitsFor(bar_index % 20 < 10)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (7,),  # Refused: line 7 is only two spaces deeper than its if.
        id="nested_conditional_width",
    ),
    pytest.param(
        """//@version=6
strategy("Conditional branch widths", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

units = 1
if bar_index % 20 < 10
  units := 8
else
    units := 2

if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (6,),  # Refused: the two-space if body fails before the else body.
        id="conditional_branch_widths",
    ),
    pytest.param(
        """//@version=6
strategy("Nested loop width", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    int units = 0
    for step = 1 to 3
      units += 3
    units

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (7,),  # Refused: line 7 is only two spaces deeper than its for.
        id="nested_loop_width",
    ),
    pytest.param(
        """//@version=6
strategy("Two space loop")
for step = 1 to 3
  strategy.entry("L", strategy.long)
""",
        (4,),  # Refused: the two-space for body has no block indent.
        id="two_space_for",
    ),
    pytest.param(
        """//@version=6
strategy("Three space loop")
for step = 1 to 3
   strategy.entry("L", strategy.long)
""",
        (4,),  # Refused: the three-space for body has no block indent.
        id="three_space_for",
    ),
    pytest.param(
        """//@version=6
strategy("Two space while")
while false
  strategy.entry("L", strategy.long)
""",
        (4,),  # Refused: the two-space while body has no block indent.
        id="two_space_while",
    ),
    pytest.param(
        """//@version=6
strategy("Three space while")
while false
   strategy.entry("L", strategy.long)
""",
        (4,),  # Refused: the three-space while body has no block indent.
        id="three_space_while",
    ),
    pytest.param(
        """//@version=6
strategy("Two space switch")
switch
  => 1
""",
        (4,),  # Refused: the two-space switch arm has no block indent.
        id="two_space_switch",
    ),
    pytest.param(
        """//@version=6
strategy("Three space switch")
switch
   => 1
""",
        (4,),  # Refused: the three-space switch arm has no block indent.
        id="three_space_switch",
    ),
]


@pytest.mark.parametrize("source, error_lines", _REFUSED_SCRIPTS)
def test_short_control_blocks_remain_syntax_errors(
    source: str, error_lines: tuple[int, ...]
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level.value == "error"  # Invalid block syntax is an error.
    assert diagnostic.phase.value == "PARSER"  # Block boundaries fail during parsing.
    assert diagnostic.location.line in error_lines
    assert diagnostic.message.startswith("Expected INDENT")  # A block indent is missing.

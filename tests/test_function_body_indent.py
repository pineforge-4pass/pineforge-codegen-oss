"""Short indentation continues a logical line without creating a block."""

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import BinOp, FuncDef, MethodDef, NumberLiteral, VarDecl
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer, TokenType
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests._e2e import (
    build_strategy_library,
    chart_feed_head,
    closed_trades,
    skip_unless_e2e_env,
)


_TWO_SPACE_FUNCTION = """//@version=6
strategy("Short function expression", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
  5

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
"""

_MIXED_FUNCTION_BODIES = """//@version=6
strategy("Function expression and block", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

westUnits() =>
  2
eastUnits() =>
    4

units = westUnits() + eastUnits()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
"""


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
        ((False, 1),),  # A tab opens a block containing one parameter expression.
        id="tab_blocks",
    ),
    pytest.param(
        _TWO_SPACE_FUNCTION,
        ((True, 1),),  # Joining the two-space line gives the inline expression 5.
        id="two_space_function",
    ),
    pytest.param(
        _MIXED_FUNCTION_BODIES,
        ((True, 1), (False, 1)),  # Two spaces join 2; four spaces open the block returning 4.
        id="continued_and_block_functions",
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
        ((False, 3),),  # Tab and four spaces share a block with three statements.
        id="tab_and_four_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Plain function")
quantity() =>
  5
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((True, 1),),  # Two spaces join the single literal to the function arrow.
        id="plain_function",
    ),
    pytest.param(
        """//@version=6
strategy("Function parameters")
quantity(int value) =>
  value
strategy.entry("L", strategy.long, qty = quantity(5))
""",
        ((True, 1),),  # The parameter expression joins its header as one inline body.
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
        ((True, 1),),  # The method's field expression joins its arrow as one inline body.
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
        ((True, 1),),  # Dedenting the type block leaves one inline field expression.
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
        ((True, 1),),  # Dedenting the conditional leaves one inline literal.
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
        ((True, 1),),  # Column zero ends the joined expression before both declarations.
        id="dedent_to_global",
    ),
    pytest.param(
        """//@version=6
strategy("One space continuation")
quantity() =>
 7
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((True, 1),),  # One space joins the literal 7 to its arrow.
        id="one_space_function",
    ),
    pytest.param(
        """//@version=6
strategy("Three space continuation")
quantity() =>
   7
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((True, 1),),  # Three spaces join the literal 7 to its arrow.
        id="three_space_function",
    ),
    pytest.param(
        """//@version=6
strategy("Consecutive continued functions")
westQuantity() =>
  2
eastQuantity() =>
  4
strategy.entry("L", strategy.long, qty = westQuantity() + eastQuantity())
""",
        ((True, 1), (True, 1)),  # Each column-zero header starts its own inline expression.
        id="consecutive_continued_functions",
    ),
    pytest.param(
        """//@version=6
strategy("Commented function header")
quantity() => // The following line supplies the expression.
  5
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((True, 1),),  # A trailing comment leaves the arrow and literal in one logical line.
        id="commented_header",
    ),
    pytest.param(
        """//@version=6
strategy("Continued ternary")
quantity(bool selected) =>
  selected ? 5
  : 7
strategy.entry("L", strategy.long, qty = quantity(true))
""",
        ((True, 1),),  # Both two-space lines join into the single expression selected ? 5 : 7.
        id="two_line_ternary",
    ),
    pytest.param(
        """//@version=6
strategy("Leading operator continuation")
quantity() =>
  +5
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((True, 1),),  # A leading plus already joins the arrow and unary expression.
        id="leading_operator_control",
    ),
    pytest.param(
        """//@version=6
strategy("Trailing operator continuation")
quantity() =>
    1 +
      2
strategy.entry("L", strategy.long, qty = quantity())
""",
        ((False, 1),),  # The trailing plus keeps 1 + 2 in the existing four-space block.
        id="trailing_operator_control",
    ),
    pytest.param(
        """//@version=6
strategy("Continued field assignment")
type Position
    int units
setQuantity(Position position) =>
  position.units := 5
position = Position.new(1)
setQuantity(position)
strategy.entry("L", strategy.long, qty = position.units)
""",
        ((False, 1),),  # The joined field assignment is one statement using inline assignment semantics.
        id="continued_field_assignment",
    ),
    pytest.param(
        """//@version=6
strategy("Continued switch arms")
quantity(bool selected) =>
    switch selected
        true =>
          5
        =>
          7
strategy.entry("L", strategy.long, qty = quantity(true))
""",
        ((False, 1),),  # One block statement selects the joined arm 5 for true, otherwise 7.
        id="continued_switch_arms",
    ),
]


def _transpile_checked(
    source: str, function_shapes: tuple[tuple[bool, int], ...]
) -> str:
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    functions = [
        node
        for node in program.body
        if isinstance(node, (FuncDef, MethodDef))
    ]
    assert tuple(
        (node.is_single_expr, len(node.body)) for node in functions
    ) == function_shapes
    return transpile(source)


@pytest.mark.parametrize("source, function_shapes", _ACCEPTED_SCRIPTS)
def test_function_bodies_transpile(
    source: str, function_shapes: tuple[tuple[bool, int], ...]
) -> None:
    assert _transpile_checked(source, function_shapes)


@pytest.mark.parametrize("source, function_shapes", _ACCEPTED_SCRIPTS)
def test_function_bodies_compile(
    source: str, function_shapes: tuple[tuple[bool, int], ...]
) -> None:
    compile_cpp(_transpile_checked(source, function_shapes), label="function_body_indent")


@pytest.mark.parametrize(
    "source, expected_quantity",
    [
        pytest.param(
            _TWO_SPACE_FUNCTION,
            5,  # The joined function returns 5, passed directly as the entry quantity.
            id="literal_entry_quantity",
        ),
        pytest.param(
            _MIXED_FUNCTION_BODIES,
            6,  # The joined function returns 2 and the block returns 4: the entry uses 6.
            id="summed_entry_quantity",
        ),
    ],
)
def test_continued_function_entry_quantity(
    source: str, expected_quantity: int, tmp_path: Path
) -> None:
    cpp = transpile(source)
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, bars=20)
    build_strategy_library(cpp, tmp_path)
    trades = closed_trades(engine, tmp_path, feed)
    assert len(trades) == 1  # Bars 0 through 19 contain one entry at 5 and one close at 15.
    assert trades[0]["qty"] == expected_quantity


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
        # Joining the two-space entry to the if condition cannot form a block.
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
        # Joining the three-space entry to the if condition cannot form a block.
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
        # Six spaces join the assignment to the four-space if header.
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
        # The two-space assignment joins the if header before the valid else block.
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
        # Six spaces join the increment to the four-space loop header.
        id="nested_loop_width",
    ),
    pytest.param(
        """//@version=6
strategy("Several continued statements", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
  int units = 1
  units += 49
  units

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # All three lines join the arrow; the declaration is no inline expression.
        id="several_continued_statements",
    ),
    pytest.param(
        """//@version=6
strategy("Two space loop")
for step = 1 to 3
  strategy.entry("L", strategy.long)
""",
        # The two-space entry joins the loop header instead of opening its block.
        id="two_space_for",
    ),
    pytest.param(
        """//@version=6
strategy("Three space loop")
for step = 1 to 3
   strategy.entry("L", strategy.long)
""",
        # The three-space entry joins the loop header instead of opening its block.
        id="three_space_for",
    ),
    pytest.param(
        """//@version=6
strategy("Two space while")
while false
  strategy.entry("L", strategy.long)
""",
        # The two-space entry joins the while condition instead of opening its block.
        id="two_space_while",
    ),
    pytest.param(
        """//@version=6
strategy("Three space while")
while false
   strategy.entry("L", strategy.long)
""",
        # The three-space entry joins the while condition instead of opening its block.
        id="three_space_while",
    ),
    pytest.param(
        """//@version=6
strategy("Two space switch")
switch
  => 1
""",
        # The two-space arm joins switch, which still needs an indented block.
        id="two_space_switch",
    ),
    pytest.param(
        """//@version=6
strategy("Three space switch")
switch
   => 1
""",
        # The three-space arm joins switch, which still needs an indented block.
        id="three_space_switch",
    ),
    pytest.param(
        """//@version=6
strategy("Short else continuation")
int units = 1
if true
    units := 2
else
  units := 3
strategy.entry("L", strategy.long, qty = units)
""",
        # The assignment joins else, which requires a newline and a block.
        id="two_space_else",
    ),
    pytest.param(
        """//@version=6
strategy("Short type field continuation")
type Position
  int units
position = Position.new(5)
strategy.entry("L", strategy.long, qty = position.units)
""",
        # The field joins type Position; adjacent header words are invalid syntax.
        id="two_space_type_field",
    ),
    pytest.param(
        """//@version=6
strategy("Adjacent function expressions")
quantity() =>
  5
  6
strategy.entry("L", strategy.long, qty = quantity())
""",
        # Joining both expressions gives 5 6, which cannot be one inline body.
        id="adjacent_function_expressions",
    ),
    pytest.param(
        """//@version=6
strategy("Adjacent method expressions")
type Position
    int units
method quantity(Position self) =>
  self.units
  self.units + 1
position = Position.new(5)
strategy.entry("L", strategy.long, qty = position.quantity())
""",
        # Joining both field expressions supplies no operator between the reads.
        id="adjacent_method_expressions",
    ),
    pytest.param(
        """//@version=6
strategy("Adjacent field assignments")
type Position
    int units
setQuantity(Position position) =>
  position.units := 5
  position.units := 6
position = Position.new(1)
setQuantity(position)
strategy.entry("L", strategy.long, qty = position.units)
""",
        # The two field assignments have no comma or logical line boundary.
        id="adjacent_field_assignments",
    ),
    pytest.param(
        """//@version=6
strategy("Adjacent continued switch arms")
quantity() =>
    switch
        =>
          5
          => 6
strategy.entry("L", strategy.long, qty = quantity())
""",
        # The second arrow joins the first arm's value instead of starting an arm.
        id="adjacent_continued_switch_arms",
    ),
]


@pytest.mark.parametrize("source", _REFUSED_SCRIPTS)
def test_short_control_blocks_remain_syntax_errors(source: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert any(
        diagnostic.level == Level.ERROR
        and diagnostic.phase in (Phase.PARSER, Phase.LEXER)
        for diagnostic in caught.value.diagnostics
    )  # An invalid logical line must fail as syntax, before semantic analysis.


@pytest.mark.parametrize(
    "source, expected_structure",
    [
        pytest.param(
            "quantity() =>\n  5\n",
            (TokenType.NEWLINE,),  # The arrow and expression have one shared line ending.
            id="function_header",
        ),
        pytest.param(
            "method quantity(int value) =>\n  value\n",
            (TokenType.NEWLINE,),  # A method header joins its expression without a block.
            id="method_header",
        ),
        pytest.param(
            "if true\n  units = 1\n",
            (TokenType.NEWLINE,),  # The if header joins the assignment without a block.
            id="conditional_header",
        ),
        pytest.param(
            "else\n  units = 1\n",
            (TokenType.NEWLINE,),  # The else keyword joins the assignment without a block.
            id="else_header",
        ),
        pytest.param(
            "for index = 1 to 3\n  units = 1\n",
            (TokenType.NEWLINE,),  # The counted-loop header joins the assignment.
            id="counted_loop_header",
        ),
        pytest.param(
            "for value in values\n  units = value\n",
            (TokenType.NEWLINE,),  # The collection-loop header joins the assignment.
            id="collection_loop_header",
        ),
        pytest.param(
            "while true\n  units = 1\n",
            (TokenType.NEWLINE,),  # The while header joins the assignment without a block.
            id="while_header",
        ),
        pytest.param(
            "switch\n  => 1\n",
            (TokenType.NEWLINE,),  # The switch header joins the arm without a block.
            id="switch_header",
        ),
        pytest.param(
            "type Position\n  int units\n",
            (TokenType.NEWLINE,),  # The type header joins the field without a block.
            id="type_header",
        ),
        pytest.param(
            "units = 5\n  -2\n",
            (TokenType.NEWLINE,),  # The complete statement gains the subtraction tail.
            id="complete_statement",
        ),
        pytest.param(
            "quantity() =>\n  int units = 1\n  units += 49\n  units\n",
            (TokenType.NEWLINE,),  # All three wrapped lines share the header's block level.
            id="successive_continuation_lines",
        ),
        pytest.param(
            "quantity() =>\n    if true\n      units = 1\n",
            (TokenType.NEWLINE, TokenType.INDENT, TokenType.NEWLINE, TokenType.DEDENT),
            # Four spaces open one block; six spaces join the nested header.
            id="nested_header",
        ),
    ],
)
def test_short_indentation_joins_any_previous_line(
    source: str, expected_structure: tuple[TokenType, ...]
) -> None:
    tokens = Lexer(source).tokenize()
    assert tuple(
        token.type
        for token in tokens
        if token.type in (TokenType.NEWLINE, TokenType.INDENT, TokenType.DEDENT)
    ) == expected_structure


def test_short_indentation_extends_a_complete_expression() -> None:
    source = """//@version=6
strategy("Continued subtraction")
units = 5
  -2
strategy.entry("L", strategy.long, qty = units)
"""
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    declaration = program.body[1]
    assert isinstance(declaration, VarDecl)
    assert isinstance(declaration.value, BinOp)
    assert declaration.value.op == "-"  # The joined tail subtracts instead of starting a statement.
    assert isinstance(declaration.value.left, NumberLiteral)
    assert declaration.value.left.value == 5  # The left operand is the written literal 5.
    assert isinstance(declaration.value.right, NumberLiteral)
    assert declaration.value.right.value == 2  # The right operand is 2, so the entry quantity is 3.
    assert transpile(source)


def test_indented_declarations_keep_existing_statement_boundaries() -> None:
    source = """//@version=6
strategy("Statement boundary")
value = 1
  other = 2
strategy.entry("L", strategy.long, qty = value + other)
"""
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    declarations = [node for node in program.body if isinstance(node, VarDecl)]
    assert tuple(node.name for node in declarations) == (
        "value", "other",
    )  # The existing physical-line fallback keeps these as two declarations.
    assert tuple(node.value.value for node in declarations) == (
        1, 2,
    )  # Their written literals remain 1 and 2, so the entry uses 3.
    assert transpile(source)

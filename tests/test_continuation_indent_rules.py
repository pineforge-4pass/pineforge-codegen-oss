"""Logical line wrapping and block indentation share one lexical rule."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


_ACCEPTED = [
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
        # One tab opens each block; the function returns the input 4.
        id="tab_blocks",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped literal", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
  5

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Two spaces join the single expression 5 to the arrow.
        id="two_space_expression",
    ),
    pytest.param(
        """//@version=6
strategy("Different function layouts", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        # The wrapped expression returns 2 and the block returns 4.
        id="expression_and_block",
    ),
    pytest.param(
        """//@version=6
strategy("Equivalent block widths", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        # A tab and four spaces share one block, so the return value is 40.
        id="tab_then_four_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at five spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
     10)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 10).
        id="call_argument_five_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at six spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
      11)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 11).
        id="call_argument_six_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at seven spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
       12)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 12).
        id="call_argument_seven_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at nine spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
         13)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 13).
        id="call_argument_nine_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at ten spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
          14)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 14).
        id="call_argument_ten_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at eleven spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
           15)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the second argument in max(1, 15).
        id="call_argument_eleven_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at twelve spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
            16)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Even twelve spaces are cosmetic while the call remains open.
        id="call_argument_twelve_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped ternary", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    bar_index % 20 < 10 ? 17 :
      2

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Six spaces continue the ternary's false operand 2.
        id="trailing_ternary",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped logical expression", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    bool windowOpen = bar_index % 20 == 5 and
      bar_index % 40 == 5 or
      bar_index % 40 == 25
    windowOpen ? 18 : 2

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Both six-space operands join the bool declaration before its tail.
        id="trailing_logical_operators",
    ),
    pytest.param(
        """//@version=6
strategy("Leading addition", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    4
      + 15

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # The leading plus joins 4 + 15, giving 19.
        id="leading_plus",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped collection arguments", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    array<int> pieces = array.from(7,
      13)
    array.sum(pieces)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # The call builds [7, 13], whose sum is 20.
        id="collection_arguments",
    ),
    pytest.param(
        """//@version=6
strategy("Shallower call argument", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
  21)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Parentheses keep the shallower argument in max(1, 21).
        id="call_argument_two_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Call argument at eight spaces", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

unitsFor() =>
    math.max(1,
        37)

units = unitsFor()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Eight spaces remain cosmetic inside max(1, 37).
        id="call_argument_eight_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Top level wrapped call", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

units = math.max(1,
    38)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Four spaces are allowed because max(1, 38) is still open.
        id="top_level_call_four_spaces",
    ),
    pytest.param(
        """//@version=6
strategy("Blank wrapped line", overlay=true, initial_capital=1000000)
q = 1.0 +

  3.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # The blank line contributes no tokens; two spaces continue 1 + 3.
        id="blank_continuation_line",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped reassignment", overlay=true, initial_capital=1000000)
float q = 1.0
q :=
  2.0 + bar_index % 3
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Two spaces supply the reassignment's entire right operand.
        id="trailing_reassignment",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped method access", overlay=true, initial_capital=1000000)
method scaled(float self) => self * 2.0
f(float x) =>
    x
      .scaled()
q = f(1.0 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Six spaces join the member access to x inside f's block.
        id="leading_member_dot",
    ),
    pytest.param(
        """//@version=6
strategy("Wrapped subtraction", overlay=true, initial_capital=1000000)
f() =>
    20
      - 5
q = math.abs(f())
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Six spaces join subtraction, so f returns 20 - 5 = 15.
        id="leading_minus",
    ),
    pytest.param(
        """//@version=6
strategy("Five space expression", overlay=true, initial_capital=1000000)
f(x) =>
     x + 1
q = f(1 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        # Five spaces join one expression to the arrow rather than opening a block.
        id="five_space_expression",
    ),
    pytest.param(
        '//@version=6\nstrategy("Open parentheses")\nq = (1.0 +\n2.0)\nplot(q)\n',
        # Column zero is cosmetic while the parenthesized addition is open.
        id="parentheses_column_zero",
    ),
    pytest.param(
        '//@version=6\nstrategy("Open parentheses")\nq = (1.0 +\n    2.0)\nplot(q)\n',
        # Four spaces are cosmetic while the parenthesized addition is open.
        id="parentheses_four_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped parameter")\nf(int x) =>\n   x + 1\nplot(f(2))\n',
        # Three spaces join one parameter expression to its arrow.
        id="parameter_three_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped parameter")\nf(int x) =>\n     x + 1\nplot(f(2))\n',
        # Five spaces join one parameter expression to its arrow.
        id="parameter_five_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped method")\nmethod plusOne(int self) =>\n  self + 1\nint x = 2\nplot(x.plusOne())\n',
        # Two spaces join the method's only expression to its arrow.
        id="method_two_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped method")\nmethod plusOne(int self) =>\n   self + 1\nint x = 2\nplot(x.plusOne())\n',
        # Three spaces join the method's only expression to its arrow.
        id="method_three_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped method")\nmethod plusOne(int self) =>\n     self + 1\nint x = 2\nplot(x.plusOne())\n',
        # Five spaces join the method's only expression to its arrow.
        id="method_five_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Shallower operand")\nf() =>\n    1 +\n   2\nplot(f())\n',
        # Three spaces continue the addition even below the four-space block.
        id="trailing_operand_three_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Wrapped operand")\nf() =>\n    1 +\n     2\nplot(f())\n',
        # Five spaces continue the addition without opening another block.
        id="trailing_operand_five_spaces",
    ),
    pytest.param(
        '//@version=6\nstrategy("Commented continuation")\nq = 1 + // Operand follows.\n// A whole comment line.\n   2\nplot(q)\n',
        # Comments contribute no tokens; three spaces continue 1 + 2.
        id="comment_continuation",
    ),
    pytest.param(
        '//@version=6\nstrategy("Bracket continuation")\nf() =>\n    [1,\n2]\n[a, b] = f()\nplot(a + b)\n',
        # Column zero stays inside the open tuple bracket, yielding [1, 2].
        id="brackets_column_zero",
    ),
    pytest.param(
        '//@version=6\nstrategy("Aligned branches")\nq = 1\nif close > open\n    q := 2\nelse if close < open\n    q := 3\nelse\n    q := 4\nplot(q)\n',
        # Aligned else clauses end the preceding four-space block.
        id="aligned_else_if",
    ),
]


_REFUSED = [
    pytest.param(
        """//@version=6
strategy("Two space control body", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

referenceUnits = input.int(2, "Reference quantity")
unitsFor(int units) =>
  units

units = unitsFor(referenceUnits)
if bar_index % 20 == 5
  strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
  strategy.close("L")
""",
        (10, 3),  # The entry token at column 3 joins the if header instead of opening a block.
        id="two_space_control_body",
    ),
    pytest.param(
        """//@version=6
strategy("Three space control body", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

referenceUnits = input.int(3, "Reference quantity")
unitsFor(int units) =>
   units

units = unitsFor(referenceUnits)
if bar_index % 20 == 5
   strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
   strategy.close("L")
""",
        (10, 4),  # The entry token at column 4 joins the if header instead of opening a block.
        id="three_space_control_body",
    ),
    pytest.param(
        """//@version=6
strategy("Nested conditional", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        (7, 7),  # The six-space assignment joins the four-space if header.
        id="nested_short_conditional",
    ),
    pytest.param(
        """//@version=6
strategy("Short first branch", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        (6, 3),  # The two-space assignment joins the if header before else is reached.
        id="short_branch_before_else",
    ),
    pytest.param(
        """//@version=6
strategy("Nested loop", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        (7, 7),  # The six-space increment joins the for header at four spaces.
        id="nested_short_loop",
    ),
    pytest.param(
        """//@version=6
strategy("Several wrapped statements", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

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
        (6, 3),  # The second statement at line 6 has no logical boundary after the declaration.
        id="several_wrapped_statements",
    ),
    pytest.param(
        """//@version=6
strategy("Column zero operand", overlay=true, initial_capital=1000000)
q = 1.0 +
2.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (3, 10),  # Column zero cannot continue the plus, which ends at column 10.
        id="column_zero_operand",
    ),
    pytest.param(
        """//@version=6
strategy("Four space operand", overlay=true, initial_capital=1000000)
q = 1.0 +
    2.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (3, 10),  # Four spaces cannot continue the plus, which ends at column 10.
        id="four_space_operand",
    ),
    pytest.param(
        """//@version=6
strategy("Shallower else", overlay=true, initial_capital=1000000)
q = 1.0
if bar_index % 40 < 20
    q := 2.0
  else
    q := 3.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (6, 3),  # The joined else retains its authored physical position at line 6, column 3.
        id="two_space_else",
    ),
    pytest.param(
        """//@version=6
strategy("Six space control body", overlay=true, initial_capital=1000000)
q = 1.0
if bar_index % 40 < 20
      q := 2.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (5, 7),  # The joined assignment retains its physical position at line 5, column 7.
        id="six_space_control_body",
    ),
    pytest.param(
        """//@version=6
strategy("Two tab body", overlay=true, initial_capital=1000000)
f(x) =>
\t\tx + 1
q = f(1 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (4, 3),  # Two tabs skip a block level; x starts after two tab characters at column 3.
        id="two_tab_body",
    ),
    pytest.param(
        """//@version=6
strategy("Skipped space level", overlay=true, initial_capital=1000000)
f(x) =>
        y = x + 1
    y * 2
q = f(1 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (4, 9),  # Eight spaces skip a level before y at column 9.
        id="eight_then_four",
    ),
    pytest.param(
        """//@version=6
strategy("Mixed block widths", overlay=true, initial_capital=1000000)
f(x) =>
\t    y = x + 1
        y * 2
q = f(1 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (5, 1),  # The existing tab-count rule makes line 5 an unexpected nested INDENT.
        id="tab_spaces_then_eight",
    ),
    pytest.param(
        """//@version=6
strategy("Adjacent wrapped statements", overlay=true, initial_capital=1000000)
f(x) =>
  y = x + 1
  y * 2
q = f(1 + bar_index % 3)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (5, 3),  # The second y starts at column 3 without a logical statement boundary.
        id="two_wrapped_statements",
    ),
    pytest.param(
        """//@version=6
strategy("Short type field", overlay=true, initial_capital=1000000)
type Cell
  float value = 2.0
cell = Cell.new()
q = cell.value
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        (3, 6),  # Joining the field prevents a type block; Cell is the adjacent name at column 6.
        id="two_space_type_field",
    ),
    pytest.param(
        '//@version=6\nstrategy("Shallower else if")\nq = 1\nif close > open\n    q := 2\n  else if close < open\n    q := 3\nplot(q)\n',
        (6, 3),  # The two-space else if joins the assignment rather than ending its block.
        id="two_space_else_if",
    ),
    pytest.param(
        '//@version=6\nstrategy("Shallower else")\nq = 1\nif close > open\n    q := 2\n   else\n    q := 3\nplot(q)\n',
        (6, 4),  # Three spaces join else to the previous statement at its physical column 4.
        id="three_space_else",
    ),
    pytest.param(
        '//@version=6\nstrategy("Deeper else")\nq = 1\nif close > open\n    q := 2\n     else\n        q := 3\nplot(q)\n',
        (6, 6),  # Five spaces also join else; its physical column is 6.
        id="five_space_else",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space control")\nq = 1\nif close > open\n     q := 2\nplot(q)\n',
        (5, 6),  # Five spaces join the assignment to the header instead of opening a block.
        id="five_space_control_body",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space type field")\ntype Cell\n   int value\n',
        (3, 6),  # Three spaces join the field, leaving Cell as the adjacent header name.
        id="three_space_type_field",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space type field")\ntype Cell\n     int value\n',
        (3, 6),  # Five spaces join the field, leaving Cell as the adjacent header name.
        id="five_space_type_field",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space loop")\nfor i = 0 to 1\n   strategy.entry("L", strategy.long)\n',
        (4, 4),  # Three spaces join the entry to the counted-loop header at column 4.
        id="three_space_counted_loop",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space loop")\nfor i = 0 to 1\n     strategy.entry("L", strategy.long)\n',
        (4, 6),  # Five spaces also join the entry to the counted-loop header.
        id="five_space_counted_loop",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space collection loop")\nvalues = array.from(1, 2)\nfor value in values\n   plot(value)\n',
        (5, 4),  # Three spaces join the call to the for-in header at column 4.
        id="three_space_collection_loop",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space collection loop")\nvalues = array.from(1, 2)\nfor value in values\n     plot(value)\n',
        (5, 6),  # Five spaces join the call to the for-in header at column 6.
        id="five_space_collection_loop",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space while body")\nwhile false\n   break\n',
        (4, 4),  # Three spaces join break to the while header at column 4.
        id="three_space_while_body",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space while body")\nwhile false\n     break\n',
        (4, 6),  # Five spaces join break to the while header at column 6.
        id="five_space_while_body",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space switch body")\nswitch\n   => 1\n',
        (4, 4),  # Three spaces join the arrow to switch at column 4.
        id="three_space_switch_body",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space switch body")\nswitch\n     => 1\n',
        (4, 6),  # Five spaces join the arrow to switch at column 6.
        id="five_space_switch_body",
    ),
    pytest.param(
        '//@version=6\nstrategy("Three space enum member")\nenum Size\n   small\n',
        (3, 6),  # Joining the member leaves Size as the adjacent header name at column 6.
        id="three_space_enum_member",
    ),
    pytest.param(
        '//@version=6\nstrategy("Five space enum member")\nenum Size\n     small\n',
        (3, 6),  # Five spaces also join the member and prevent an enum block.
        id="five_space_enum_member",
    ),
    pytest.param(
        '//@version=6\nstrategy("Skipped method level")\nmethod plusOne(int self) =>\n\t\tself + 1\nint x = 2\nplot(x.plusOne())\n',
        (4, 3),  # Two tabs skip a block level before the method's first expression.
        id="method_two_tabs",
    ),
    pytest.param(
        '//@version=6\nstrategy("Aligned function operand")\nf() =>\n    1.0 +\n    2.0\nplot(f())\n',
        (4, 10),  # The plus ends at column 10 and its next line has structural indentation.
        id="function_four_space_operand",
    ),
    pytest.param(
        '//@version=6\nstrategy("Deeper function operand")\nf() =>\n    1.0 +\n        2.0\nplot(f())\n',
        (4, 10),  # Eight spaces are structural rather than a continuation after the plus.
        id="function_eight_space_operand",
    ),
]


@pytest.mark.parametrize("source", _ACCEPTED)
def test_wrapped_sources_translate(source: str) -> None:
    assert transpile(source)


@pytest.mark.parametrize("source", _ACCEPTED)
def test_wrapped_sources_compile(source: str) -> None:
    compile_cpp(transpile(source), label="continuation_indent")


@pytest.mark.parametrize("source, position", _REFUSED)
def test_invalid_logical_lines_are_located(
    source: str, position: tuple[int, int],
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase in (Phase.LEXER, Phase.PARSER)
    assert (diagnostic.location.line, diagnostic.location.col) == position


@pytest.mark.parametrize(
    "source, code",
    [
        pytest.param(
            '//@version=6\nstrategy("Missing wrap indentation")\nx = 1 +\n2\n',
            "PF-E0038",  # A column-zero operand violates continuation indentation.
            id="continuation_code",
        ),
        pytest.param(
            '//@version=6\nstrategy("Skipped block level")\nf(int x) =>\n\t\tx\nplot(f(1))\n',
            "PF-E0039",  # Two tabs skip the first block level.
            id="block_code",
        ),
    ],
)
def test_indentation_errors_have_catalog_codes(source: str, code: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert caught.value.diagnostics[0].code == code

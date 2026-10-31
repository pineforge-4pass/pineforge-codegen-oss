"""Closed-trade fold plans and the shapes that must keep ordinary loops."""

from dataclasses import FrozenInstanceError, fields
from textwrap import indent

import pytest

from pineforge_codegen.ast_nodes import ArgOrder, ForStmt, IfStmt
from pineforge_codegen.codegen.closed_trade_fold import (
    FoldPlan, plan_closed_trade_folds,
)
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


HEAD = '//@version=6\nstrategy("fold")\n'
BODY = "p = strategy.closedtrades.profit(i)\ntotal += p\n"


def _parse(source):
    return Parser(Lexer(source).tokenize(), source=source).parse()


def _script(
    declarations="float total = 0.0\n", body=BODY, *,
    prefix="", between="", tail="plot(total)\n",
    guard="strategy.closedtrades > 0",
    loop="for i = 0 to strategy.closedtrades - 1",
    outer_else="",
):
    repeated = loop + "\n" + indent(body, "    ")
    guarded = repeated if guard is None else (
        "if " + guard + "\n" + indent(repeated, "    ")
    )
    if outer_else:
        guarded += "else\n" + indent(outer_else, "    ")
    return HEAD + prefix + declarations + between + guarded + tail


def _assert_plan(program, expected, safe_name=str, *, guard_position=2):
    plans = plan_closed_trade_folds(program, safe_name)
    # Every accepted fixture has exactly one direct guard and one selected loop.
    assert len(plans) == 1
    plan = plans[0]
    assert isinstance(plan, FoldPlan)
    assert (plan.index, plan.accumulators, plan.locals, plan.stem) == expected
    # Runtime identity is checked at the independently specified source position.
    loop = program.body[guard_position].body[0]
    assert isinstance(loop, ForStmt)
    assert plan.for_node_id == id(loop)
    assert plan._for_node is loop
    return plan


@pytest.mark.parametrize("declaration", [
    # An inferred integer seed is reset by the declaration at body index 1.
    "total = 0\n",
    # An explicit integer seed has the same declaration and guard positions.
    "int total = 3\n",
    # An explicit floating seed remains a literal at the same position.
    "float total = 0.5\n",
    # A unary minus over an integer literal is an accepted reset.
    "total = -2\n",
    # A typed negative integer is also a unary-minus literal reset.
    "int total = -3\n",
    # A typed negative float preserves the permitted unary-minus shape.
    "float total = -0.25\n",
])
def test_literal_seeds_and_constant_updates(declaration):
    program = _parse(_script(declarations=declaration, body="total += 1\n"))
    # The sole fold is numbered zero; strategy() and one declaration put G at 2.
    _assert_plan(program, (0, (("total", "total"),), (), "_pf_ctf0_"))


@pytest.mark.parametrize("body, expected_locals", [
    # An inferred profit local binds one double before the numeric update.
    (BODY, (("p", "profit", "double"),)),
    # An explicit float profit local retains the same double accessor type.
    ("float p = strategy.closedtrades.profit(i)\ntotal += p\n",
     (("p", "profit", "double"),)),
    # An inferred entry identifier binds a string before its guarded update.
    ('tag = strategy.closedtrades.entry_id(i)\n'
     'if tag == "L"\n    total += 1\n',
     (("tag", "entry_id", "std::string"),)),
    # An explicit string local retains the entry identifier's accessor type.
    ('string tag = strategy.closedtrades.entry_id(i)\n'
     'if tag == "L"\n    total += 1\n',
     (("tag", "entry_id", "std::string"),)),
])
def test_local_declaration_types(body, expected_locals):
    program = _parse(_script(body=body))
    # The sole fold is zero; locals follow their written declarations.
    _assert_plan(program, (0, (("total", "total"),), expected_locals, "_pf_ctf0_"))


@pytest.mark.parametrize("condition", [
    # Equality reads the string local on the left.
    'tag == "L"',
    # Inequality reads the string local on the left.
    'tag != "L"',
    # Equality also permits the string literal on the left.
    '"L" == tag',
    # Inequality also permits the string literal on the left.
    '"L" != tag',
])
def test_string_guard_comparisons(condition):
    program = _parse(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\n"
        f"if {condition}\n    total += 1\n"
    )))
    # The sole fold is zero; its only row local is tag, and G stays at 2.
    _assert_plan(program, (
        0, (("total", "total"),), (("tag", "entry_id", "std::string"),), "_pf_ctf0_",
    ))


@pytest.mark.parametrize("condition", [
    # Greater-than compares the profit local to a numeric literal.
    "p > 0",
    # Greater-or-equal compares the profit local to a numeric literal.
    "p >= 0",
    # Less-than compares the profit local to a numeric literal.
    "p < 0",
    # Less-or-equal compares the profit local to a numeric literal.
    "p <= 0",
    # Numeric equality compares the profit local to a numeric literal.
    "p == 0",
    # Numeric inequality compares the profit local to a numeric literal.
    "p != 0",
    # Greater-than permits the numeric literal on the left.
    "0 > p",
    # Greater-or-equal permits the numeric literal on the left.
    "0 >= p",
    # Less-than permits the numeric literal on the left.
    "0 < p",
    # Less-or-equal permits the numeric literal on the left.
    "0 <= p",
    # Numeric equality permits the numeric literal on the left.
    "0 == p",
    # Numeric inequality permits the numeric literal on the left.
    "0 != p",
    # A floating literal is also a permitted right-hand numeric operand.
    "p >= 0.5",
    # A floating literal is also a permitted left-hand numeric operand.
    "0.5 >= p",
])
def test_numeric_guard_comparisons(condition):
    program = _parse(_script(body=(
        "p = strategy.closedtrades.profit(i)\n"
        f"if {condition}\n    total += p\n"
    )))
    # The sole fold is zero; each comparison reads the same double local.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ))


def test_six_accumulators_keep_declaration_order():
    program = _parse(_script(
        declarations=(
            "int longTrades = 0\n"
            "int shortTrades = 0\n"
            "int longWins = 0\n"
            "int shortWins = 0\n"
            "float longProfit = 0.0\n"
            "float shortProfit = 0.0\n"
        ),
        body=(
            "entryId = strategy.closedtrades.entry_id(i)\n"
            "profit = strategy.closedtrades.profit(i)\n"
            'if entryId == "Long"\n'
            "    longTrades += 1\n"
            "    longProfit += profit\n"
            "    if profit > 0\n"
            "        longWins += 1\n"
            'if entryId == "Short"\n'
            "    shortTrades += 1\n"
            "    shortProfit += profit\n"
            "    if profit > 0\n"
            "        shortWins += 1\n"
        ),
        tail="plot(longProfit + shortProfit)\n",
    ))
    # Six declarations put G at 7; fold zero keeps their declaration order.
    _assert_plan(program, (
        0,
        (
            ("longTrades", "longTrades"), ("shortTrades", "shortTrades"),
            ("longWins", "longWins"), ("shortWins", "shortWins"),
            ("longProfit", "longProfit"), ("shortProfit", "shortProfit"),
        ),
        (("entryId", "entry_id", "std::string"), ("profit", "profit", "double")),
        "_pf_ctf0_",
    ), guard_position=7)


def test_nested_guards_allow_a_local_used_within_its_scope():
    program = _parse(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\n"
        'if tag == "L"\n'
        "    float p = strategy.closedtrades.profit(i)\n"
        "    if 0 < p\n"
        "        total += p\n"
        "total += 0.5\n"
    )))
    # Fold zero keeps tag before the nested p declaration within one row step.
    _assert_plan(program, (
        0, (("total", "total"),),
        (("tag", "entry_id", "std::string"), ("p", "profit", "double")),
        "_pf_ctf0_",
    ))


def test_unrelated_statements_can_separate_reset_and_guard():
    program = _parse(_script(
        between="distance = close - open\n",
        tail="plot(total + distance)\n",
    ))
    # The unrelated declaration moves G to 3; the sole fold is still zero.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ), guard_position=3)


@pytest.mark.parametrize("between", [
    # The direct read observes the declaration's literal before the fold runs.
    pytest.param("before = total\n", id="read_before_guard"),
    # The conditional read also observes that literal without writing it.
    pytest.param("if close > open\n    plot(total)\n",
                 id="conditional_read_before_guard"),
])
def test_reads_after_declaration_allow_a_later_fold(between):
    program = _parse(_script(between=between))
    # One intervening statement puts G at 3; it still owns only fold zero.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ), guard_position=3)


def test_unused_numeric_declarations_are_not_accumulators():
    program = _parse(_script(prefix="int unused = 7\n"))
    # unused moves G to 3 but has no update; the sole fold is zero.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ), guard_position=3)


def test_accessor_index_uses_the_written_counter_name():
    program = _parse(_script(
        loop="for tradeNumber = 0 to strategy.closedtrades - 1",
        body="p = strategy.closedtrades.profit(tradeNumber)\ntotal += p\n",
    ))
    # A renamed counter leaves the sole fold numbered zero with the same locals.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ))


def test_safe_names_are_mapped_in_accumulator_declaration_order():
    program = _parse(_script(
        declarations="int class = 0\nfloat trades_ = -0.5\n",
        body="trades_ += 0.25\nclass += 1\n",
        loop="for tradeNumber = 0 to strategy.closedtrades - 1",
        tail="plot(class + trades_)\n",
    ))
    mappings = {"class": "_class_", "trades_": "pf_safe_trades_"}
    calls = []

    def safe_name(name):
        calls.append(name)
        return mappings[name]

    # Two declarations put G at 3; fold zero keeps declaration order.
    _assert_plan(program, (
        0, (("class", "_class_"), ("trades_", "pf_safe_trades_")), (), "_pf_ctf0_",
    ), safe_name, guard_position=3)
    # The callback sees the two accumulator names once each, in declaration order.
    assert calls == ["class", "trades_"]


def test_temporary_prefix_inside_string_data_does_not_collide():
    program = _parse(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\n"
        'if tag == "_pf_ctf0_x"\n'
        "    total += 1\n"
    )))
    # The prefix is string data; fold zero has only the authored local tag.
    _assert_plan(program, (
        0, (("total", "total"),), (("tag", "entry_id", "std::string"),), "_pf_ctf0_",
    ))


def test_disjoint_loops_keep_separate_plans_in_source_order():
    program = _parse(
        HEAD
        + "float first = 0.0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        firstProfit = strategy.closedtrades.profit(i)\n"
        + "        first += firstProfit\n"
        + "float second = 0.0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for j = 0 to strategy.closedtrades - 1\n"
        + "        secondProfit = strategy.closedtrades.profit(j)\n"
        + "        second += secondProfit\n"
        + "plot(first + second)\n"
    )
    plans = plan_closed_trade_folds(program, str)
    # Two disjoint guards at 2 and 4 own folds zero and one in source order.
    assert len(plans) == 2
    assert tuple(
        (plan.index, plan.accumulators, plan.locals, plan.stem) for plan in plans
    ) == (
        (0, (("first", "first"),), (("firstProfit", "profit", "double"),), "_pf_ctf0_"),
        (1, (("second", "second"),), (("secondProfit", "profit", "double"),), "_pf_ctf1_"),
    )
    assert plans[0].for_node_id == id(program.body[2].body[0])
    assert plans[0]._for_node is program.body[2].body[0]
    assert plans[1].for_node_id == id(program.body[4].body[0])
    assert plans[1]._for_node is program.body[4].body[0]


def test_a_nonmatching_loop_does_not_hide_a_later_proved_loop():
    program = _parse(
        HEAD
        + "float first = 0.0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        first += strategy.closedtrades.profit(i)\n"
        + "float second = 0.0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for j = 0 to strategy.closedtrades - 1\n"
        + "        second += 1\n"
        + "plot(first + second)\n"
    )
    # The inline update is refused; the sole proved guard at 4 owns fold zero.
    _assert_plan(program, (0, (("second", "second"),), (), "_pf_ctf0_"),
                 guard_position=4)


def test_plan_record_fields_are_fixed_and_frozen():
    plan = _assert_plan(_parse(_script()), (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ))
    # The private AST reference adds no field to the five-field public record.
    assert tuple(field.name for field in fields(FoldPlan)) == (
        "index", "for_node_id", "accumulators", "locals", "stem",
    )
    with pytest.raises(FrozenInstanceError):
        setattr(plan, "index", 10)


@pytest.mark.parametrize("source, expected", [
    # A second assignment before G destroys the literal-reset proof.
    pytest.param(_script(between="total := 1\n"), (), id="write_before_guard"),
    # A second assignment after G is also forbidden by the single-writer proof.
    pytest.param(_script(tail="total := 1\n"), (), id="write_after_guard"),
    # A conditional compound assignment supplies a second write route.
    pytest.param(_script(tail="if close > open\n    total += 1\n"),
                 (), id="conditional_write_after_guard"),
    # A write in a helper is still a second write route for the spelling.
    pytest.param(_script(tail="writeTotal() =>\n    total += 1\n    total\n"),
                 (), id="function_writes_accumulator"),
    # Tuple binding the same spelling violates the unique declaration rule.
    pytest.param(_script(tail="if close > open\n    [total, other] = [1, 2]\n"),
                 (), id="tuple_binding_accumulator"),
    # Another declaration of the accumulator spelling prevents unique binding.
    pytest.param(_script(tail="if close > open\n    float total = 1.0\n"),
                 (), id="shadowed_accumulator"),
    # A second loop cannot bind the accumulator's spelling as its counter.
    pytest.param(_script(tail="for total = 0 to 1\n    other = total\n"),
                 (), id="accumulator_as_another_counter"),
    # Destructured iteration also binds the accumulator's spelling elsewhere.
    pytest.param(_script(tail=(
        "for [total, other] in array.from(1.0)\n    copy = other\n"
    )), (), id="accumulator_as_destructured_counter"),
    # A declaration after G does not reset the accumulator before the loop.
    pytest.param(_script(declarations="", tail="float total = 0.0\nplot(total)\n"),
                 (), id="declaration_after_guard"),
    # A declaration under another block is not a direct child of Program.body.
    pytest.param(_script(declarations="if close > open\n    float total = 0.0\n"),
                 (), id="nested_accumulator_declaration"),
    # Persistent storage does not supply the required fresh literal each bar.
    pytest.param(_script(declarations="var float total = 0.0\n"),
                 (), id="var_accumulator"),
    # Intrabar-persistent storage also lacks the required per-bar reset.
    pytest.param(_script(declarations="varip float total = 0.0\n"),
                 (), id="varip_accumulator"),
    # A bar field changes the seed between calculations.
    pytest.param(_script(declarations="float total = close\n"),
                 (), id="bar_seed"),
    # A call is not a literal seed even when its default is a constant.
    pytest.param(_script(declarations="total = input.float(0.0)\n"),
                 (), id="call_seed"),
    # Constant arithmetic is outside the literal-only seed grammar.
    pytest.param(_script(declarations="total = 1 + 1\n"),
                 (), id="expression_seed"),
    # Unary plus is not the permitted unary-minus seed form.
    pytest.param(_script(declarations="total = +1\n"),
                 (), id="unary_plus_seed"),
    # A string accumulator is outside the numeric accumulator grammar.
    pytest.param(_script(declarations='string total = ""\n'),
                 (), id="string_accumulator"),
    # Direct accumulator history changes the member's storage and clock.
    pytest.param(_script(tail="plot(total[1])\n"), (), id="accumulator_history"),
    # History over a larger expression also reads the accumulator historically.
    pytest.param(_script(tail="plot((total + 1)[1])\n"),
                 (), id="accumulator_expression_history"),
    # Function bodies must not remotely read the accumulator.
    pytest.param(_script(tail="readTotal() => total\nplot(readTotal())\n"),
                 (), id="function_reads_accumulator"),
    # Method bodies have the same remote-read restriction.
    pytest.param(_script(tail="method readTotal(float self) => total\n"),
                 (), id="method_reads_accumulator"),
    # Function defaults are source expressions despite living in annotations.
    pytest.param(_script(tail="readTotal(float value = total) => value\n"),
                 (), id="default_reads_accumulator"),
    # A positional request payload must not capture the accumulator.
    pytest.param(_script(tail=(
        'remote = request.security(syminfo.tickerid, "D", total)\n'
    )), (), id="request_reads_accumulator"),
    # A named request payload has the same remote-read restriction.
    pytest.param(_script(tail=(
        'remote = request.security(syminfo.tickerid, "D", expression = total)\n'
    )), (), id="request_keyword_reads_accumulator"),
    # An equivalent-looking >= guard is outside the exact guard grammar.
    pytest.param(_script(guard="strategy.closedtrades >= 1"),
                 (), id="greater_equal_guard"),
    # Reversing the outer guard is outside the exact grammar.
    pytest.param(_script(guard="0 < strategy.closedtrades"),
                 (), id="reversed_outer_guard"),
    # A different positive threshold omits a trade count the grammar includes.
    pytest.param(_script(guard="strategy.closedtrades > 1"),
                 (), id="changed_guard_threshold"),
    # The unguarded Pine loop runs out-of-range descending iterations at N=0.
    pytest.param(_script(guard=None), (), id="unguarded_loop"),
    # A top-level else branch adds statements outside the accepted guard.
    pytest.param(_script(outer_else="total += 1\n"),
                 (), id="outer_else_branch"),
    # A different lower bound skips the first ledger row.
    pytest.param(_script(loop="for i = 1 to strategy.closedtrades - 1"),
                 (), id="changed_lower_bound"),
    # A different upper bound omits the last ledger row.
    pytest.param(_script(loop="for i = 0 to strategy.closedtrades - 2"),
                 (), id="changed_upper_bound"),
    # A named count is not the exact closedtrades bound expression.
    pytest.param(_script(between="count = strategy.closedtrades\n",
                         loop="for i = 0 to count - 1"),
                 (), id="aliased_count_bound"),
    # Descending traversal cannot resume an oldest-first prefix.
    pytest.param(_script(loop="for i = strategy.closedtrades - 1 to 0"),
                 (), id="descending_loop"),
    # A sliding window depends on the current count in its lower bound.
    pytest.param(_script(loop=(
        "for i = strategy.closedtrades - 2 to strategy.closedtrades - 1"
    )), (), id="window_loop"),
    # Even an explicit unit step is outside the required step=None form.
    pytest.param(_script(loop="for i = 0 to strategy.closedtrades - 1 by 1"),
                 (), id="explicit_unit_step"),
    # A nonunit step does not fold all consecutive rows.
    pytest.param(_script(loop="for i = 0 to strategy.closedtrades - 1 by 2"),
                 (), id="nonunit_step"),
    # An accessor of the next row need not share the current row's finality.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i + 1)\ntotal += p\n"
    )), (), id="next_row_index"),
    # A previous-row expression is also outside the sole-counter grammar.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i - 1)\ntotal += p\n"
    )), (), id="previous_row_index"),
    # A constant index does not denote the row currently being folded.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(0)\ntotal += p\n"
    )), (), id="constant_accessor_index"),
    # Accessor keyword arguments are outside args=[counter], kwargs={} form.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(trade_num = i)\ntotal += p\n"
    )), (), id="accessor_keyword"),
    # An extra positional argument is outside the one-argument accessor form.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i, 0)\ntotal += p\n"
    )), (), id="accessor_extra_argument"),
    # Only entry_id and profit are available as row-local accessors.
    pytest.param(_script(body=(
        "tag = strategy.closedtrades.exit_id(i)\ntotal += 1\n"
    )), (), id="third_accessor"),
    # An inline accessor skips the required local declaration form.
    pytest.param(_script(body="total += strategy.closedtrades.profit(i)\n"),
                 (), id="inline_accessor_update"),
    # The row step must not read the current ledger count.
    pytest.param(_script(body="total += strategy.closedtrades\n"),
                 (), id="count_in_body"),
    # A bar-dependent price cannot be part of a settled row step.
    pytest.param(_script(body="total += close\n"),
                 (), id="bar_value_in_body"),
    # Other strategy state is also bar-dependent.
    pytest.param(_script(body="total += strategy.equity\n"),
                 (), id="strategy_state_in_body"),
    # The counter is permitted only as an accessor's sole argument.
    pytest.param(_script(body="total += i\n"),
                 (), id="counter_as_value"),
    # A write to the counter violates its read-only row-index role.
    pytest.param(_script(body=BODY + "i += 1\n"),
                 (), id="counter_write"),
    # Binding the counter elsewhere prevents a unique loop binding.
    pytest.param(_script(prefix="i = 0\n"), (), id="counter_shadow"),
    # A counter read outside its accessor arguments violates the closed scope.
    pytest.param(_script(tail="plot(i)\nplot(total)\n"),
                 (), id="counter_read_outside"),
    # A profit local cannot be reassigned within the row step.
    pytest.param(_script(body=BODY + "p := 1.0\n"),
                 (), id="local_reassignment"),
    # A local cannot be a second accumulator updated in place.
    pytest.param(_script(body=BODY + "p += 1\n"),
                 (), id="local_compound_write"),
    # A local used before declaration has no proved accessor binding.
    pytest.param(_script(body=(
        "total += p\np = strategy.closedtrades.profit(i)\n"
    )), (), id="local_used_before_declaration"),
    # Another declaration of the local spelling violates unique binding.
    pytest.param(_script(prefix="p = 0.0\n"), (), id="local_declared_outside"),
    # A read after the loop would expose a row-local value outside the step.
    pytest.param(_script(tail="plot(p)\nplot(total)\n"),
                 (), id="local_read_outside"),
    # A parameter with the same local spelling violates its isolated binding.
    pytest.param(_script(tail="identity(float p) => p\n"),
                 (), id="local_parameter_shadow"),
    # The two declarations of p are not one unique local binding.
    pytest.param(_script(body=BODY + "p = strategy.closedtrades.profit(i)\n"),
                 (), id="duplicate_local"),
    # A local declared conditionally is unavailable after that guard's scope.
    pytest.param(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\n"
        'if tag == "L"\n'
        "    p = strategy.closedtrades.profit(i)\n"
        "    total += p\n"
        "total += p\n"
    )), (), id="conditional_local_escape"),
    # A persistent row local would retain state between step invocations.
    pytest.param(_script(body=(
        "var float p = strategy.closedtrades.profit(i)\ntotal += p\n"
    )), (), id="var_local"),
    # Intrabar persistence is also outside the row-local declaration grammar.
    pytest.param(_script(body=(
        "varip float p = strategy.closedtrades.profit(i)\ntotal += p\n"
    )), (), id="varip_local"),
    # An int local would narrow the profit rather than bind the accessor double.
    pytest.param(_script(body=(
        "int p = strategy.closedtrades.profit(i)\ntotal += p\n"
    )), (), id="integer_profit_local"),
    # A string annotation cannot prove a profit local's numeric type.
    pytest.param(_script(body=(
        "string p = strategy.closedtrades.profit(i)\ntotal += p\n"
    )), (), id="wrong_accessor_local_type"),
    # A local alias is not a direct closed-trade accessor declaration.
    pytest.param(_script(body=BODY + "copy = p\n"),
                 (), id="local_alias"),
    # History inside the body is not a current-row numeric update.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\ntotal += p[1]\n"
    )), (), id="local_history_in_body"),
    # A string accessor local is not a numeric += operand.
    pytest.param(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\ntotal += tag\n"
    )), (), id="string_update_operand"),
    # Subtraction is a separate update grammar, excluded from this planner.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\ntotal -= p\n"
    )), (), id="subtraction_update"),
    # Plain reassignment is outside the += update grammar.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\ntotal := p\n"
    )), (), id="plain_update"),
    # A negative update is an AST UnaryOp, not a NumberLiteral update.
    pytest.param(_script(body="total += -1\n"),
                 (), id="unary_update"),
    # A branch with else is outside the one-arm guard grammar.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\n"
        "if p > 0\n    total += p\nelse\n    total += 1\n"
    )), (), id="inner_else_branch"),
    # A guard depending on bar state cannot be replayed from settled row data.
    pytest.param(_script(body="if close > 0\n    total += 1\n"),
                 (), id="bar_guard"),
    # A logical combination is not one permitted local-to-literal comparison.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\n"
        "if p > 0 and p < 10\n    total += p\n"
    )), (), id="compound_guard"),
    # Comparing two accessor locals is not a local-to-literal comparison.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\n"
        "q = strategy.closedtrades.profit(i)\n"
        "if p > q\n    total += p\n"
    )), (), id="two_local_guard"),
    # A negative source literal parses as UnaryOp, outside the Cmp grammar.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\n"
        "if p > -1\n    total += p\n"
    )), (), id="unary_guard_operand"),
    # Ordered string comparisons are outside the equality-only string grammar.
    pytest.param(_script(body=(
        "tag = strategy.closedtrades.entry_id(i)\n"
        'if tag > "L"\n    total += 1\n'
    )), (), id="ordered_string_guard"),
    # A conditional expression is not a literal or profit-local update value.
    pytest.param(_script(body=(
        "p = strategy.closedtrades.profit(i)\ntotal += p > 0 ? p : 0\n"
    )), (), id="ternary_update"),
    # A switch is a separate statement form outside LocalDecl, Guard and Update.
    pytest.param(_script(body="switch\n    true => total += 1\n"),
                 (), id="switch_statement"),
    # break requires a persisted stopped state that the plan does not provide.
    pytest.param(_script(body=BODY + "break\n"), (), id="break_statement"),
    # continue is outside the accepted statement grammar.
    pytest.param(_script(body=BODY + "continue\n"), (), id="continue_statement"),
    # A nested loop is not one row-local statement.
    pytest.param(_script(body="for j = 0 to 1\n    total += 1\n"),
                 (), id="nested_for_loop"),
    # A while loop is also outside the row-step grammar.
    pytest.param(_script(body="while false\n    total += 1\n"),
                 (), id="nested_while_loop"),
    # Collection iteration is outside the row-step grammar.
    pytest.param(_script(body="for value in array.from(1)\n    total += value\n"),
                 (), id="nested_for_in_loop"),
    # Tuple assignment is not an accessor-local declaration.
    pytest.param(_script(body="[left, right] = [1, 2]\ntotal += left\n"),
                 (), id="tuple_statement"),
    # A bare accessor call is an ExprStmt, not a LocalDecl.
    pytest.param(_script(body="strategy.closedtrades.profit(i)\ntotal += 1\n"),
                 (), id="expression_statement"),
    # User calls may have state and are outside the row-step grammar.
    pytest.param(_script(prefix="readProfit(int row) => 1.0\n",
                         body="p = readProfit(i)\ntotal += p\n"),
                 (), id="user_call"),
    # Updating a nonliteral global introduces state outside the accumulator set.
    pytest.param(_script(prefix="other = close\n", body=BODY + "other += 1\n"),
                 (), id="write_to_other_state"),
    # Declaring a local without updating an accumulator is not a reduction.
    pytest.param(_script(body="p = strategy.closedtrades.profit(i)\n"),
                 (), id="no_accumulator_update"),
    # An extra statement beside L makes the outer guard body differ from [L].
    pytest.param(_script().replace(
        "if strategy.closedtrades > 0\n",
        "if strategy.closedtrades > 0\n    other = 0\n",
    ), (), id="extra_guard_statement"),
    # A guard under another conditional is not a direct program child.
    pytest.param(HEAD + "float total = 0.0\nif barstate.islast\n" + indent(
        "if strategy.closedtrades > 0\n"
        "    for i = 0 to strategy.closedtrades - 1\n"
        "        total += 1\n", "    ",
    ), (), id="nested_outer_guard"),
    # A function's local loop is not a direct top-level loop.
    pytest.param(HEAD + "sumRows() =>\n" + indent(
        "float total = 0.0\n"
        "if strategy.closedtrades > 0\n"
        "    for i = 0 to strategy.closedtrades - 1\n"
        "        total += 1\n"
        "total\n", "    ",
    ), (), id="loop_in_function"),
    # A method's local loop has the same top-level exclusion.
    pytest.param(HEAD + "method sumRows(float self) =>\n" + indent(
        "float total = 0.0\n"
        "if strategy.closedtrades > 0\n"
        "    for i = 0 to strategy.closedtrades - 1\n"
        "        total += 1\n"
        "total\n", "    ",
    ), (), id="loop_in_method"),
    # A value-producing if owns its loop below a VarDecl, not Program.body.
    pytest.param(HEAD + "float total = 0.0\n" + (
        "value = if strategy.closedtrades > 0\n"
        "    for i = 0 to strategy.closedtrades - 1\n"
        "        total += 1\n"
    ), (), id="loop_as_value"),
    # Any authored temporary-family variable could capture emitted members.
    pytest.param(_script(prefix="_pf_ctf0_x = 0\n"),
                 (), id="temporary_variable_collision"),
    # The temporary-family exclusion also covers function names.
    pytest.param(_script(prefix="_pf_ctfUnused() => 0\n"),
                 (), id="temporary_function_collision"),
    # Parameter names belong to the authored temporary-family namespace too.
    pytest.param(_script(prefix="identity(float _pf_ctf2_s0) => _pf_ctf2_s0\n"),
                 (), id="temporary_parameter_collision"),
    # A UDT field spelling also reserves the temporary-family prefix.
    pytest.param(_script(prefix="type Cell\n    float _pf_ctf2_s0\n"),
                 (), id="temporary_field_collision"),
    # Enum member spellings also reserve the temporary-family prefix.
    pytest.param(_script(prefix="enum Side\n    _pf_ctf2_s0\n"),
                 (), id="temporary_enum_collision"),
    # Binding strategy as a variable makes namespace resolution ambiguous.
    pytest.param(_script(prefix="strategy = 0\n"),
                 (), id="strategy_variable_shadow"),
    # A callable parameter named strategy also makes the namespace ambiguous.
    pytest.param(_script(prefix="identity(float strategy) => strategy\n"),
                 (), id="strategy_parameter_shadow"),
    # A method receiver can also bind the namespace's spelling.
    pytest.param(_script(prefix="method identity(float strategy) => strategy\n"),
                 (), id="strategy_method_parameter_shadow"),
    # Tuple declarations also bind the namespace's spelling.
    pytest.param(_script(prefix="[strategy, other] = [0, 1]\n"),
                 (), id="strategy_tuple_shadow"),
    # A loop binder named strategy must not be treated as the builtin namespace.
    pytest.param(_script(prefix="for strategy = 0 to 1\n    other = strategy\n"),
                 (), id="strategy_counter_shadow"),
    # An import alias is another route that binds the namespace's spelling.
    pytest.param(_script(prefix="import example/Stats/1 as strategy\n"),
                 (), id="strategy_import_shadow"),
    # A user type sharing the namespace name prevents a builtin proof.
    pytest.param(_script(prefix="type strategy\n    float value = 0.0\n"),
                 (), id="strategy_type_shadow"),
    # A bare strategy value could alias the namespace even beside a canonical loop.
    pytest.param(_script(prefix="alias = strategy\n"),
                 (), id="strategy_namespace_alias"),
    # An aliased namespace receiver does not match the canonical CT node.
    pytest.param(_script(prefix="alias = strategy\n").replace(
        "strategy.closedtrades", "alias.closedtrades",
    ), (), id="aliased_loop_namespace"),
    # An authored profit function prevents proving builtin accessor resolution.
    pytest.param(_script(prefix="profit(int row) => 1.0\n"),
                 (), id="profit_function_name"),
    # The string accessor name is subject to the same authored-function exclusion.
    pytest.param(_script(prefix='entry_id(int row) => "L"\n'),
                 (), id="entry_id_function_name"),
    # An authored method named like an accessor is conservatively excluded too.
    pytest.param(_script(prefix="method profit(float self) => self\n"),
                 (), id="profit_method_name"),
])
def test_near_misses_return_no_plan_without_mapping_names(source, expected):
    calls = []

    def safe_name(name):
        calls.append(name)
        return name

    assert plan_closed_trade_folds(_parse(source), safe_name) == expected
    # Every rejected shape must leave the safe-name allocator untouched.
    assert calls == []


def test_empty_program_has_no_plan():
    # No statement can supply the required direct top-level guarded loop.
    assert plan_closed_trade_folds(_parse(""), str) == ()


def test_argument_order_annotations_do_not_duplicate_syntax_reads():
    program = _parse(_script())
    guard = program.body[2]
    assert isinstance(guard, IfStmt)
    call = guard.body[0].body[0].value
    call.annotations = {"call_arg_order": ArgOrder(call.args), "parent": program}
    # The counter appears once in syntax; aliases leave the sole fold numbered zero.
    _assert_plan(program, (
        0, (("total", "total"),), (("p", "profit", "double"),), "_pf_ctf0_",
    ))

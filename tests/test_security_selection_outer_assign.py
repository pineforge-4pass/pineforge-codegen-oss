"""Requested outer stores and terminal results keep their value contracts."""

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = (
    '//@version=6\nstrategy("Requested values", currency = currency.NONE, '
    "initial_capital = 1000000)\n"
)
SCHEDULE = (
    "if bar_index > 6 and bar_index % 7 == 3\n"
    '    strategy.entry("L", strategy.long, qty = qty_v)\n'
    "if bar_index > 6 and bar_index % 7 == 5\n"
    '    strategy.close("L")\n'
)
REQUEST = "request.security(syminfo.tickerid, timeframe.period, chosen)"
ENTRY_BARS = range(10, 53, 7)
DRIVER = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[60];
    for (int i = 0; i < 60; ++i)
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    std::cout << std::setprecision(17);
    for (int count = 1; count <= 60; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "15", "15");
        if (!strategy.last_error().empty()) return 1;
        std::cout << strategy.got_v << " " << strategy.qty_v << "\n";
    }
}
'''


def outer_source(persistent, *, requested=True, name="balance_v", op=":=", switch=False,
                 else_only=False):
    prefix = (persistent + " ") if isinstance(persistent, str) else ("var " if persistent else "")
    store = f"{name} := {name} + 1" if op == ":=" else f"{name} {op} 1"
    if switch:
        selection = (
            "chosen = switch bar_index % 2\n"
            + ("    0 => bar_index * 3\n    =>\n"
               f"        {store}\n        bar_index * 2\n" if else_only else
               f"    0 =>\n        {store}\n        bar_index * 3\n"
               "    => bar_index * 2\n")
        )
    else:
        selection = (
            "chosen = if bar_index % 2 == 0\n"
            + ("    bar_index * 3\nelse\n"
               f"    {store}\n    bar_index * 2\n" if else_only else
               f"    {store}\n    bar_index * 3\nelse\n    bar_index * 2\n")
        )
    return (
        HEADER
        + f"{prefix}float {name} = 0.0\n"
        + selection
        + f"got_v = {REQUEST if requested else 'chosen'}\n"
        f"qty_v = 1 + (got_v + int({name})) % 5\n"
        + SCHEDULE
    )


def outer_values(bar, persistent):
    # The chart increments on even bars; var retains prior increments,
    # while a plain declaration resets to zero before each chart evaluation.
    outer = bar // 2 + 1 if persistent else int(bar % 2 == 0)
    chosen = bar * (3 if bar % 2 == 0 else 2)
    return chosen, 1 + (chosen + outer) % 5


def run_values(pine, label):
    return [
        tuple(map(float, line.split()))
        for line in run_emitted_tu(
            transpile(pine), DRIVER, opt="-O2", label=label
        ).splitlines()
    ]


def assert_located_refusal(pine, code, line, col):
    # A translation refusal returns no TU to the native harness.
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    errors = [d for d in caught.value.diagnostics if d.code == code]
    assert len(errors) == 1
    diagnostic = errors[0]
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    return diagnostic


@pytest.mark.parametrize("name", ("balance_v", "running_v"))
@pytest.mark.parametrize("op", (":=", "+=", "-=", "*="))
@pytest.mark.parametrize("switch", (False, True))
def test_requested_plain_outer_store_is_a_located_refusal(name, op, switch):
    # Red: the requested arm's plain outer write compiled with unverified values.
    # The accepted plain control's taped quantities are 2,5,4,3,1,1,3;
    # plain outer writes remain a compile-agreement gap rather than a wrong TU.
    pine = outer_source(False, name=name, op=op, switch=switch)
    assert_located_refusal(pine, "PF-E3103", 6 if switch else 5, 9 if switch else 5)


def test_plain_assignment_records_the_accepted_value_before_refusal():
    # The chart's own declaration resets and its even arm adds one; the
    # requested result is 3*b on even bars and 2*b on odd bars for this source.
    expected = [outer_values(b, False)[1] for b in ENTRY_BARS]
    assert expected == [2, 5, 4, 3, 1, 1, 3]
    assert_located_refusal(outer_source(False), "PF-E3103", 5, 5)


@pytest.mark.parametrize("switch", (False, True))
def test_requested_outer_store_uses_evaluator_storage(switch):
    cpp = transpile(outer_source(True, switch=switch))
    evaluator = cpp.split("void _eval_security_0(", 1)[1].split(
        "void evaluate_security(", 1)[0]
    # A request writes its copy; only the chart executes the chart's write.
    # The existing if rewrite already isolated its store; switch was refused.
    assert "_sec0_balance_v" in evaluator
    assert not re.search(r"(?<!\w)balance_v\b", evaluator)


@pytest.mark.parametrize("switch", (False, True))
@pytest.mark.parametrize("persistent", ("var", "varip"))
@pytest.mark.parametrize("op", (":=", "+="))
def test_requested_persistent_outer_values_are_unchanged(switch, persistent, op):
    # Var counts the even chart bars through b: floor(b/2)+1.
    # The if is a green preservation pin; the equivalent switch was refused.
    actual = run_values(outer_source(persistent, switch=switch, op=op),
                        "persistent outer request")
    assert actual == [outer_values(b, True) for b in range(60)]
    assert [actual[b][1] for b in ENTRY_BARS] == [2, 4, 1, 4, 5, 4, 4]


@pytest.mark.parametrize("switch", (False, True))
def test_requested_persistent_write_in_other_arm(switch):
    actual = run_values(outer_source(True, switch=switch, else_only=True),
                        "persistent other arm")
    # Only odd chart bars increment: through b there are floor((b+1)/2).
    expected = [(b * (3 if b % 2 == 0 else 2),
                 1 + (b * (3 if b % 2 == 0 else 2) + (b + 1) // 2) % 5)
                for b in range(60)]
    assert actual == expected
    assert [actual[b][1] for b in ENTRY_BARS] == [1, 4, 5, 4, 4, 4, 3]


@pytest.mark.parametrize("body", (
    "    balance_v += 1\n    bar_index * 3\n",
    "    before_v = balance_v\n    balance_v -= before_v + 1\n    bar_index * 3\n",
    "    if bar_index % 3 == 0\n        balance_v += 1\n    bar_index * 3\n",
    "    for i = 0 to 2\n        balance_v += i\n    bar_index * 3\n",
))
def test_requested_nested_plain_writes_are_refused(body):
    pine = (HEADER + "float balance_v = 0.0\nchosen = if bar_index % 2 == 0\n"
            + body + "else\n    balance_v *= 2\n    bar_index * 2\n"
            + f"got_v = {REQUEST}\n")
    # Any path writing the enclosing plain binding requires a refusal.
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert any(d.code == "PF-E3103" and d.location.line >= 5 for d in caught.value.diagnostics)


@pytest.mark.parametrize("declaration,mutation,read", (
    ("array<float> items_v = array.new<float>(1, 0.0)",
     "array.set(items_v, 0, array.get(items_v, 0) + 1)", "array.get(items_v, 0)"),
    ("array<float> items_v = array.new<float>()",
     "items_v.push(1.0)", "items_v.size()"),
    ("map<string, float> items_v = map.new<string, float>()",
     'map.put(items_v, "k", 1.0)', "map.size(items_v)"),
))
@pytest.mark.parametrize("switch", (False, True))
def test_requested_outer_collection_mutation_is_refused(declaration, mutation, read, switch):
    selection = ("chosen = switch bar_index % 2\n    0 =>\n"
                 if switch else "chosen = if bar_index % 2 == 0\n")
    indent = "        " if switch else "    "
    tail = "    => bar_index * 2\n" if switch else "else\n    bar_index * 2\n"
    pine = (HEADER + declaration + "\n" + selection + indent + mutation + "\n"
            + indent + "bar_index * 3\n" + tail + f"got_v = {REQUEST}\n"
            + f"qty_v = 1 + (got_v + int({read})) % 5\n")
    # A method's mutation writes its receiver just as an assignment does.
    assert_located_refusal(pine, "PF-E3103", 6 if switch else 5, 9 if switch else 5)


def test_requested_local_alias_does_not_hide_an_outer_collection_write():
    pine = (HEADER + "array<float> items_v = array.from(0.0)\n"
            "chosen = if bar_index % 2 == 0\n"
            "    alias_v = items_v\n    alias_v.push(1.0)\n    bar_index * 3\n"
            "else\n    bar_index * 2\n" + f"got_v = {REQUEST}\n")
    # The alias names the same collection; its declaration does not detach it.
    assert_located_refusal(pine, "PF-E3103", 6, 5)


def test_requested_helper_cannot_assign_a_global():
    pine = (HEADER + "float balance_v = 0.0\n"
            "write_v(x) =>\n    balance_v := balance_v + 1\n    x * 3\n"
            "got_v = request.security(syminfo.tickerid, timeframe.period, write_v(bar_index))\n")
    # TradingView forbids global assignment inside a function (CE10088).
    diagnostic = assert_located_refusal(pine, "PF-E3104", 5, 5)
    assert "CE10088" in diagnostic.message


def test_requested_outer_read_keeps_its_values():
    pine = (HEADER + "var float balance_v = 0.0\nbalance_v += 1\n"
            "chosen = if bar_index % 2 == 0\n    bar_index * 3 + int(balance_v)\n"
            "else\n    bar_index * 2\n" + f"got_v = {REQUEST}\n"
            "qty_v = 1 + got_v % 5\n" + SCHEDULE)
    actual = run_values(pine, "outer read")
    # The unconditional increment yields b+1; only the even value reads it.
    expected = [(3*b + b+1 if b % 2 == 0 else 2*b) for b in range(60)]
    assert actual == [(v, 1 + v % 5) for v in expected]
    assert [actual[b][1] for b in ENTRY_BARS] == [2, 5, 3, 3, 4, 1, 5]


def test_requested_outer_collection_read_is_preserved():
    pine = (HEADER + "array<float> items_v = array.from(7.0)\n"
            "chosen = if bar_index % 2 == 0\n"
            "    array.get(items_v, 0) + bar_index * 3\nelse\n    bar_index * 2\n"
            + f"got_v = {REQUEST}\nqty_v = 1 + got_v % 5\n" + SCHEDULE)
    actual = run_values(pine, "outer collection read")
    # The read-only element is seven; selection and quantities still depend
    # on every bar. This pins acceptance without adding a collection write.
    expected = [3*b + 7 if b % 2 == 0 else 2*b for b in range(60)]
    assert actual == [(v, 1 + v % 5) for v in expected]


@pytest.mark.parametrize("has_else", (True, False))
def test_requested_helper_if_keeps_its_values(has_else):
    pine = (
        HEADER
        + "selected_v(x) =>\n"
        "    if x % 2 == 0\n"
        "        part_v = x * 3\n"
        "        part_v + 1\n"
        + ("    else\n        x * 2\n" if has_else else "")
        + "got_v = request.security(syminfo.tickerid, timeframe.period, "
        "selected_v(bar_index))\n"
        "qty_v = 1 + nz(got_v, 0) % 5\n"
        + SCHEDULE
    )
    actual = run_values(pine, "helper selected value")
    # Green preservation pins: even b yields 3*b+1; odd b yields 2*b
    # with else, or na (converted to zero for quantity) without else.
    assert len(actual) == 60
    for bar, (value, quantity) in enumerate(actual):
        expected = 3 * bar + 1 if bar % 2 == 0 else (2 * bar if has_else else math.nan)
        assert math.isnan(value) if math.isnan(expected) else value == expected
        assert quantity == 1 + (0 if math.isnan(expected) else expected) % 5
    tape = [2, 5, 4, 3, 1, 1, 3] if has_else else [2, 1, 4, 1, 1, 1, 3]
    assert [actual[b][1] for b in ENTRY_BARS] == tape


def test_requested_void_helper_if_is_refused_at_call():
    pine = (
        HEADER
        + "action_v(x) =>\n"
        "    if x > 6 and x % 7 == 3\n"
        '        strategy.entry("L", strategy.long, qty = 2)\n'
        "got_v = request.security(syminfo.tickerid, timeframe.period, action_v(bar_index))\n"
    )
    # Red: a statement-only terminal if cannot supply a request value.
    diagnostic = assert_located_refusal(pine, "PF-E3102", 6, 62)
    assert diagnostic.args == {}


@pytest.mark.parametrize("terminal", (
    'strategy.entry("L", strategy.long, qty = 2)',
    'strategy.close("L")',
    'alert("signal")',
    "bgcolor(color.red)",
))
@pytest.mark.parametrize("position", ("direct", "helper", "if", "switch"))
def test_requested_void_result_is_refused_in_every_value_position(terminal, position):
    declarations = ""
    if position == "direct":
        value = terminal
    elif position == "helper":
        declarations = f"action_v() =>\n    {terminal}\n"
        value = "action_v()"
    else:
        selection = (
            f"if bar_index % 2 == 0\n    {terminal}\n"
            if position == "if" else
            f"switch bar_index % 2\n    0 => {terminal}\n"
        )
        declarations = "chosen = " + selection
        value = "chosen"
    pine = (
        HEADER + declarations
        + f"got_v = request.security(syminfo.tickerid, timeframe.period, {value})\n"
    )
    # Red where default-valued terminal lowering hid void; other forms pin
    # the same refusal identity rather than a C++ or runtime failure.
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert any(d.code == "PF-E3102" for d in caught.value.diagnostics)

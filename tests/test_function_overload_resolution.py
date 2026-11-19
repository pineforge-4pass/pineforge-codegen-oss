"""Scalar function overloads share one binding across lowering and emission."""

from __future__ import annotations

import hashlib
from dataclasses import fields
from itertools import zip_longest
from pathlib import Path
from typing import NamedTuple

import pytest

import pineforge_codegen as codegen
from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import ASTNode, FuncCall, FuncDef, Identifier, Program
from pineforge_codegen.errors import CompileError, SourceLocation
from pineforge_codegen.function_overloads import bind_scalar_overloads
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("function overloads")\n'
DEFINITIONS = """probe(float value) => value
probe(int value) => value + 1
"""
PROBE = HEADER + DEFINITIONS + """identity(value) => value
floatResult = probe(close - open)
intResult = probe(2)
unknownResult = probe(identity(close - open))
"""


def _driver(fields: tuple[str, ...], *, counts=(1,), timeframe="") -> str:
    output = ' << " " << '.join("strategy." + field for field in fields)
    lengths = ", ".join(str(count) for count in counts)
    return r'''
#include <iostream>
int main() {
    Bar bars[] = {
        {10.0, 15.0, 9.0, 12.5, 1.0, 0},
        {20.0, 25.0, 19.0, 24.0, 1.0, 60000},
    };
    for (int count : {''' + lengths + r'''}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "''' + timeframe + r'''", "''' + timeframe + r'''");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << ''' + output + r''' << "\n";
    }
}
'''


def test_float_call_uses_the_first_overload_and_compiles() -> None:
    cpp = transpile(PROBE)
    # The first of two definitions has identity pf_overload_probe_1; its float body returns value.
    assert """    double pf_overload_probe_1(double value) {
        return value;
    }""" in cpp
    assert "floatResult = pf_overload_probe_1(" in cpp
    compile_cpp(cpp, label="float and int function overloads")


def test_scheduled_strategy_uses_the_float_overload() -> None:
    source = """//@version=6
strategy("overload strategy", overlay = true, initial_capital = 1000000, pyramiding = 2, process_orders_on_close = false)
cycleIndex = int(math.floor(bar_index / 20))
flip = cycleIndex % 2 == 0
probe(float value) => value
probe(int value) => value + 1
signalValue = probe(close - open)
tradeQty = na(signalValue) ? 0.75 : (signalValue > 0 ? 1.25 : signalValue < 0 ? 2.5 : 3.75) + math.abs(signalValue) / (1.0 + math.abs(signalValue))
if bar_index % 20 == 5
    strategy.entry("Long", strategy.long, qty = tradeQty)
if bar_index % 20 == 15
    strategy.close("Long", comment = "scheduled")
"""
    cpp = transpile(source)
    # The float difference 12.5-10=2.5 must call the first definition before quantity calculation.
    assert "signalValue = pf_overload_probe_1(" in cpp
    compile_cpp(cpp, label="scheduled overload strategy")


def test_integer_call_and_identity_result_bind_by_their_own_type() -> None:
    cpp = transpile(PROBE)
    # Literal 2 exactly matches int. identity(value) returns its argument, here the float
    # difference, which the int parameter does not take.
    assert "intResult = probe(" in cpp
    assert "unknownResult = pf_overload_probe_1(" in cpp
    assert "floatResult = pf_overload_probe_1(" in cpp


def test_float_integer_and_identity_arguments_run_on_literal_bars() -> None:
    cpp = transpile(PROBE)
    # 12.5-10=2.5; int 2 adds 1; the float identity(...) returns reaches the float declaration.
    assert run_emitted_tu(
        cpp, _driver(("floatResult", "intResult", "unknownResult")),
        opt="-O0", label="literal overload arguments",
    ) == "2.5 3 2.5\n"


@pytest.mark.parametrize(
    "definitions, actual",
    [
        # The float difference is 2.5 and selects the first float identity.
        ("probe(float value) => value\nprobe(int value) => value + 1\n",
         "close - open"),
        # Literal 2 is int, so the first int definition returns 3.
        ("probe(int value) => value + 1\nprobe(float value) => value\n", "2"),
        # A comparison is bool, so the first definition negates true to false.
        ("probe(bool value) => not value\nprobe(int value) => value + 1\n",
         "close > open"),
        # A literal string has length four and selects the first string definition.
        ("probe(string value) => str.length(value)\nprobe(int value) => value + 1\n",
         '"abcd"'),
        # str.tostring is certainly string, and its argument 12.5 formats to four characters.
        ("probe(string value) => str.length(value)\nprobe(int value) => value + 1\n",
         "str.tostring(close)"),
        # Hex color 0x12,0x34,0x56 has red channel 18 and selects the first color definition.
        ("probe(color value) => color.r(value)\nprobe(int value) => value + 1\n",
         "#123456"),
    ],
)
def test_proven_scalar_families_select_the_first_identity(
    definitions: str, actual: str,
) -> None:
    cpp = transpile(HEADER + definitions + "result = probe(" + actual + ")\n")
    assert "result = pf_overload_probe_1(" in cpp
    compile_cpp(cpp, label="proven scalar overload")


NAMED_DEFAULTS = HEADER + """var counter = array.new<float>(1, 0.0)
tick() =>
    array.set(counter, 0, array.get(counter, 0) + 1.0)
    array.get(counter, 0)
probe(float value, int scale = 2, float extra = 0.5) => value * scale + extra
probe(int value, int scale = 2, float extra = 0.5) => value + 100
named = probe(value = float(tick()))
afterNamed = array.get(counter, 0)
defaulted = probe(float(tick()))
afterDefaulted = array.get(counter, 0)
reordered = probe(extra = 0.25, value = 2.5, scale = 4)
"""


def test_named_and_default_arguments_evaluate_each_actual_once() -> None:
    cpp = transpile(NAMED_DEFAULTS)
    # tick gives 1 then 2 once each: 1*2+.5=2.5, 2*2+.5=4.5; reordered gives 2.5*4+.25=10.25.
    assert run_emitted_tu(
        cpp, _driver(("named", "afterNamed", "defaulted", "afterDefaulted", "reordered")),
        opt="-O0", label="named and default overload arguments",
    ) == "2.5 1 4.5 2 10.25\n"


def test_unique_overload_output_matches_explicit_literal_identities() -> None:
    source = HEADER + DEFINITIONS + "result = probe(close - open)\n"
    literal = HEADER + """pf_overload_probe_1(float value) => value
probe(int value) => value + 1
result = pf_overload_probe_1(close - open)
"""
    # Renaming the first definition and its float call is the complete transform for this source.
    assert transpile(source) == transpile(literal)


def test_single_definition_keeps_its_literal_emission(monkeypatch) -> None:
    source = HEADER + "probe(float value) => value\nresult = probe(close - open)\n"
    cpp = transpile(source)
    # One float definition keeps probe(double value), returns value, and calls probe directly.
    assert """    double probe(double value) {
        return value;
    }""" in cpp
    assert "result = probe(" in cpp
    assert "pf_overload_" not in cpp
    monkeypatch.setattr(
        codegen, "bind_scalar_overloads", lambda program, **_kwargs: program, raising=False,
    )
    assert cpp == transpile(source)


def test_qualifier_only_overloads_run_the_weakest_declaration_the_argument_fits() -> None:
    source = HEADER + """pick(simple int value) => value + 100
pick(series int value) => value + 900
fromLiteral = pick(7)
fromSeries = pick(bar_index)
"""
    cpp = transpile(source)
    # TradingView's tape of fixtures/tail_f_tv/overload_qualifiers.pine (its README): a
    # literal calls the simple declaration and a series argument the series one.
    assert "fromLiteral = pf_overload_pick_1(" in cpp
    assert "fromSeries = pick(" in cpp
    # Local arithmetic on the two-bar feed: 7 + 100, and bar_index 0 and then 1 plus 900.
    assert run_emitted_tu(
        cpp, _driver(("fromLiteral", "fromSeries"), counts=(1, 2)),
        opt="-O0", label="qualifier-only overloads",
    ) == "107 900\n107 901\n"


def test_qualifier_fixture_binds_each_call_as_the_tradingview_tape_shows() -> None:
    source = (
        Path(__file__).parent / "fixtures" / "tail_f_tv" / "overload_qualifiers.pine"
    ).read_text()
    program = bind_scalar_overloads(Parser(Lexer(source).tokenize(), source=source).parse())
    # fixtures/tail_f_tv/README.md: a literal, an input and a top-level declaration of an
    # input call the simple declaration, written first; bar_index % 5 and minute(time)
    # the series one. tests/test_e2e_tail_f_tapes.py replays the tape itself.
    assert [_bound_call(program, name) for name in ("r1", "r2", "r3", "r4", "r5")] == [
        "pf_overload_pick_1", "pf_overload_pick_1", "pf_overload_pick_1", "pick", "pick",
    ]


def test_duplicate_signature_still_has_its_original_refusal() -> None:
    source = HEADER + """probe(float value) => value
probe(int value) => value + 1
probe(float other) => other + 2.0
result = probe(close)
"""
    # The first and third definitions have the same float signature; names and bodies do not distinguish it.
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert caught.value.diagnostics[0].code == "PF-E2055"


def test_collision_with_an_authored_identity_and_variant_is_avoided() -> None:
    source = HEADER + """pf_overload_probe_1(float value) => value + 20.0
float pf_overload_probe_1_2_cs0 = 99.0
probe(float value) => value
probe(int value) => value + 1
result = probe(close - open)
occupied = pf_overload_probe_1(close - open)
"""
    cpp = transpile(source)
    # The stem and suffix 2's call-site family are occupied, so suffix 3 is the first free identity.
    assert "result = pf_overload_probe_1_3(" in cpp
    assert "occupied = pf_overload_probe_1(" in cpp
    compile_cpp(cpp, label="overload identity collision")


def test_typed_parameters_aliases_and_shadowing_preserve_exact_binding() -> None:
    source = HEADER + DEFINITIONS + """forward(float value) => probe(value)
untyped(value) => probe(value)
difference = close - open
outside = probe(difference)
float total = 0.0
if true
    int difference = 2
    total := probe(difference)
after = probe(difference)
typed = forward(close - open)
fallback = untyped(close - open)
"""
    cpp = transpile(source)
    # The float alias and typed parameter retain 2.5; the block's int 2 becomes 3. The
    # untyped parameter holds its float argument, so its call reaches the float declaration too.
    assert "outside = pf_overload_probe_1(" in cpp
    assert "after = pf_overload_probe_1(" in cpp
    assert run_emitted_tu(
        cpp, _driver(("outside", "total", "after", "typed", "fallback")),
        opt="-O0", label="lexical overload types",
    ) == "2.5 3 2.5 2.5 2.5\n"


@pytest.mark.parametrize(
    "body",
    [
        # A call in the first definition is before the final definition, so the group stays unchanged.
        "probe(float value) => probe(int(value))\n"
        "probe(int value) => value + 1\nresult = probe(close)\n",
        # A call between definitions retains the existing source-order analysis.
        "probe(float value) => value\nresult = probe(close)\n"
        "probe(int value) => value + 1\n",
        # An untyped parameter does not establish a declared scalar signature.
        "probe(value) => value\nprobe(int value) => value + 1\nresult = probe(close)\n",
    ],
)
def test_unresolved_definition_groups_keep_byte_identical_output(
    body: str, monkeypatch,
) -> None:
    source = HEADER + body
    cpp = transpile(source)
    assert "pf_overload_" not in cpp
    monkeypatch.setattr(
        codegen, "bind_scalar_overloads", lambda program, **_kwargs: program, raising=False,
    )
    assert cpp == transpile(source)


def test_ambiguous_defaults_keep_the_later_declaration_and_math_results_bind_by_type() -> None:
    source = HEADER + """probe(float value = 1.5) => value
probe(int value = 2) => value + 1
ambiguous = probe()
fromMath = probe(math.abs(close - open))
exact = probe(close - open)
"""
    cpp = transpile(source)
    # Both declarations take the call without arguments at no cost, so the later one is
    # kept. math.abs of a float is a float, which the int parameter does not take.
    assert "ambiguous = probe(" in cpp
    assert "fromMath = pf_overload_probe_1(" in cpp
    assert "exact = pf_overload_probe_1(" in cpp


@pytest.mark.parametrize("defer_binding", [False, True])
def test_named_math_argument_binds_the_float_declaration(
    defer_binding: bool, monkeypatch,
) -> None:
    if defer_binding:
        monkeypatch.setattr(
            codegen, "bind_scalar_overloads", lambda program, **_kwargs: program,
        )
    source = HEADER + DEFINITIONS + "result = probe(value = math.abs(close - open))\n"
    # Bound, the float result of math.abs reaches the float declaration. With the pass
    # left out, the definitions stay together and the call keeps the final one.
    expected = "result = probe(" if defer_binding else "result = pf_overload_probe_1("
    assert expected in transpile(source)


def test_reassigned_inferred_type_stays_unknown_but_declared_type_is_proven() -> None:
    source = HEADER + DEFINITIONS + """inferred = close - open
inferred := close
float declared = close - open
declared := close
fallback = probe(inferred)
exact = probe(declared)
"""
    cpp = transpile(source)
    # A reassigned inferred binding loses proof, while the explicit float declaration keeps its type.
    assert "fallback = probe(" in cpp
    assert "exact = pf_overload_probe_1(" in cpp


def test_ta_call_sites_are_owned_by_the_selected_overload() -> None:
    source = HEADER + """probe(float value) => ta.sma(value, 2)
probe(int value) => value + 100
first = probe(close - open)
second = probe((close - open) * 2.0)
integer = probe(2)
"""
    cpp = transpile(source)
    # Two float call sites get independent SMA state; (2.5+4)/2=3.25 and (5+8)/2=6.5; int gives 102.
    assert "first = pf_overload_probe_1_cs0(" in cpp
    assert "second = pf_overload_probe_1_cs1(" in cpp
    assert run_emitted_tu(
        cpp, _driver(("first", "second", "integer"), counts=(2,)),
        opt="-O0", label="overload TA ownership",
    ) == "3.25 6.5 102\n"


def test_history_and_persistent_state_follow_the_selected_identity() -> None:
    source = HEADER + """probe(float value) =>
    var float total = 0.0
    total += nz(value[1])
    total
probe(int value) => value + 100
first = probe(close - open)
second = probe((close - open) * 2.0)
integer = probe(2)
"""
    cpp = transpile(source)
    # Bar zero has no previous call; bar one adds 2.5 and 5 to separate persistent totals; int stays 102.
    assert run_emitted_tu(
        cpp, _driver(("first", "second", "integer"), counts=(1, 2)),
        opt="-O0", label="overload history ownership",
    ) == "0 0 102\n2.5 5 102\n"


def test_tuple_return_metadata_belongs_to_the_selected_definition() -> None:
    source = HEADER + """probe(float value) => [value, value + 0.5]
probe(int value) => [value + 100, value + 200]
[first, second] = probe(close - open)
[integer, other] = probe(2)
total = first + second + integer + other
"""
    cpp = transpile(source)
    # 2.5+3+102+202 = 309.5; the scalar observes both selected tuple results.
    assert run_emitted_tu(
        cpp, _driver(("total",)),
        opt="-O0", label="overload tuple metadata",
    ) == "309.5\n"


@pytest.mark.parametrize(
    "body",
    [
        # The global tuple holds 2.5 and 3, so reading both elements gives 5.5.
        """pair(float value) => [value, value + 0.5]
[first, second] = pair(close - open)
total = first + second
""",
        # Calendar reads before the local tuple and outside its helper are 0; the pair still sums to 5.5.
        """pair(float value) => [value, value + 0.5]
readPair() =>
    calendar = second
    [first, second] = pair(close - open)
    first + second + calendar
total = readPair() + second
""",
    ],
    ids=("global", "local"),
)
def test_tuple_element_shadows_the_calendar_builtin(body: str) -> None:
    # The first bar's close-open is 12.5-10=2.5, and the other tuple element is 2.5+.5=3.
    assert run_emitted_tu(
        transpile(HEADER + body), _driver(("total",)),
        opt="-O0", label="tuple calendar name",
    ) == "5.5\n"


def test_tuple_calendar_name_keeps_its_current_value_and_history() -> None:
    source = HEADER + """pair(float value) => [value, value + 0.5]
[first, second] = pair(close - open)
total = first + nz(second[1]) + second
"""
    # Bar zero gives 2.5+0+3=5.5; bar one gives 4+3+4.5=11.5 from the same tuple history.
    assert run_emitted_tu(
        transpile(source), _driver(("total",), counts=(1, 2)),
        opt="-O0", label="tuple calendar history",
    ) == "5.5\n11.5\n"


def test_tuple_calendar_history_has_one_slot_per_bar() -> None:
    source = HEADER + """pair(float value) => [value, value + 0.5]
[first, minute] = pair(close - open)
current = minute[0]
previous = nz(minute[1], -10.0)
olderMissing = na(minute[2])
builtinPrevious = nz(second[1], -20)
"""
    assert run_emitted_tu(
        transpile(source),
        _driver(("current", "previous", "olderMissing", "builtinPrevious"), counts=(1, 2)),
        opt="-O0", label="tuple calendar history slots",
    ) == (
        # 12.5-10+.5=3; tuple offsets 1 and 2 are missing, as is calendar second[1].
        "3 -10 1 -20\n"
        # 24-20+.5=4.5; tuple [1]=3, [2] is missing, and the previous calendar second is 0.
        "4.5 3 1 0\n"
    )


def test_nested_fixnan_state_uses_the_selected_overload_on_each_call_path() -> None:
    source = HEADER + """probe(float value) => fixnan(value)
probe(int value) => value + 100
outer(float value) => probe(value)
source = bar_index == 0 ? close - open : float(na)
first = outer(float(source))
second = outer(float(source * 2.0))
integer = probe(2)
"""
    cpp = transpile(source)
    # The two paths seed fixnan with 2.5 and 5, then each retains its seed on na; the int call gives 102.
    assert run_emitted_tu(
        cpp, _driver(("first", "second", "integer"), counts=(1, 2)),
        opt="-O0", label="nested overload fixnan ownership",
    ) == "2.5 5 102\n2.5 5 102\n"


def test_security_contexts_resolve_the_selected_helper_before_analysis() -> None:
    source = HEADER + """probe(float value, string tf) =>
    request.security(syminfo.tickerid, tf, value)
probe(int value, string tf) => value + 100
first = probe(close - open, "1")
second = probe((close - open) * 2.0, "2")
integer = probe(2, "1")
"""
    cpp = transpile(source)
    # Both requests specialize the float helper; explicit int 2 keeps the separate final definition.
    assert "pf_overload_probe_1" in cpp
    assert 'register_security_eval(0, "1", input_tf_, false, false);' in cpp
    assert 'register_security_eval(1, "2", input_tf_, false, false);' in cpp
    compile_cpp(cpp, label="overload security contexts")


# Overloads that differ by a record (``type``) parameter.

RECORD_HEADER = HEADER + "type Holder\n    float factor\n"
SCALAR_PAIR = "pair(float x) =>\n    [x, 0]\n"
RECORD_PAIR = "pair(Holder h) =>\n    [h, 0]\n"


def _close_driver(field: str) -> str:
    """Runs of one and of two bars, which close at 101.5 and then at 197.25."""
    return r'''
#include <iostream>
int main() {
    Bar bars[] = {
        {100.0, 102.0, 99.0, 101.5, 1.0, 0},
        {196.0, 198.0, 195.0, 197.25, 1.0, 60000},
    };
    for (int count : {1, 2}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "", "");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.''' + field + r''' << "\n";
    }
}
'''


def _bound(body: str) -> Program:
    """The parsed script after the overload pre-pass alone."""
    source = RECORD_HEADER + body
    return bind_scalar_overloads(Parser(Lexer(source).tokenize(), source=source).parse())


def _definitions(program: Program) -> list[str]:
    return [item.name for item in program.body if isinstance(item, FuncDef)]


def _bound_call(program: Program, name: str) -> str:
    """The function the outermost user call in the declaration of ``name`` reaches."""
    statement = next(
        item for item in program.body if getattr(item, "name", None) == name
    )
    return min(
        (depth, node.callee.name) for node, depth in iter_ast_nodes(statement)
        if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
    )[1]


@pytest.mark.parametrize(
    "definitions",
    [
        # The record overload is declared last: the float call was emitted against it.
        pytest.param(SCALAR_PAIR + RECORD_PAIR, id="scalar-then-record"),
        pytest.param(RECORD_PAIR + SCALAR_PAIR, id="record-then-scalar"),
        pytest.param(SCALAR_PAIR, id="scalar-only"),
    ],
)
def test_float_argument_runs_the_scalar_overload_in_either_declaration_order(
    definitions: str,
) -> None:
    source = (
        RECORD_HEADER + definitions
        + "[adjustment, _] = pair(close)\nactual = adjustment\nplot(actual)\n"
    )
    # pair(close) returns its float argument first, so each run ends on its last bar's close.
    assert run_emitted_tu(
        transpile(source), _close_driver("actual"),
        opt="-O0", label="float call of scalar and record overloads",
    ) == "101.5\n197.25\n"


@pytest.mark.parametrize(
    "definitions",
    [
        # The scalar overload is declared last: the record call must not reach it.
        pytest.param(RECORD_PAIR + SCALAR_PAIR, id="record-then-scalar"),
        pytest.param(SCALAR_PAIR + RECORD_PAIR, id="scalar-then-record"),
    ],
)
def test_record_argument_runs_the_record_overload_in_either_declaration_order(
    definitions: str,
) -> None:
    source = (
        RECORD_HEADER + definitions
        + "[held, _] = pair(Holder.new(close))\nactual = held.factor\nplot(actual)\n"
    )
    # The returned record still holds the close it was constructed with.
    assert run_emitted_tu(
        transpile(source), _close_driver("actual"),
        opt="-O0", label="record call of scalar and record overloads",
    ) == "101.5\n197.25\n"


@pytest.mark.parametrize(
    "definitions, scalar, record",
    [
        ("pair(float x) =>\n    [x, 1]\npair(Holder h) =>\n    [h, 2]\n",
         "pf_overload_pair_1", "pair"),
        ("pair(Holder h) =>\n    [h, 2]\npair(float x) =>\n    [x, 1]\n",
         "pair", "pf_overload_pair_1"),
    ],
    ids=("scalar-then-record", "record-then-scalar"),
)
def test_each_call_keeps_the_parameter_and_tuple_of_its_own_overload(
    definitions: str, scalar: str, record: str,
) -> None:
    source = RECORD_HEADER + definitions + """[plain, plainTag] = pair(close)
[held, heldTag] = pair(Holder.new(open))
actual = plain + held.factor + plainTag + heldTag
plot(actual)
"""
    cpp = transpile(source)
    # The earlier declaration has the first identity; each one keeps its declared parameter.
    assert f" {scalar}(double x) {{" in cpp
    assert f" {record}(Holder h) {{" in cpp
    # 101.5 + 100 + 1 + 2 = 204.5 on the first bar; 197.25 + 196 + 1 + 2 = 396.25 on the second.
    assert run_emitted_tu(
        cpp, _close_driver("actual"),
        opt="-O0", label="both calls of scalar and record overloads",
    ) == "204.5\n396.25\n"


def test_int_argument_runs_the_float_overload_declared_before_a_record_one() -> None:
    source = RECORD_HEADER + """scale(float x) => x * 2.0
scale(Holder h) => h.factor
result = scale(3)
"""
    cpp = transpile(source)
    # No record parameter holds the int 3 and the float one accepts it: 3 * 2.0 = 6.
    assert "result = pf_overload_scale_1(" in cpp
    assert run_emitted_tu(
        cpp, _driver(("result",)),
        opt="-O0", label="int call of scalar and record overloads",
    ) == "6\n"


def test_record_bindings_and_typed_parameters_run_the_record_overload() -> None:
    source = RECORD_HEADER + """scale(Holder h) => h.factor
scale(float x) => x * 2.0
forward(Holder h) => scale(h)
Holder declared = Holder.new(close)
inferred = Holder.new(open)
fromDeclared = scale(declared)
fromInferred = scale(inferred)
fromParameter = forward(declared)
fromFloat = scale(close)
"""
    cpp = transpile(source)
    assert "fromDeclared = pf_overload_scale_1(" in cpp
    assert "fromInferred = pf_overload_scale_1(" in cpp
    assert "fromFloat = scale(" in cpp
    # The records hold close 12.5 and open 10; the float call doubles 12.5 to 25.
    assert run_emitted_tu(
        cpp, _driver(("fromDeclared", "fromInferred", "fromParameter", "fromFloat")),
        opt="-O0", label="lexical record overload types",
    ) == "12.5 10 12.5 25\n"


def test_two_record_overloads_run_by_their_constructed_type() -> None:
    source = HEADER + """type Lot
    float units
type Fee
    float cost
measure(Lot lot) => lot.units
measure(Fee fee) => fee.cost * 2.0
lotUnits = measure(Lot.new(close))
feeTwice = measure(Fee.new(open))
"""
    cpp = transpile(source)
    assert "lotUnits = pf_overload_measure_1(" in cpp
    assert "feeTwice = measure(" in cpp
    # The first record holds the close 12.5; the second holds the open 10, which is doubled.
    assert run_emitted_tu(
        cpp, _driver(("lotUnits", "feeTwice")),
        opt="-O0", label="two record overloads",
    ) == "12.5 20\n"


def test_record_overload_output_matches_explicit_literal_identities() -> None:
    use = "[adjustment, _] = {}(close)\nactual = adjustment\nplot(actual)\n"
    source = RECORD_HEADER + SCALAR_PAIR + RECORD_PAIR + use.format("pair")
    literal = (
        RECORD_HEADER + "pf_overload_pair_1(float x) =>\n    [x, 0]\n" + RECORD_PAIR
        + use.format("pf_overload_pair_1")
    )
    # Renaming the scalar definition and its float call is the complete transform for this source.
    assert transpile(source) == transpile(literal)


@pytest.mark.parametrize(
    "definitions, scalar, record",
    [
        ("scale(float x) => x * 2.0\nscale(Holder h) => h.factor\n",
         "pf_overload_scale_1", "scale"),
        ("scale(Holder h) => h.factor\nscale(float x) => x * 2.0\n",
         "scale", "pf_overload_scale_1"),
    ],
    ids=("scalar-then-record", "record-then-scalar"),
)
def test_proven_scalar_and_record_arguments_select_their_declaration(
    definitions: str, scalar: str, record: str,
) -> None:
    program = _bound(definitions + """fromFloat = scale(close)
fromInt = scale(3)
fromRecord = scale(Holder.new(close))
Holder declared = Holder.new(open)
fromDeclared = scale(declared)
""")
    # close is a float and 3 an int only the float parameter accepts; the others are records.
    assert [
        _bound_call(program, name)
        for name in ("fromFloat", "fromInt", "fromRecord", "fromDeclared")
    ] == [scalar, scalar, record, record]


def test_identity_result_binds_by_type_and_unmatched_arguments_keep_the_source_name() -> None:
    program = _bound("""identity(value) => value
scale(float x) => x * 2.0
scale(Holder h) => h.factor
unknown = scale(identity(close))
missing = scale(na)
worded = scale("a")
""")
    assert _definitions(program) == ["identity", "pf_overload_scale_1", "scale"]
    # identity(close) returns the float close, which no record holds. na fits both
    # parameters at no cost, so the later declaration is kept, and a string fits
    # neither: that call keeps the final declaration.
    assert [
        _bound_call(program, name) for name in ("unknown", "missing", "worded")
    ] == ["pf_overload_scale_1", "scale", "scale"]


def test_one_proven_record_argument_selects_beside_an_unproven_one() -> None:
    program = _bound("""identity(value) => value
blend(Holder h, float weight) => h.factor * weight
blend(float x, float weight) => x * weight
partial = blend(Holder.new(close), identity(close))
""")
    # The record rules out the float declaration whatever identity(...) returns.
    assert _bound_call(program, "partial") == "pf_overload_blend_1"


def test_parameter_type_replaces_an_outer_binding_of_the_same_name() -> None:
    program = _bound("""scale(Holder h) => h.factor
scale(float x) => x * 2.0
float level = close
Holder kept = Holder.new(open)
viaRecord(Holder level) => scale(level)
viaFloat(float kept) => scale(kept)
outside = scale(level)
retained = scale(kept)
""")
    # Each name is its parameter's type inside the function and its global's type outside.
    assert [
        _bound_call(program, name)
        for name in ("viaRecord", "viaFloat", "outside", "retained")
    ] == ["pf_overload_scale_1", "scale", "scale", "pf_overload_scale_1"]


@pytest.mark.parametrize(
    "body",
    [
        # An untyped parameter leaves the group without a complete signature.
        "probe(value) => value\nprobe(Holder h) => h.factor\n"
        "result = probe(Holder.new(close))\n",
        # A collection parameter is neither a scalar nor a record.
        "probe(array<float> values) => array.size(values)\n"
        "probe(Holder h) => h.factor\nresult = probe(Holder.new(close))\n",
        # Two declarations with one record signature are not distinct.
        "probe(Holder h) => h.factor\nprobe(Holder other) => other.factor * 2.0\n"
        "result = probe(Holder.new(close))\n",
        # A call between the definitions retains the existing source-order analysis.
        "probe(float value) => value\nresult = probe(close)\n"
        "probe(Holder h) => h.factor\n",
        # A call in the first definition is before the final definition.
        "probe(float value) => probe(Holder.new(value))\n"
        "probe(Holder h) => h.factor\nresult = probe(close)\n",
    ],
    ids=("untyped", "collection", "repeated-record", "forward", "recursive"),
)
def test_unresolved_record_groups_keep_their_declared_names(body: str) -> None:
    program = _bound(body)
    assert _definitions(program) == ["probe", "probe"]
    assert _bound_call(program, "result") == "probe"


# A ``simple`` or ``const`` parameter takes only a value whose qualifier is no
# stronger; a declaration it rules out leaves the call to the others.

SIMPLE_INT = "f(simple int n) => n + 100\n"
RECORD_AND_FLOAT = "f(Holder h) => h.factor\nf(float y) => y * 2.0\n"
# The script TradingView compiles without an error or a warning.
# TradingView's plot values of it were not measured.
MEASURED_SERIES_CALL = (
    '//@version=6\nstrategy("overload review diagnostic")\n'
    "type Holder\n    float factor\n" + SIMPLE_INT + RECORD_AND_FLOAT
    + "actual = f(bar_index)\nplot(actual)\n"
)


def test_series_argument_beside_a_simple_overload_runs_the_float_declaration() -> None:
    cpp = transpile(MEASURED_SERIES_CALL)
    # bar_index is series, which the simple parameter does not take, and no record holds
    # an int: the float declaration, written last, is the one that takes the call.
    assert "actual = f(" in cpp
    # A guard of that selection on the local two-bar feed, not TradingView's values:
    # bar_index 0 and then 1, doubled.
    assert run_emitted_tu(
        cpp, _driver(("actual",), counts=(1, 2)),
        opt="-O0", label="series call beside a simple overload",
    ) == "0\n2\n"


def test_series_argument_runs_an_unqualified_int_overload_of_a_record_group() -> None:
    source = (
        RECORD_HEADER + "f(int n) => n + 100\n" + RECORD_AND_FLOAT
        + "actual = f(bar_index)\nplot(actual)\n"
    )
    cpp = transpile(source)
    # With no qualifier to prove, the int declaration takes the int ahead of the float one.
    assert "actual = pf_overload_f_1(" in cpp
    # bar_index is 0 and then 1, and that declaration adds 100.
    assert run_emitted_tu(
        cpp, _driver(("actual",), counts=(1, 2)),
        opt="-O0", label="series call of an unqualified int overload",
    ) == "100\n101\n"


def test_each_call_of_a_qualified_record_group_binds_by_its_own_qualifier() -> None:
    source = RECORD_HEADER + SIMPLE_INT + RECORD_AND_FLOAT + """relay(int k) => f(k)
fromLiteral = f(7)
fromRelay = relay(bar_index)
"""
    cpp = transpile(source)
    assert "fromLiteral = pf_overload_f_1(" in cpp
    # The literal is a constant, which the simple parameter takes: 7 + 100. The
    # parameter k declares no qualifier and holds the series bar_index, so f(k)
    # reaches the float declaration, which doubles bar_index 0 and then 1.
    assert run_emitted_tu(
        cpp, _driver(("fromLiteral", "fromRelay"), counts=(1, 2)),
        opt="-O0", label="proven and unproven qualifiers in a record group",
    ) == "107 0\n107 2\n"


@pytest.mark.parametrize(
    "definition, use, statement",
    [
        # An operator over literals is a constant.
        pytest.param(SIMPLE_INT, "result = f(3 + 4)\n", "result", id="operator"),
        # A name written once has its initializer's qualifier.
        pytest.param(SIMPLE_INT, "span = 7\nresult = f(span)\n", "result", id="binding"),
        # A parameter declared simple holds a simple value.
        pytest.param(SIMPLE_INT, "relay(simple int k) => f(k)\n", "relay", id="parameter"),
        # An omitted argument has the qualifier of the parameter's default.
        pytest.param(
            "f(simple int n = 7) => n + 100\n", "result = f()\n", "result", id="default",
        ),
        # A constant is no stronger than a const parameter.
        pytest.param("f(const int n) => n + 100\n", "result = f(7)\n", "result", id="const"),
    ],
)
def test_proven_qualifier_selects_the_qualified_declaration_of_a_record_group(
    definition: str, use: str, statement: str,
) -> None:
    program = _bound(definition + RECORD_AND_FLOAT + use)
    assert _bound_call(program, statement) == "pf_overload_f_1"


@pytest.mark.parametrize(
    "definition, use, statement",
    [
        # bar_index is a builtin variable, which no syntax proves simple.
        pytest.param(SIMPLE_INT, "result = f(bar_index + 1)\n", "result", id="builtin-operand"),
        # The cast returns an int, and a call's result has no proven qualifier.
        pytest.param(SIMPLE_INT, "result = f(int(close))\n", "result", id="call"),
        # The script writes the name again.
        pytest.param(
            SIMPLE_INT, "int span = 7\nspan := 8\nresult = f(span)\n", "result", id="rewritten",
        ),
        # The declaration's own series qualifier outranks its constant initializer.
        pytest.param(
            SIMPLE_INT, "var series int span = 7\nresult = f(span)\n", "result",
            id="series-declaration",
        ),
        # A parameter declared series holds a series value.
        pytest.param(
            SIMPLE_INT, "relay(series int k) => f(k)\n", "relay", id="series-parameter",
        ),
        # A simple value is stronger than a const parameter.
        pytest.param(
            "f(const int n) => n + 100\n", "relay(simple int k) => f(k)\n", "relay",
            id="simple-for-const",
        ),
    ],
)
def test_unproven_qualifier_keeps_the_final_declaration_of_a_record_group(
    definition: str, use: str, statement: str,
) -> None:
    program = _bound(definition + RECORD_AND_FLOAT + use)
    # The group is resolved, so the call is the one thing left on the final declaration.
    assert _definitions(program)[:3] == ["pf_overload_f_1", "pf_overload_f_2", "f"]
    assert _bound_call(program, statement) == "f"


def test_scalar_group_passes_over_a_simple_parameter_a_series_argument_cannot_reach() -> None:
    program = _bound(SIMPLE_INT + "f(float y) => y * 2.0\nresult = f(bar_index)\n")
    # The same rule without a record declaration: the simple int parameter does not
    # take the series value, so the float declaration, written last, takes it.
    assert _definitions(program) == ["pf_overload_f_1", "f"]
    assert _bound_call(program, "result") == "f"


def test_series_argument_for_another_functions_simple_parameter_is_still_refused() -> None:
    source = (
        RECORD_HEADER + "g(simple int n) => n + 100\n" + SIMPLE_INT + RECORD_AND_FLOAT
        + "kept = f(bar_index)\nrefused = g(bar_index)\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    # g has one declaration, so its simple parameter refuses the series argument;
    # the call of f above it keeps the final declaration and is not the diagnostic.
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2080"
    assert diagnostic.message == (
        "Cannot call 'g' with a series argument for simple parameter 'n'."
    )


# A body is read with what its parameters hold at each call, so the calls
# inside it are bound by the type and the qualifier of the values it passes on.

SIMPLE_CALLEE = "h(simple int m) => m + 1\nh(float z) => z * 3.0\n"
SIMPLE_FUNCTION = "g(simple int m) => m + 1\n"
HELD_AND_FLOAT = "f(Holder k) => k.factor\nf(float y) => y * 2.0\n"
THROUGH_CALLEE = "f(int n) => h(n)\n" + HELD_AND_FLOAT
# The script TradingView compiles without an error or a warning and plots.
MEASURED_NESTED_CALL = (
    '//@version=6\nstrategy("nested qualifier")\n'
    "type Holder\n    float factor\n" + SIMPLE_CALLEE + THROUGH_CALLEE
    + "actual = f(bar_index)\nplot(actual)\n"
)
# TradingView's plot of that script on BINANCE:BTCUSDT, 60, at its first four
# hourly epochs, as (epoch, value).
N1_TRADINGVIEW_ROWS = (
    (1794268800, 22536.0), (1794272400, 22539.0),
    (1794276000, 22542.0), (1794279600, 22545.0),
)


def test_series_argument_a_body_passes_on_runs_the_float_overload_of_its_callee() -> None:
    cpp = transpile(MEASURED_NESTED_CALL)
    # The int declaration takes the series int ahead of the float one. Its body's h(n)
    # then reaches h(float z): the simple parameter of the other h takes no series value.
    assert "actual = pf_overload_f_1(" in cpp
    output = run_emitted_tu(
        cpp, _driver(("actual",), counts=(1, 2)),
        opt="-O0", label="series call of a body that reaches a simple overload",
    )
    # A guard of that selection on the local two-bar feed, not TradingView's values:
    # bar_index 0 and then 1, times 3.0.
    assert output == "0\n3\n"
    # The plot is a multiple of bar_index, and the multiple names the declarations that
    # ran: 3.0 through h(float z), 2.0 for f(float y), a step of 1 through h(simple int m).
    # TradingView's rows rise by the same amount from one hourly bar to the next. The
    # bar_index of an epoch is not derived here.
    first, second = (float(line) for line in output.split())
    for (earlier, before), (later, after) in zip(
            N1_TRADINGVIEW_ROWS, N1_TRADINGVIEW_ROWS[1:]):
        assert later - earlier == 3600
        assert after - before == second - first


def test_each_call_reads_the_selected_body_with_its_own_qualifier() -> None:
    source = RECORD_HEADER + SIMPLE_CALLEE + THROUGH_CALLEE + """fromLiteral = f(7)
fromSeries = f(bar_index)
"""
    cpp = transpile(source)
    # Both calls select f(int n). Its body's h(n) is bound for each: a constant reaches
    # the simple parameter of h and a series value the float one, so the series call
    # runs a copy of the declaration.
    assert "fromLiteral = pf_overload_f_1(" in cpp
    assert "fromSeries = pf_overload_f_1_v2(" in cpp
    # 7 + 1; bar_index 0 and then 1, times 3.0.
    assert run_emitted_tu(
        cpp, _driver(("fromLiteral", "fromSeries"), counts=(1, 2)),
        opt="-O0", label="const and series qualifiers through a body call",
    ) == "8 0\n8 3\n"


@pytest.mark.parametrize(
    "definitions, use, statement",
    [
        # The function the body calls has one declaration, whose simple
        # parameter does not take the series value.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) => g(n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="function",
        ),
        # An operator over the parameter has the parameter's qualifier.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) => g(n + 1)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="operator",
        ),
        # So does a name initialized from the parameter.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) =>\n    raised = n + 1\n    g(raised)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="binding",
        ),
        # The body names the simple parameter it passes the value to.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) => g(m = n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="keyword",
        ),
        # A method's simple parameter.
        pytest.param(
            "method plus(int self, simple int m) => self + m\n"
            "f(int n) => n.plus(n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="method",
        ),
        # A builtin's simple parameter that the analyzer checks. str.tostring
        # is certainly a string, and a call's result has no proven qualifier.
        pytest.param(
            "f(string d) => strategy.risk.allow_entry_in(d)\n" + HELD_AND_FLOAT,
            "result = f(str.tostring(close))\n", "result", id="builtin",
        ),
        # A simple value is stronger than the const parameter the body reaches.
        pytest.param(
            "g(const int m) => m + 1\nf(int n) => g(n)\n" + HELD_AND_FLOAT,
            "relay(simple int k) => f(k)\n", "relay", id="simple-for-const",
        ),
        # The body gives the simple parameter a value of its own that nothing
        # proves simple, so no call of the declaration is proven, a literal's
        # included.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) => g(bar_index) + n\n" + HELD_AND_FLOAT,
            "result = f(7)\n", "result", id="unproven-body-value",
        ),
        # So does a default the body leaves the simple parameter to take.
        pytest.param(
            "g(simple int m = bar_index) => m + 1\nf(int n) => g() + n\n" + HELD_AND_FLOAT,
            "result = f(7)\n", "result", id="callee-default",
        ),
        # A call in a parameter default is the declaration's own as well.
        pytest.param(
            SIMPLE_CALLEE + "f(Holder k, int w = h(bar_index)) => k.factor + w\n"
            "f(float y) => y * 2.0\n",
            "result = f(Holder.new(close))\n", "result", id="default",
        ),
        # The calls of a declaration inside another are the outer one's too.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) =>\n    inner() => g(n)\n    n + 1\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", id="nested-declaration",
        ),
    ],
)
def test_unproven_value_for_a_parameter_the_body_reaches_keeps_the_final_declaration(
    definitions: str, use: str, statement: str,
) -> None:
    program = _bound(definitions + use)
    # The group is resolved, so the call is the one thing left on the final declaration.
    assert "pf_overload_f_1" in _definitions(program)
    assert _bound_call(program, statement) == "f"


# A call is checked by the declaration it reaches, not by the name the pass gave that
# declaration. The pass renames every earlier declaration of a group and copies one
# whose body binds in more than one way, and which reading keeps the first name
# follows the order the bodies are read in. What a declaration is stays on it through
# a rename and a copy: the parser's record of the name it was written with and where
# (``declaration_names``), its own location, its parameters and its body.


def _parse(source: str) -> Program:
    return Parser(Lexer(source).tokenize(), source=source).parse()


def _functions(program: Program) -> list[FuncDef]:
    return [item for item in program.body if isinstance(item, FuncDef)]


def _frozen(value, spelled: dict[str, str] | None = None):
    """Syntax as tuples of plain values, sharing nothing with the tree it is read from.

    A node is its type, its location and its fields, and a number keeps its type, so
    1 is not 1.0. A call written with a plain name is spelled as ``spelled`` has it:
    the name its declaration was written with, where the pass gave it another.
    """
    if isinstance(value, SourceLocation):
        return (value.file, value.line, value.col, value.end_col)
    if isinstance(value, ASTNode):
        members = []
        for item in fields(value):
            if item.name in ("loc", "annotations"):
                continue
            member = getattr(value, item.name)
            if item.name == "callee" and isinstance(member, Identifier):
                name = (spelled or {}).get(member.name, member.name)
                members.append((item.name, ("Identifier", _frozen(member.loc), name)))
            else:
                members.append((item.name, _frozen(member, spelled)))
        return (type(value).__name__, _frozen(value.loc), tuple(members))
    if isinstance(value, dict):
        return tuple((key, _frozen(item, spelled)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item, spelled) for item in value)
    if isinstance(value, (bool, int, float)):
        return (type(value).__name__, value)
    return value


def _written(definition: FuncDef) -> tuple:
    """The parser's record of the name a declaration was written with, and where."""
    names = _frozen((definition.annotations or {}).get("declaration_names"))
    assert names, f"{definition.name} has no record of the name it was written with"
    return names


class _Origin(NamedTuple):
    """What a declaration is, whatever the pass named it."""

    written: tuple     # the name it was written with, and where
    location: tuple    # its own position in the source
    parameters: tuple  # each parameter's name, type, qualifier and default
    body: tuple        # whether it is one expression, and its statements


def _origin(program: Program, definition: FuncDef) -> _Origin:
    spelled = {item.name: _written(item)[0][0] for item in _functions(program)}
    notes = definition.annotations or {}
    return _Origin(
        written=_written(definition),
        location=_frozen(definition.loc),
        parameters=tuple(zip_longest(
            definition.params,
            notes.get("param_type_hints") or (),
            notes.get("param_qualifiers") or (),
            _frozen(notes.get("param_defaults") or (), spelled),
        )),
        body=(definition.is_single_expr, _frozen(definition.body, spelled)),
    )


def _declaration(program: Program, name: str) -> FuncDef:
    """The one declaration a call written with ``name`` runs."""
    found = [item for item in _functions(program) if item.name == name]
    assert len(found) == 1, f"{name} names {len(found)} declarations"
    return found[0]


def _through(program: Program, definition: FuncDef) -> list[FuncDef]:
    """The declarations the calls in a body run, in source order, each followed by the
    ones its own body runs."""
    declared = {item.name for item in _functions(program)}
    calls = sorted(
        (
            node for node, _depth in iter_ast_nodes(definition)
            if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
            and node.callee.name in declared
        ),
        key=lambda call: (call.callee.loc.line, call.callee.loc.col),
    )
    found: list[FuncDef] = []
    for call in calls:
        target = _declaration(program, call.callee.name)
        found += [target, *_through(program, target)]
    return found


def _written_as(program: Program, source: str, text: str) -> FuncDef:
    """The declaration whose place in the source starts with ``text``: the name, the
    qualifiers and the types the script spells there."""
    lines = source.splitlines()
    found = [
        item for item in _functions(program)
        if lines[item.loc.line - 1][item.loc.col - 1:].startswith(text)
    ]
    assert len(found) == 1, f"{text!r} starts {len(found)} declarations"
    return found[0]


@pytest.mark.parametrize(
    "definitions, use, statement, through",
    [
        # The measured declarations: h(float z) takes the series value the body of
        # f(int n) passes on, so that declaration takes the series call.
        pytest.param(
            SIMPLE_CALLEE + THROUGH_CALLEE, "result = f(bar_index)\n", "result",
            ("h(float z)",), id="series-through-overload",
        ),
        # The series value reaches h(float z) through a second function.
        pytest.param(
            SIMPLE_CALLEE + "via(int a) => h(a)\nf(int n) => via(n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", ("via(int a)", "h(float z)"),
            id="series-through-two-calls",
        ),
        # A literal is a constant, which the simple parameter the body reaches takes.
        pytest.param(
            SIMPLE_CALLEE + THROUGH_CALLEE, "result = f(7)\n", "result",
            ("h(simple int m)",), id="literal",
        ),
        # So is a name written once.
        pytest.param(
            SIMPLE_CALLEE + THROUGH_CALLEE, "span = 7\nresult = f(span)\n", "result",
            ("h(simple int m)",), id="binding",
        ),
        # A parameter declared simple holds a simple value.
        pytest.param(
            SIMPLE_CALLEE + THROUGH_CALLEE, "relay(simple int k) => f(k)\n", "relay",
            ("h(simple int m)",), id="simple-parameter",
        ),
        # A constant is no stronger than the const parameter the body reaches.
        pytest.param(
            "g(const int m) => m + 1\nf(int n) => g(n)\n" + HELD_AND_FLOAT,
            "result = f(7)\n", "result", ("g(const int m)",), id="const",
        ),
        # A string literal is a constant, which the builtin's simple parameter takes.
        # That body calls no function of the script.
        pytest.param(
            "f(string d) => strategy.risk.allow_entry_in(d)\n" + HELD_AND_FLOAT,
            'result = f("long")\n', "result", (), id="builtin",
        ),
        # The function the body calls declares no qualifier.
        pytest.param(
            "g(int m) => m + 1\nf(int n) => g(n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", ("g(int m)",), id="unqualified-callee",
        ),
        # A series parameter takes any value.
        pytest.param(
            "g(series int m) => m + 1\nf(int n) => g(n)\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", ("g(series int m)",), id="series-callee",
        ),
        # The simple parameter takes a literal of the body, not the argument.
        pytest.param(
            SIMPLE_FUNCTION + "f(int n) => g(2) + n\n" + HELD_AND_FLOAT,
            "result = f(bar_index)\n", "result", ("g(simple int m)",), id="own-literal",
        ),
    ],
)
def test_proven_value_for_every_parameter_the_body_reaches_selects_the_declaration(
    definitions: str, use: str, statement: str, through: tuple[str, ...],
) -> None:
    source = RECORD_HEADER + definitions + use
    # What is expected is read from a parse of its own and frozen before the pass runs,
    # so it holds nothing the pass renames or copies. Every row means the first
    # declaration the script writes as f, and ``through`` names what that body's calls
    # reach for the row's value, each by the text the script declares it with.
    authored = _parse(source)
    intended = _origin(
        authored, next(item for item in _functions(authored) if item.name == "f"),
    )
    inner = [_origin(authored, _written_as(authored, source, text)) for text in through]

    program = bind_scalar_overloads(_parse(source))
    reached = _declaration(program, _bound_call(program, statement))
    selected = _origin(program, reached)
    # Where it was written. f(Holder k) and the final f(float y), which a call that no
    # declaration takes keeps, are written on other lines; a copy of the first
    # declaration carries its line whatever number its name ends in.
    assert (selected.written, selected.location) == (intended.written, intended.location)
    # What it declares: the parameter's name, type and qualifier, which differ in each
    # of the other two declarations.
    assert selected.parameters == intended.parameters
    # What it computes: the statements as written, constants included, with each call
    # still naming the function the script calls there.
    assert selected.body == intended.body
    # What that body reaches for this call's value. A copy of the same declaration read
    # for another qualifier differs here alone: the copy a series value runs reaches
    # h(float z), where the one a constant or a simple value runs reaches
    # h(simple int m).
    assert [_origin(program, item) for item in _through(program, reached)] == inner


def test_literal_calls_of_a_simple_relay_run_the_simple_declaration_its_body_reaches() -> None:
    source = RECORD_HEADER + SIMPLE_CALLEE + THROUGH_CALLEE + """relay(simple int k) => f(k)
fromSeven = relay(7)
fromEleven = relay(11)
fromMinusTwo = relay(-2)
"""
    # The simple-parameter row above, with its relay called. Each call runs the relay,
    # then the first declaration of f and, from that body, h(simple int m): the three
    # declarations as the script writes them, found by their text in a parse the pass
    # never sees.
    authored = _parse(source)
    path = [
        _origin(authored, _written_as(authored, source, text)) for text in (
            "relay(simple int k) => f(k)", "f(int n) => h(n)", "h(simple int m) => m + 1",
        )
    ]
    program = bind_scalar_overloads(_parse(source))
    for name in ("fromSeven", "fromEleven", "fromMinusTwo"):
        reached = _declaration(program, _bound_call(program, name))
        assert [
            _origin(program, item) for item in (reached, *_through(program, reached))
        ] == path
    # Local arithmetic on the first bar, not TradingView's values: h(simple int m) adds
    # 1 to each literal. h(float z) would print 21 33 -6 and f(float y) 14 22 -4.
    assert run_emitted_tu(
        transpile(source), _driver(("fromSeven", "fromEleven", "fromMinusTwo")),
        opt="-O0", label="literal calls of a simple relay",
    ) == "8 12 -1\n"


def test_scalar_group_binds_the_callee_of_its_body_by_the_qualifier_it_passes_on() -> None:
    program = _bound(
        SIMPLE_CALLEE + "f(int n) => h(n)\nf(float y) => y * 2.0\nresult = f(bar_index)\n"
    )
    # The same rule without a record declaration: the int declaration takes the int,
    # and its body's h(n) reaches h(float z), which keeps the name h.
    assert _bound_call(program, "result") == "pf_overload_f_1"
    assert _bound_call(program, "pf_overload_f_1") == "h"


def test_series_argument_a_single_declaration_passes_on_is_still_refused() -> None:
    source = (
        RECORD_HEADER + SIMPLE_FUNCTION + "relay(int k) => g(k)\n"
        + "f(int n) => g(n)\n" + HELD_AND_FLOAT
        + "kept = f(bar_index)\nrefused = relay(bar_index)\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    # relay has one declaration, so the series argument it passes on to the
    # simple parameter of g is refused at line 6, where relay calls g. The call
    # of f keeps the final declaration, so line 7, where f calls g, is not refused.
    assert {
        (diagnostic.location.line, diagnostic.code, diagnostic.message)
        for diagnostic in caught.value.diagnostics
        if diagnostic.message.startswith("Cannot call ")
    } == {(
        6, "PF-E2080",
        "Cannot call 'g' with a series argument for simple parameter 'm'.",
    )}


# A copy of a declaration keeps the state its body declares. The untyped relay below
# is read once for a float series and once for an int constant, its probe(x) binds a
# different declaration each time, and so the two calls run two copies of it. Both
# copies declare the same names. A history binding in a block and a ``var`` of a
# record are not stored apart by a name of their own, and with one copy's beside the
# other's these two scripts were refused, where the single relay compiled before the
# pass made copies. They are those scripts byte for byte: each digest is the sha256
# of the measured source.

COPIED_NESTED_HISTORY = """//@version=6
strategy("clone nested history")
probe(float x) => x * 2.0
probe(int x) => x + 1
relay(x) =>
    float result = 0.0
    if bar_index >= 0
        step = probe(x)
        result := nz(step[1])
    result
left = relay(close - open)
right = relay(2)
plot(left + right)
"""
COPIED_PERSISTENT_RECORD = """//@version=6
strategy("clone persistent record")
type Holder
    float factor
probe(float x) => x * 2.0
probe(int x) => x + 1
relay(x) =>
    var Holder state = Holder.new(1.0)
    state.factor := probe(x)
    state.factor
left = relay(close - open)
right = relay(2)
plot(left + right)
"""


@pytest.mark.parametrize(
    "source, digest, expected",
    [
        # Each copy pushes its own step on every bar: (12.5 - 10) * 2.0 = 5 from
        # probe(float x) and 2 + 1 = 3 from probe(int x). The first bar has no
        # step[1]; the second reads the copy's own first-bar value. Two copies
        # pushing into one Series would read each other's: 3 and 8. The final probe
        # for both calls, as before the pass bound them, would print 3 3.
        pytest.param(
            COPIED_NESTED_HISTORY,
            "c94057b7b3261b3a122a179b3237624ed5b940968e69232873b5c8a34e1b4967",
            "0 0\n5 3\n", id="nested-history",
        ),
        # Each copy writes its record's field and reads it back on the same bar:
        # (12.5 - 10) * 2.0 = 5 and then (24 - 20) * 2.0 = 8, beside 2 + 1 = 3. This
        # row shows the script compiles and each copy runs the probe its value
        # selects; a write read back at once does not tell two records from one,
        # which the test after this one does.
        pytest.param(
            COPIED_PERSISTENT_RECORD,
            "d4789ae7744cd973647b3d3c1be7071c0834be04d2dd32224956fbf722a5d3c7",
            "5 3\n8 3\n", id="persistent-record",
        ),
    ],
)
def test_copies_of_a_declaration_with_state_compile_and_run_the_overload_each_selects(
    source: str, digest: str, expected: str,
) -> None:
    assert hashlib.sha256(source.encode()).hexdigest() == digest
    # What is expected is read from a parse the pass never sees: the one relay the
    # script writes, and the probe each of its two calls reaches from that body.
    authored = _parse(source)
    written = _origin(authored, _written_as(authored, source, "relay(x) =>"))
    reached = [
        _origin(authored, _written_as(authored, source, text))
        for text in ("probe(float x)", "probe(int x)")
    ]

    program = bind_scalar_overloads(_parse(source))
    copies = [
        _declaration(program, _bound_call(program, name)) for name in ("left", "right")
    ]
    # Two declarations in the bound program, each the relay as it was written (its
    # place, its parameter and its statements, the names it declares included), and
    # each reaching the probe that takes its own call's value.
    assert copies[0] is not copies[1]
    for definition, inner in zip(copies, reached):
        assert _origin(program, definition) == written
        assert [_origin(program, item) for item in _through(program, definition)] == [inner]

    # Local arithmetic on the two-bar feed, not TradingView's values.
    assert run_emitted_tu(
        transpile(source), _driver(("left", "right"), counts=(1, 2)),
        opt="-O0", label="state of each copy of a declaration",
    ) == expected


def test_each_copy_of_a_declaration_carries_its_own_persistent_record() -> None:
    source = RECORD_HEADER + """probe(float x) => x * 2.0
probe(int x) => x + 1
relay(x) =>
    var Holder state = Holder.new(1.0)
    carried = state.factor
    state.factor := probe(x)
    carried
left = relay(close - open)
right = relay(2)
"""
    # The measured record script, reading the field before it writes it, so what a
    # call returns is what its own record held from the bar before. Both records
    # start at 1.0. On the second bar the float copy returns the 5 it wrote on the
    # first, (12.5 - 10) * 2.0, and the int copy its 3. One record for both copies
    # would hand each the other's write on the second bar: 3 and 8. Local
    # arithmetic, not TradingView's values.
    assert run_emitted_tu(
        transpile(source), _driver(("left", "right"), counts=(1, 2)),
        opt="-O0", label="persistent record of each copy of a declaration",
    ) == "1 1\n5 3\n"


def test_history_collision_the_script_writes_beside_a_copy_is_still_refused() -> None:
    source = HEADER + DEFINITIONS + """relay(x) =>
    float result = 0.0
    if bar_index >= 0
        step = probe(x)
        result := nz(step[1])
    result
other(x) =>
    float result = 0.0
    if bar_index >= 0
        step = x * 3.0
        result := nz(step[1])
    result
left = relay(close - open)
right = relay(2)
third = other(close)
"""
    with pytest.raises(CompileError) as caught:
        transpile(source)
    # Only the copy's step is stored apart. The relay and the other function the
    # script writes still declare one block history name each, which is refused at
    # the later one (line 14, column 9) as it is without the overloads of probe.
    diagnostic = caught.value.diagnostics[0]
    assert (diagnostic.location.line, diagnostic.location.col, diagnostic.message) == (
        14, 9,
        "A callable history local named 'step' shares raw generated Series state "
        "with another lexical declaration; declaration-exact callable storage is "
        "not supported yet.",
    )

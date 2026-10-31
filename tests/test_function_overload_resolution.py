"""Scalar function overloads share one binding across lowering and emission."""

from __future__ import annotations

from pathlib import Path

import pytest

import pineforge_codegen as codegen
from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
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


def test_integer_call_and_unknown_argument_keep_their_own_binding() -> None:
    cpp = transpile(PROBE)
    # Literal 2 exactly matches int; identity(...) has no certain type, so both use the final name.
    assert "intResult = probe(" in cpp
    assert "unknownResult = probe(" in cpp
    assert "floatResult = pf_overload_probe_1(" in cpp


def test_float_integer_and_unknown_arguments_run_on_literal_bars() -> None:
    cpp = transpile(PROBE)
    # 12.5-10=2.5; int 2 adds 1; the unknown call keeps int, narrowing 2.5 to 2 before adding 1.
    assert run_emitted_tu(
        cpp, _driver(("floatResult", "intResult", "unknownResult")),
        opt="-O0", label="literal overload arguments",
    ) == "2.5 3 3\n"


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


def test_qualifier_only_overloads_keep_byte_identical_output(monkeypatch) -> None:
    source = HEADER + """pick(simple int value) => value + 100
pick(series int value) => value + 900
result = pick(7)
"""
    cpp = transpile(source)
    # Equal base signatures retain the final series definition: 7+900=907.
    assert "pf_overload_" not in cpp
    monkeypatch.setattr(
        codegen, "bind_scalar_overloads", lambda program, **_kwargs: program, raising=False,
    )
    assert cpp == transpile(source)


def test_qualifier_fixture_keeps_byte_identical_output(monkeypatch) -> None:
    source = (
        Path(__file__).parent / "fixtures" / "tail_f_tv" / "overload_qualifiers.pine"
    ).read_text()
    cpp = transpile(source)
    # Every pick signature is int, so all five calls retain the existing qualifier-only lookup.
    assert "pf_overload_" not in cpp
    monkeypatch.setattr(
        codegen, "bind_scalar_overloads", lambda program, **_kwargs: program, raising=False,
    )
    assert cpp == transpile(source)


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
    # The float alias and typed parameter retain 2.5; the block's int 2 becomes 3; untyped forwards to 3.
    assert "outside = pf_overload_probe_1(" in cpp
    assert "after = pf_overload_probe_1(" in cpp
    assert run_emitted_tu(
        cpp, _driver(("outside", "total", "after", "typed", "fallback")),
        opt="-O0", label="lexical overload types",
    ) == "2.5 3 2.5 2.5 3\n"


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
        # Equal base signatures with different qualifiers keep the qualifier-only path.
        "probe(simple int value) => value + 100\n"
        "probe(series int value) => value + 900\nresult = probe(7)\n",
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


def test_ambiguous_defaults_and_unproven_math_keep_the_final_lookup() -> None:
    source = HEADER + """probe(float value = 1.5) => value
probe(int value = 2) => value + 1
ambiguous = probe()
unknown = probe(math.abs(close - open))
exact = probe(close - open)
"""
    cpp = transpile(source)
    # Both default-only signatures fit; math.abs has no exact scalar proof here; the float difference does.
    assert "ambiguous = probe(" in cpp
    assert "unknown = probe(" in cpp
    assert "exact = pf_overload_probe_1(" in cpp


@pytest.mark.parametrize("defer_binding", [False, True])
def test_unproven_named_math_argument_keeps_the_overload_group(
    defer_binding: bool, monkeypatch,
) -> None:
    if defer_binding:
        monkeypatch.setattr(
            codegen, "bind_scalar_overloads", lambda program, **_kwargs: program,
        )
    source = HEADER + DEFINITIONS + "result = probe(value = math.abs(close - open))\n"
    # An unresolved math call keeps probe whether earlier definitions were renamed or left together.
    assert "result = probe(" in transpile(source)


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

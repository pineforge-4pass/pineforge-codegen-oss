"""User text must not be written into emitted C++ outside the name allocator.

Pinned by sink. The type-name vocabulary is generated from the codegen's own
keyword and namespace sets; the member vocabulary is the gate's own table set,
exercised on a resolved string receiver; a few named shapes pin the remaining
guards:

* an unknown call is refused with a codegen diagnostic instead of being
  written as C++ call text (a user type used as a namespace; a member name
  outside the receiver's own table);
* an unknown member read on a non-user-type value is refused;
* a type argument / type hint follows the type grammar only, and a type name
  that is not declared is never written into the emitted text.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import (
    CPP_EMITTER_NAMES, CPP_KEYWORDS, cpp_code_only,
)
from pineforge_codegen.codegen.visit_call import PINEFORGE_MEMBER_NAMES
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.lexer import KEYWORDS

HEAD = '//@version=6\nstrategy("raw emit")\n'

# The names a user type is tried under: C++ keywords plus the names the
# emitter itself uses as namespaces.
UDT_NAMES = sorted(
    (set(CPP_KEYWORDS) | (CPP_EMITTER_NAMES & {"std", "pineforge"}))
    - set(KEYWORDS)
    - {"int", "float", "bool", "string", "color"}
)

# Standard-library member spellings that are not Pine methods on a scalar.
NON_PINE_MEMBERS = (
    "copy", "insert", "clear", "size", "append", "resize", "substr",
)


def _assert_codegen_refusal(source: str) -> CompileError:
    with pytest.raises(CompileError) as excinfo:
        transpile(source, filename="raw_emit.pine")
    diagnostic = excinfo.value.diagnostics[0]
    assert diagnostic.phase == Phase.CODEGEN
    assert "is not implemented in PineForge runtime" in diagnostic.message
    return excinfo.value


@pytest.mark.parametrize("name", UDT_NAMES)
def test_udt_named_like_a_namespace_member_call_is_refused(name: str) -> None:
    # Some names are refused earlier by a builtin namespace's own diagnostic
    # or by a keyword; either refusal is acceptable, raw emission is not.
    source = HEAD + f"type {name}\n    float v\n{name}.pf_probe_member()\n"
    with pytest.raises(CompileError):
        transpile(source, filename="raw_emit.pine")


@pytest.mark.parametrize("name", ("std", "pineforge"))
def test_udt_namespace_call_hits_the_codegen_guard(name: str) -> None:
    # These names own no builtin dispatch, so the refusal is the emitter's.
    source = HEAD + f"type {name}\n    float v\n{name}.pf_probe_member()\n"
    _assert_codegen_refusal(source)


@pytest.mark.parametrize("member", NON_PINE_MEMBERS)
def test_unknown_method_on_a_scalar_is_refused(member: str) -> None:
    source = HEAD + 's = "ab"\n' + f"s.{member}()\n"
    _assert_codegen_refusal(source)


def test_unknown_method_on_a_collection_is_refused() -> None:
    source = HEAD + "a = array.new<float>(1)\na.pf_probe_member()\n"
    _assert_codegen_refusal(source)


@pytest.mark.parametrize("member", sorted(PINEFORGE_MEMBER_NAMES))
def test_every_table_method_name_on_a_string_is_refused(member: str) -> None:
    # A string has no Pine methods, so every name in the codegen's tables must
    # be refused on a resolved string receiver rather than written raw.
    source = HEAD + f's = "ab"\nr = s.{member}()\n'
    _assert_codegen_refusal(source)


def test_unknown_method_on_an_untyped_string_parameter_is_refused() -> None:
    source = HEAD + 'f(s) => s.copy()\nr = f("ab")\n'
    _assert_codegen_refusal(source)


@pytest.mark.parametrize(
    "receiver",
    (
        "alert.freq_once_per_bar",
        "currency.USDT",
        "format.percent",
        "order.ascending",
        "session.regular",
    ),
)
def test_unknown_method_on_a_string_constant_receiver_is_refused(
    receiver: str,
) -> None:
    # A builtin namespace constant lowers to a string temporary; its member
    # call must be gated by that emitted type, not by a resolved Pine spec.
    source = HEAD + f"r = {receiver}.copy()\n"
    _assert_codegen_refusal(source)


def test_member_read_on_a_builtin_value_is_refused() -> None:
    source = HEAD + 's = "ab"\nx = s.pf_probe_field\n'
    _assert_codegen_refusal(source)


def test_unknown_chained_receiver_method_is_refused() -> None:
    source = (
        HEAD
        + "type Cell\n    float v\n"
        + "c = Cell.new()\n"
        + "r = c.v.pf_probe_member()\n"
    )
    _assert_codegen_refusal(source)


def test_string_template_argument_is_never_a_type() -> None:
    # The lookahead treats a '<' followed by non-type tokens as a comparison,
    # not as template arguments, so the text cannot reach a type position.
    # Pinned outcome: the comparison spelling transpiles, and the probe never
    # appears in code (literals are blanked by cpp_code_only()).
    source = HEAD + 'a = array.new<"pf_probe_type">(0)\n'
    cpp = transpile(source, filename="raw_emit.pine")
    assert "pf_probe_type" not in cpp_code_only(cpp)


def test_non_type_udt_field_hint_is_a_parse_error() -> None:
    source = HEAD + 'type Cell\n    "x" f\nc = Cell.new()\n'
    with pytest.raises(CompileError):
        transpile(source, filename="raw_emit.pine")


def test_undeclared_array_element_type_is_refused() -> None:
    source = HEAD + "a = array.new<Undeclared>(0)\n"
    with pytest.raises(CompileError) as excinfo:
        transpile(source, filename="raw_emit.pine")
    assert "is not implemented" in str(excinfo.value)


def test_undeclared_array_from_element_type_is_refused() -> None:
    source = HEAD + "a = array.from<Undeclared>(close)\n"
    with pytest.raises(CompileError) as excinfo:
        transpile(source, filename="raw_emit.pine")
    assert "is not implemented" in str(excinfo.value)


@pytest.mark.parametrize("element", ("float", "int", "bool", "string", "color"))
def test_primitive_array_element_type_still_works(element: str) -> None:
    cpp = transpile(HEAD + f"a = array.new<{element}>(0)\n")
    assert "std::vector<" in cpp


@pytest.mark.parametrize("element", ("line", "label", "box", "linefill",
                                    "table", "polyline"))
def test_drawing_and_visual_array_element_types_still_work(element: str) -> None:
    transpile(HEAD + f"a = array.new<{element}>(0)\n")


def test_enum_array_element_type_still_works() -> None:
    source = HEAD + "enum Signal\n    buy\n    sell\na = array.new<Signal>(0)\n"
    transpile(source)


def test_nested_collection_element_type_still_works() -> None:
    transpile(HEAD + "a = array.new<matrix<float>>(0)\n")


def test_nested_map_element_type_with_a_comma_still_works() -> None:
    transpile(HEAD + "a = array.new<map<string, float>>(0)\n")


def test_undeclared_array_element_type_in_a_switch_arm_is_refused() -> None:
    # A switch arm downgrades the support checker's error to a warning; the
    # emitter's own guard must still refuse the element type.
    source = (
        HEAD
        + "x = 1\n"
        + "r = switch x\n"
        + "    1 => array.new<Undeclared>(0)\n"
        + "    => array.new<float>(0)\n"
    )
    _assert_codegen_refusal(source)


def test_enum_array_default_is_na_not_the_first_member() -> None:
    source = HEAD + "enum Signal\n    buy\n    sell\na = array.new<Signal>(2)\n"
    cpp = transpile(source)
    # The vector's element slot is a double, so its absent value is the
    # double sentinel; the integer one is not na after the conversion.
    assert "((2), (na<double>()))" in cpp


def test_declared_udt_array_element_type_still_works() -> None:
    source = HEAD + "type Cell\n    float v\na = array.new<Cell>(0)\n"
    cpp = transpile(source)
    assert "Cell{}" in cpp


def test_chart_point_field_read_still_works() -> None:
    source = (
        HEAD
        + "p = chart.point.now(close)\n"
        + "v = p.price\n"
    )
    transpile(source)


def test_chart_point_from_a_tuple_in_a_function_still_reads_its_field() -> None:
    source = (
        HEAD
        + "f() =>\n"
        + "    [chart.point.now(close), 1]\n"
        + "[p, n] = f()\n"
        + "v = p.price\n"
    )
    transpile(source)


def test_chart_point_unknown_field_is_refused() -> None:
    source = (
        HEAD
        + "p = chart.point.now(close)\n"
        + "v = p.pf_probe_field\n"
    )
    _assert_codegen_refusal(source)


@pytest.mark.parametrize("member", ("size", "copy", "clear"))
def test_array_methods_on_an_array_still_work(member: str) -> None:
    # The receiver's own table still routes these; the guard must not take
    # the legitimate lowering away.
    source = HEAD + "a = array.new<float>(1)\n" + f"v = a.{member}()\n"
    transpile(source)


def test_drawing_method_still_works() -> None:
    source = (
        HEAD
        + "l = line.new(bar_index, close, bar_index + 1, close)\n"
        + "v = l.get_x1()\n"
    )
    transpile(source)

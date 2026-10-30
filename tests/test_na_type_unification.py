"""Compile-backed coverage for context-typed ``na`` expressions."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, have_compile_env


pytestmark = pytest.mark.skipif(
    not have_compile_env(),
    reason="set PINEFORGE_ENGINE_INCLUDE plus Eigen3 and a C++ compiler",
)


def _script(body: str) -> str:
    return f"""//@version=6
strategy("na type")
{body}
if bar_index == 5
    strategy.entry("L", strategy.long)
"""


def _selection_body(
    name: str,
    setup: str,
    value: str,
    na_first: bool,
    prefix: str = "",
) -> str:
    arms = ("na", value) if na_first else (value, "na")
    return f"""{prefix}{name}(bool choose) =>
    {setup}
    choose ? {arms[0]} : {arms[1]}
selected = {name}(true)
"""


_SELECTION_ROWS = [
    (
        "int-na-first",
        _selection_body(
            "choose_int_na_first", "values = array.from(7)", "values.shift()", True,
        ),
        "int choose_int_na_first(",
        # Hand-derived: an int array element and na produce a Pine int.
    ),
    (
        "int-na-second",
        _selection_body(
            "choose_int_na_second", "values = array.from(7)", "values.shift()", False,
        ),
        "int choose_int_na_second(",
        # Hand-derived: an int array element and na produce a Pine int.
    ),
    (
        "float-na-first",
        _selection_body(
            "choose_float_na_first",
            "values = array.from(1.5)",
            "values.shift()",
            True,
        ),
        "double choose_float_na_first(",
        # Hand-derived: a float array element and na produce a Pine float.
    ),
    (
        "float-na-second",
        _selection_body(
            "choose_float_na_second",
            "values = array.from(1.5)",
            "values.shift()",
            False,
        ),
        "double choose_float_na_second(",
        # Hand-derived: a float array element and na produce a Pine float.
    ),
    (
        "bool-na-first",
        _selection_body(
            "choose_bool_na_first",
            "values = array.from(true)",
            "values.shift()",
            True,
        ),
        "bool choose_bool_na_first(",
        # Hand-derived: a bool array element and na produce a Pine bool.
    ),
    (
        "bool-na-second",
        _selection_body(
            "choose_bool_na_second",
            "values = array.from(true)",
            "values.shift()",
            False,
        ),
        "bool choose_bool_na_second(",
        # Hand-derived: a bool array element and na produce a Pine bool.
    ),
    (
        "string-na-first",
        _selection_body(
            "choose_string_na_first",
            'values = array.from("x")',
            "values.shift()",
            True,
        ),
        "std::string choose_string_na_first(",
        # Hand-derived: a string array element and na produce a Pine string.
    ),
    (
        "string-na-second",
        _selection_body(
            "choose_string_na_second",
            'values = array.from("x")',
            "values.shift()",
            False,
        ),
        "std::string choose_string_na_second(",
        # Hand-derived: a string array element and na produce a Pine string.
    ),
    (
        "color-na-first",
        _selection_body(
            "choose_color_na_first",
            "values = array.from(color.red)",
            "values.shift()",
            True,
        ),
        "int64_t choose_color_na_first(",
        # Hand-derived: a color array element and na produce a Pine color.
    ),
    (
        "color-na-second",
        _selection_body(
            "choose_color_na_second",
            "values = array.from(color.red)",
            "values.shift()",
            False,
        ),
        "int64_t choose_color_na_second(",
        # Hand-derived: a color array element and na produce a Pine color.
    ),
    (
        "udt-na-first",
        _selection_body(
            "choose_box_na_first",
            "value = Box.new(1.0)",
            "value",
            True,
            prefix="type Box\n    float value\n",
        ),
        "Box choose_box_na_first(",
        # Hand-derived: a Box value and na produce a nullable Box handle.
    ),
    (
        "udt-na-second",
        _selection_body(
            "choose_box_na_second",
            "value = Box.new(1.0)",
            "value",
            False,
            prefix="type Box\n    float value\n",
        ),
        "Box choose_box_na_second(",
        # Hand-derived: a Box value and na produce a nullable Box handle.
    ),
    (
        "array-string-na-first",
        _selection_body(
            "choose_array_string_na_first",
            'value = array.from("x")',
            "value",
            True,
        ),
        "std::vector<std::string> choose_array_string_na_first(",
        # Hand-derived: an array<string> value and na produce array<string>.
    ),
    (
        "array-string-na-second",
        _selection_body(
            "choose_array_string_na_second",
            'value = array.from("x")',
            "value",
            False,
        ),
        "std::vector<std::string> choose_array_string_na_second(",
        # Hand-derived: an array<string> value and na produce array<string>.
    ),
    (
        "array-float-na-first",
        _selection_body(
            "choose_array_float_na_first",
            "value = array.from(1.5)",
            "value",
            True,
        ),
        "std::vector<double> choose_array_float_na_first(",
        # Hand-derived: an array<float> value and na produce array<float>.
    ),
    (
        "array-float-na-second",
        _selection_body(
            "choose_array_float_na_second",
            "value = array.from(1.5)",
            "value",
            False,
        ),
        "std::vector<double> choose_array_float_na_second(",
        # Hand-derived: an array<float> value and na produce array<float>.
    ),
    (
        "map-na-first",
        _selection_body(
            "choose_map_na_first",
            "value = map.new<string, float>()",
            "value",
            True,
        ),
        "PineMap<std::string, double> choose_map_na_first(",
        # Hand-derived: a map<string, float> value and na keep the map type.
    ),
    (
        "map-na-second",
        _selection_body(
            "choose_map_na_second",
            "value = map.new<string, float>()",
            "value",
            False,
        ),
        "PineMap<std::string, double> choose_map_na_second(",
        # Hand-derived: a map<string, float> value and na keep the map type.
    ),
    (
        "matrix-na-first",
        _selection_body(
            "choose_matrix_na_first",
            "value = matrix.new<float>(1, 1)",
            "value",
            True,
        ),
        "PineMatrix choose_matrix_na_first(",
        # Hand-derived: a matrix<float> value and na keep the matrix type.
    ),
    (
        "matrix-na-second",
        _selection_body(
            "choose_matrix_na_second",
            "value = matrix.new<float>(1, 1)",
            "value",
            False,
        ),
        "PineMatrix choose_matrix_na_second(",
        # Hand-derived: a matrix<float> value and na keep the matrix type.
    ),
]


@pytest.mark.parametrize(
    "label,body,expected_signature",
    _SELECTION_ROWS,
    ids=[row[0] for row in _SELECTION_ROWS],
)
def test_na_adopts_each_value_family(
    label: str,
    body: str,
    expected_signature: str,
) -> None:
    cpp = transpile(_script(body))
    assert expected_signature in cpp
    compile_cpp(cpp, label=label)


def test_issue_shape_string_return_compiles() -> None:
    source = """type Box
    array<string> items
var b = Box.new(array.new<string>())
take(Box x) => x.items.size() > 2 ? x.items.shift() : na
b.items.push("a")
taken = take(b)
"""
    cpp = transpile(_script(source))
    # Hand-derived: array<string>.shift() is a string, so take returns string.
    assert "std::string take(" in cpp
    compile_cpp(cpp, label="string-return-selection")


def test_nested_ternaries_keep_the_concrete_terminal_type() -> None:
    source = """nested_left(bool outer, bool inner) =>
    values = array.from("x")
    outer ? na : inner ? values.shift() : na
nested_right(bool outer, bool inner) =>
    values = array.from("x")
    outer ? inner ? na : values.shift() : na
left_value = nested_left(false, true)
right_value = nested_right(false, false)
"""
    cpp = transpile(_script(source))
    # Hand-derived: both nested selections have one concrete string arm.
    assert "std::string nested_left(" in cpp
    assert "std::string nested_right(" in cpp
    compile_cpp(cpp, label="nested-na-selections")


def test_indented_and_single_line_function_returns_adopt_string() -> None:
    source = """indented_value(bool choose) =>
    values = array.from("x")
    if choose
        values.shift()
    else
        na
single_line_value(bool choose) =>
    values = array.from("x")
    choose ? na : values.shift()
indented_result = indented_value(true)
single_line_result = single_line_value(false)
"""
    cpp = transpile(_script(source))
    # Hand-derived: each function has a string arm and an na arm.
    assert "std::string indented_value(" in cpp
    assert "std::string single_line_value(" in cpp
    compile_cpp(cpp, label="indented-and-single-line-returns")


def test_if_expression_and_typed_array_initialization_use_the_target_type() -> None:
    source = """values = array.from("x")
array<string> missing = na
string selected = if bar_index == 0
    values.shift()
else
    na
"""
    cpp = transpile(_script(source))
    # Hand-derived: missing is vector<string>, and selected is string.
    assert "std::vector<std::string> missing;" in cpp
    assert "std::string selected;" in cpp
    compile_cpp(cpp, label="if-expression-target-types")


def test_typed_arguments_comparisons_and_explicit_string_cast_compile() -> None:
    source = """type Box
    float value
take_text(string value) =>
    value
take_array(array<string> value) =>
    value
take_box(Box value) =>
    value.value
var string persistent_text = na
text_result = take_text(na)
array_result = take_array(na)
box_result = take_box(na)
string explicit_text = string(na)
string current_text = "x"
text_is_na = current_text == na
int current_int = 1
int_is_na = current_int == na
bool current_bool = true
bool_is_na = current_bool == na
color current_color = color.red
color_is_na = current_color == na
"""
    cpp = transpile(_script(source))
    # Hand-derived: typed parameters and comparison operands keep their authored types.
    assert "std::string take_text(" in cpp
    assert "std::vector<std::string> take_array(" in cpp
    assert "double take_box(" in cpp
    compile_cpp(cpp, label="typed-na-arguments-and-comparisons")


def test_two_na_arms_keep_the_existing_numeric_fallback() -> None:
    source = """both_missing(bool choose) =>
    choose ? na : na
result = both_missing(true)
"""
    cpp = transpile(_script(source))
    # Hand-derived: with no concrete arm, the existing double fallback remains.
    assert "double both_missing(" in cpp
    compile_cpp(cpp, label="two-na-fallback")

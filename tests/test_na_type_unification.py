"""Compile-backed coverage for context-typed ``na`` expressions."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.types import TypeHelper
from pineforge_codegen.ast_nodes import BoolLiteral, NumberLiteral, Ternary
from pineforge_codegen.codegen.types import TypeInferer
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp, have_compile_env, run_emitted_tu


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
        # Hand-derived: an int array element and na keep the integer return slot.
    ),
    (
        "int-na-second",
        _selection_body(
            "choose_int_na_second", "values = array.from(7)", "values.shift()", False,
        ),
        "int choose_int_na_second(",
        # Hand-derived: reversing the nullable arms keeps the integer return slot.
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
        "string-deep-na",
        "choose_deep_string(bool choose) =>\n    "
        + 'choose ? "x" : ' * 300
        + "na\nselected = choose_deep_string(true)\n",
        "std::string choose_deep_string(",
        # Hand-derived: all 300 concrete arms are strings, so the terminal na is string.
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
        "pf_safe_Box choose_box_na_first(",
        # Hand-derived: Box and na retain the user handle, escaped past the drawing's Box.
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
        "pf_safe_Box choose_box_na_second(",
        # Hand-derived: Box and na retain the user handle, escaped past the drawing's Box.
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
    if expected_signature.startswith(("int ", "double ")):
        function_body = cpp.split(expected_signature, 1)[1].split("\n    }", 1)[0]
    if expected_signature.startswith("int "):
        # An integer return evaluates the NaN selection once and guards its narrowing.
        assert "return [&](){ auto _pf_v = (" in function_body
        assert "na<double>()" in function_body
        assert (
            "return (_pf_v >= -2147483648.0 && _pf_v < 2147483648.0) "
            "? (int)_pf_v : na<int>(); "
            "else return is_na(_pf_v) ? na<int>() : (int)_pf_v; }();"
        ) in function_body
    elif expected_signature.startswith("double "):
        # A numeric return pairs its concrete arm with double NaN, never an int sentinel.
        assert "na<double>()" in function_body
        assert "na<int>()" not in function_body
    compile_cpp(cpp, label=label)


@pytest.mark.parametrize(
    "body,line,col",
    [
        pytest.param(
            _selection_body(
                "choose_bool_na_first", "values = array.from(true)", "values.shift()", True,
            ),
            5, 14,  # Four spaces and choose ? put the true-arm na at column 14.
            id="bool-na-first",
        ),
        pytest.param(
            _selection_body(
                "choose_bool_na_second", "values = array.from(true)", "values.shift()", False,
            ),
            5, 31,  # The values.shift() true arm puts the false-arm na at column 31.
            id="bool-na-second",
        ),
    ],
)
def test_na_adopts_each_value_family_refuses_bool_na(body: str, line: int, col: int) -> None:
    # A bool cannot be na in Pine v6, even when read from an array.
    with pytest.raises(CompileError) as caught:
        transpile(_script(body))
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2043"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "helper_class", [TypeHelper, TypeInferer], ids=["analyzer", "codegen"],
)
@pytest.mark.parametrize("nested_arm", ["true", "false"])
def test_ternary_type_queries_visit_each_arm_once(
    helper_class,
    nested_arm: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = NumberLiteral(value=65)
    for index in range(64):
        arm = NumberLiteral(value=index + 1)
        value = Ternary(
            condition=BoolLiteral(value=True),
            true_val=value if nested_arm == "true" else arm,
            false_val=arm if nested_arm == "true" else value,
        )

    nullability_checks = 0
    joins = 0
    original_contains_na = helper_class._selection_contains_na
    original_selection_spec = helper_class._selection_type_spec

    def contains_na(cls, node):
        nonlocal nullability_checks
        nullability_checks += 1
        return original_contains_na(node)

    def selection_spec(self, branches, **kwargs):
        nonlocal joins
        joins += 1
        return original_selection_spec(self, branches, **kwargs)

    monkeypatch.setattr(helper_class, "_selection_contains_na", classmethod(contains_na))
    monkeypatch.setattr(helper_class, "_selection_type_spec", selection_spec)
    helper = helper_class()
    if helper_class is TypeInferer:
        # Hand-derived: every value arm is an integer, so C++ inference returns int.
        assert helper._infer_type(value) == "int"
    else:
        # Hand-derived: a primitive selection without na keeps the analyzer's absent spec.
        assert helper._type_spec_from_expr(value) is None
    # Hand-derived: 64 ternaries require 64 joins, independent of the nested arm.
    assert joins == 64
    # Hand-derived: 64 ternaries plus 65 terminal arms allow at most 129 structural checks.
    assert nullability_checks <= 129


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
    # Hand-derived: missing is vector<string>; the global string has an empty initial slot.
    assert "std::vector<std::string> missing;" in cpp
    assert 'std::string selected = std::string("");' in cpp
    compile_cpp(cpp, label="if-expression-target-types")


def test_typed_arguments_comparisons_and_explicit_string_cast_compile() -> None:
    source = """import examples/NaComparisons/1 as comparisons
type Box
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
text_is_na = comparisons.text(current_text)
int current_int = 1
int_is_na = comparisons.integer(current_int)
bool current_bool = true
bool_is_na = na(current_bool)
bool_equal = current_bool == bool_is_na
color current_color = color.red
color_is_na = comparisons.tint(current_color)
"""
    library = """//@version=5
library("NaComparisons")
export text(string value) => value == na
export integer(int value) => value == na
export tint(color value) => value == na
"""
    # Hand-derived: na(current_bool) is PF-E2072 at line 22, column 17.
    with pytest.raises(CompileError) as caught:
        transpile(
            _script(source),
            filename="na-type.pine",
            libraries={"examples/NaComparisons/1": library},
        )
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2072"
    assert diagnostic.args == {}
    assert diagnostic.message == (
        "Cannot call 'na' with a bool argument: "
        "Pine v6 bool values cannot be na."
    )
    assert diagnostic.location.file == "na-type.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (22, 17)
    assert diagnostic.location.end_col == 29

    # A numeric source produces the same bool predicate for the valid companion.
    valid_source = source.replace(
        "bool_is_na = na(current_bool)",
        "bool_is_na = na(current_int)",
    )
    cpp = transpile(
        _script(valid_source), libraries={"examples/NaComparisons/1": library},
    )
    # Hand-derived: typed parameters and comparison operands keep their authored types.
    assert "std::string take_text(" in cpp
    assert "std::vector<std::string> take_array(" in cpp
    assert "double take_box(" in cpp
    # A string comparison returns bool and keeps its string parameter.
    assert "bool NaComparisons_v1__text(std::string " in cpp
    # The integer literal 1 fits the declared parameter's 32-bit C++ storage.
    assert "bool NaComparisons_v1__integer(int " in cpp
    # A color parameter uses the packed 64-bit color representation.
    assert "bool NaComparisons_v1__tint(int64_t " in cpp
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


_NUMERIC_NA_SOURCE = _script("""int n = 5
y = n + na
sum_left = na + n
difference_right = n - na
difference_left = na - n
product_right = n * na
product_left = na * n
quotient_right = n / na
quotient_left = na / n
remainder_right = n % na
remainder_left = na % n
time_right = time + na
time_left = na + time
int missing = na
var int persistent_missing = na
int selected_missing = bar_index == 0 ? na : n
int selected_value = bar_index == 0 ? n : na
float d = 5.0
double_result = d + na
plot(na(y) ? 1 : 0)
""")


_NUMERIC_NA_ASSIGNMENTS = [
    # 5 + NaN is NaN; borrowing INT_MIN instead would give -2147483643.
    ("y", "y = (5 + na<double>());"),
    # NaN + 5 is NaN, with the missing operand evaluated first.
    ("sum_left", "sum_left = (na<double>() + 5);"),
    # 5 - NaN is NaN, without integer subtraction or overflow.
    ("difference_right", "difference_right = (5 - na<double>());"),
    # NaN - 5 is NaN, with the missing operand on the left.
    ("difference_left", "difference_left = (na<double>() - 5);"),
    # 5 * NaN is NaN, without multiplying an integer sentinel.
    ("product_right", "product_right = (5 * na<double>());"),
    # NaN * 5 is NaN, with the missing operand on the left.
    ("product_left", "product_left = (na<double>() * 5);"),
    # 5 / NaN is NaN; division keeps its existing explicit double casts.
    ("quotient_right", "quotient_right = ((double)(5) / (double)(na<double>()));"),
    # NaN / 5 is NaN; moving the missing operand left does not change its type.
    ("quotient_left", "quotient_left = ((double)(na<double>()) / (double)(5));"),
    # fmod(5, NaN) is NaN; a finite integer sentinel would instead give 5.
    ("remainder_right", "remainder_right = std::fmod((double)(5), (double)(na<double>()));"),
    # fmod(NaN, 5) is NaN; a finite integer sentinel would instead give -3.
    ("remainder_left", "remainder_left = std::fmod((double)(na<double>()), (double)(5));"),
    # A 64-bit timestamp plus NaN is NaN, so na must not use int64_t.
    ("time_right", "time_right = (current_bar_.timestamp + na<double>());"),
    # NaN plus a 64-bit timestamp is NaN in the opposite operand order too.
    ("time_left", "time_left = (na<double>() + current_bar_.timestamp);"),
]


@pytest.fixture(scope="module")
def numeric_na_cpp() -> str:
    cpp = transpile(_NUMERIC_NA_SOURCE)
    compile_cpp(cpp, label="numeric-na-binary-operands")
    return cpp


@pytest.mark.parametrize(
    "target,expected_assignment",
    _NUMERIC_NA_ASSIGNMENTS,
    ids=[row[0] for row in _NUMERIC_NA_ASSIGNMENTS],
)
def test_numeric_binary_na_keeps_the_float_fallback(
    numeric_na_cpp: str,
    target: str,
    expected_assignment: str,
) -> None:
    assignment = next(
        line.strip() for line in numeric_na_cpp.splitlines()
        if line.strip().startswith(f"{target} = ")
    )
    assert assignment == expected_assignment
    assert "na<int>()" not in assignment
    assert "na<int64_t>()" not in assignment
    # Hoisted plain globals use the initializer's double storage for a bare na.
    assert "double missing = 0.0;" in numeric_na_cpp
    assert "missing = na<double>();" in numeric_na_cpp
    # A persistent declared int's constructor keeps the integer sentinel.
    assert "persistent_missing(na<int>())" in numeric_na_cpp
    # Both declared integer selections keep double storage and a NaN arm.
    assert "double selected_missing = 0.0;" in numeric_na_cpp
    assert "double selected_value = 0.0;" in numeric_na_cpp
    assert "? (na<double>()) : (5)" in numeric_na_cpp
    assert "? (5) : (na<double>())" in numeric_na_cpp
    # 5.0 + NaN already uses double and must keep the same emitted expression.
    assert "double_result = (5.0 + na<double>());" in numeric_na_cpp


_NUMERIC_NA_DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <limits>
#include <type_traits>

int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_same_v<decltype(strategy.selected_missing), double>);
    static_assert(std::is_same_v<decltype(strategy.selected_value), double>);
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.5, 10.0, 1700000000000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 1;
    std::printf("%d %d %d %.17g %d\n", std::isnan(strategy.y),
                is_na(strategy.missing),
                std::isnan(strategy.selected_missing),
                strategy.selected_value, std::isnan(strategy.double_result));
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_numeric_binary_na_is_nan_after_one_bar(opt: str) -> None:
    source = _script("""int n = 5
y = n + na
int missing = na
int selected_missing = bar_index == 0 ? na : n
int selected_value = bar_index == 0 ? n : na
float d = 5.0
double_result = d + na
plot(na(y) ? 1 : 0)
""")
    output = run_emitted_tu(
        transpile(source), _NUMERIC_NA_DRIVER, opt=opt, label="numeric-na-binary-operands",
    )
    # 5 + NaN is NaN: isnan is 1 instead of 0 for the finite INT_MIN + 5.
    # The host prepares bar 0; both missing values are na, the other selection is 5.
    # 5.0 + NaN is NaN, giving the final 1 independently of integer storage.
    assert output == "1 1 1 5 1\n"


_INT_NA_SUM_ROWS = [
    # Integer 5 plus na is NaN, retained in the destination's double storage.
    pytest.param("n + na", "(5 + na<double>())", id="na-right"),
    # Na plus integer 5 is na with the operands in their written order.
    pytest.param("na + n", "(na<double>() + 5)", id="na-left"),
]


def _int_na_sum_source(expression: str) -> str:
    return _script(
        f"int n = 5\nint missing_sum = {expression}\n"
        "bool missing_value = na(missing_sum)\n"
    )


@pytest.mark.parametrize("expression,arithmetic", _INT_NA_SUM_ROWS)
def test_integer_na_sum_keeps_double_storage(
    expression: str, arithmetic: str,
) -> None:
    cpp = transpile(_int_na_sum_source(expression))
    # The declared int stores the NaN sum directly, without a narrowing cast.
    assert "double missing_sum = 0.0;" in cpp
    assert f"missing_sum = {arithmetic};" in cpp
    assert "auto _pf_v = " not in cpp
    compile_cpp(cpp, label="integer sum with bare na")


_INT_NA_SUM_DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <limits>
#include <type_traits>

int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_same_v<decltype(strategy.missing_sum), double>);
    Bar bar{100.0, 101.0, 99.0, 100.5, 10.0, 1700000000000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 1;
    std::printf("%d %d\n",
                std::isnan(strategy.missing_sum),
                strategy.missing_value);
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("expression,arithmetic", _INT_NA_SUM_ROWS)
def test_integer_na_sum_stores_nan(
    expression: str, arithmetic: str, opt: str,
) -> None:
    # Both sums are NaN: the stored double is NaN and Pine na() is true.
    assert run_emitted_tu(
        transpile(_int_na_sum_source(expression)), _INT_NA_SUM_DRIVER,
        opt=opt, label="integer sum with bare na",
    ) == "1 1\n"


_FLOAT_NA_TERNARY_ROWS = [
    pytest.param(
        "float y = close > open ? 1 : na\n",
        "y = ((",
        "? (1) : (na<double>())",
        # The bear bar takes na; the bull bar takes the integer value 1.
        id="float-na-second",
    ),
    pytest.param(
        "float y = close < open ? na : 1\n",
        "y = ((",
        "? (na<double>()) : (1)",
        # The bear bar takes the first-arm na; the bull bar takes 1.
        id="float-na-first",
    ),
    pytest.param(
        "choose_value(bool cond) =>\n"
        "    float local = cond ? 1 : na\n"
        "    local\n"
        "float y = choose_value(close > open)\n",
        "double local = ((",
        "? (1) : (na<double>())",
        # The function's float local holds na on the bear bar and 1 on the bull bar.
        id="function-local-float",
    ),
    pytest.param(
        "choose_value(bool cond) =>\n"
        "    if cond\n"
        "        1.0\n"
        "    else\n"
        "        cond ? 1 : na\n"
        "float y = choose_value(close > open)\n",
        "_func_ret = ((",
        "? (1) : (na<double>())",
        # The float function return keeps the bear branch's missing arm as NaN.
        id="float-function-return",
    ),
    pytest.param(
        "accept_float(float value) => value\n"
        "float y = accept_float(close > open ? k : na)\n",
        "accept_float(((",
        "? (k) : (na<double>())",
        # The declared int and the float argument both select NaN or 1.
        id="int-sink-and-float-argument",
    ),
    pytest.param(
        "float y = float(close > open ? 1 : na)\n",
        "(double)(((",
        "? (1) : (na<double>())",
        # An explicit float cast keeps the selected NaN or 1.
        id="explicit-float-cast",
    ),
    pytest.param(
        "float y = 0.0\n"
        "y := close > open ? 1 : na\n",
        "y = ((",
        "? (1) : (na<double>())",
        # Reassignment to a float slot preserves na and the bull bar's value 1.
        id="float-reassignment",
    ),
    pytest.param(
        "var float y = close > open ? 1 : na\n",
        "y = ((",
        "? (1) : (na<double>())",
        # Each fresh strategy first reaches the persistent float on its only bar.
        id="persistent-float",
    ),
    pytest.param(
        "float selected = close > open ? 1 : na\n"
        "previous = selected[1]\n"
        "float y = selected\n"
        "if not na(previous)\n"
        '    runtime.error("the first history slot must be na")\n',
        "selected.push(((",
        "? (1) : (na<double>())",
        # The current float slot is na or 1; its first previous slot is always na.
        id="float-history-slot",
    ),
    pytest.param(
        "values = array.new<float>(1, close > open ? 1 : na)\n"
        "float y = values.get(0)\n",
        "std::vector<double>",
        "? (1) : (na<double>())",
        # The sole float array element is na on the bear bar and 1 on the bull bar.
        id="float-array-element",
    ),
    pytest.param(
        "float y = 0.0 + (close > open ? 1 : na)\n",
        "(0.0 + ((",
        "? (1) : (na<double>())",
        # Adding zero to the promoted selection preserves NaN or the value 1.
        id="float-binary-operand",
    ),
    pytest.param(
        "float y = close == open ? 2.0 : (close > open ? 1 : na)\n",
        ": (((",
        "? (1) : (na<double>())",
        # Both unequal-price bars take the inner int selection in a float ternary.
        id="nested-float-selection",
    ),
    pytest.param(
        "type Cell\n"
        "    float value\n"
        "cell = Cell.new(close > open ? 1 : na)\n"
        "float y = cell.value\n",
        ".value = ((",
        "? (1) : (na<double>())",
        # A float record field selects NaN or 1 without integer conversion.
        id="float-record-field",
    ),
    pytest.param(
        "float selected_time = close > open ? time : na\n"
        "float y = selected_time / 1700000000000.0\n",
        "selected_time = ((",
        "? (current_bar_.timestamp) : (na<double>())",
        # The exact timestamp divided by itself is 1; the missing arm is NaN.
        id="wide-integer-selection",
    ),
    pytest.param(
        "values = array.from(1)\n"
        "float y = close > open ? values.shift() : na\n"
        "if values.size() != (close > open ? 0 : 1)\n"
        '    runtime.error("selection evaluated an incorrect number of arms")\n',
        "y = ((",
        ": (na<double>())",
        # Bear keeps the single element; bull shifts it once and returns 1.
        id="selected-arm-evaluated-once",
    ),
]


def _float_na_ternary_source(body: str) -> str:
    return _script(
        "int k = close > open ? 1 : na\n"
        + body
        + "bool missing_value = na(y)\n"
        + 'if na(y)\n    strategy.entry("N", strategy.long)\n'
    )


@pytest.mark.parametrize("body,conversion,arms", _FLOAT_NA_TERNARY_ROWS)
def test_float_na_ternary_preserves_nan_in_both_targets(
    body: str, conversion: str, arms: str,
) -> None:
    cpp = transpile(_float_na_ternary_source(body))
    # A float sink stores double, including a hoisted initializer inferred as int.
    assert "double y =" in cpp
    # The selected expression already has a NaN arm and needs no repair wrapper.
    assert conversion in cpp
    assert "auto _pf_w = (" not in cpp
    assert arms in cpp
    # The companion declared int also uses double storage and a NaN missing arm.
    assert "double k = 0.0;" in cpp
    k_assignment = next(
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith("k = ")
    )
    assert "? (1) : (na<double>())" in k_assignment
    assert "na<int>()" not in k_assignment
    compile_cpp(cpp, label="float-na-ternary")


_FLOAT_NA_TERNARY_DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <limits>
#include <type_traits>

int main() {
    GeneratedStrategy bear_strategy;
    GeneratedStrategy bull_strategy;
    static_assert(std::is_same_v<decltype(bear_strategy.k), double>);
    pineforge::Bar bear{100.0, 101.0, 98.0, 99.0, 10.0, 1700000000000LL};
    pineforge::Bar bull{100.0, 102.0, 99.0, 101.0, 10.0, 1700000000000LL};
    bear_strategy.run(&bear, 1);
    bull_strategy.run(&bull, 1);
    if (!bear_strategy.last_error().empty() || !bull_strategy.last_error().empty())
        return 1;
    std::printf("%d %d %d %d %d %d\n",
                std::isnan(bear_strategy.y), bear_strategy.missing_value,
                std::isnan(bear_strategy.k),
                bull_strategy.y == 1.0, bull_strategy.missing_value,
                bull_strategy.k == 1.0);
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("body,conversion,arms", _FLOAT_NA_TERNARY_ROWS)
def test_float_na_ternary_is_nan_on_a_bear_bar(
    body: str, conversion: str, arms: str, opt: str,
) -> None:
    output = run_emitted_tu(
        transpile(_float_na_ternary_source(body)),
        _FLOAT_NA_TERNARY_DRIVER,
        opt=opt,
        label="float-na-ternary",
    )
    # Bear: both doubles are NaN and na(y) is true; bull: both are 1, na(y) is false.
    assert output == "1 1 1 1 0 1\n"

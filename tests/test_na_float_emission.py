"""Pin unchanged controls and the exact integer-to-float boundary changes."""

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import reference_codegen, transpile_json
from tests._numeric_expected import (
    integer_arithmetic, integer_store, integer_to_double, require_numeric_hunk,
)


def _previous_codegen() -> Path:
    # Fixed main snapshot before integer-to-float conversion changed.
    commit = "883221a00585"
    tree = reference_codegen(commit)
    if tree is None:
        pytest.fail(f"Reference {commit} unavailable; fetch main with full history.")
    return tree


def test_proven_non_na_emission_unchanged(tmp_path):
    tree = _previous_codegen()
    fixture = Path(__file__).parent / "fixtures" / "reuse_sma152.pine"
    old = transpile_json(fixture, tree)
    current = transpile_json(fixture)
    assert old.get("ok"), old
    assert current.get("ok"), current
    assert current["cpp"] == old["cpp"]
    pine = tmp_path / "finite.pine"
    pine.write_text(
        '//@version=6\nstrategy("Finite numeric writes")\n'
        "float value = bar_index\nplot(value)\n",
        encoding="utf-8",
    )
    old = transpile_json(pine, tree)
    current = transpile_json(pine)
    assert old.get("ok"), old
    assert current.get("ok"), current
    assert current["cpp"] == old["cpp"]


@pytest.mark.parametrize("body", [
    'period = input.int(7, "Window", minval=1, maxval=50)\n'
    "float observed = period * 3 - 1\n",
    'float observed = input.int(7, "Window") * 3\n',
    "period = input(7)\nfloat observed = period * 3\n",
    "choose() =>\n    int anchor = bar_index\n    (anchor + 3) * 7\n"
    "observed = choose()\n",
    "float observed = year(time) + month(time)\n",
    "int multiplier = timeframe.multiplier\nfloat observed = multiplier * 3\n",
    "items = array.new<int>(3, 0)\nfloat observed = array.size(items) * 2\n",
    "int cached = int(na)\nfloat observed = nz(cached, 4) + 2\n",
    "choose() =>\n    float result = 0.0\n    for step = 0 to 1\n"
    "        copy = step\n        result += copy\n    result\n"
    "float observed = choose()\n",
])
def test_integer_source_controls_keep_their_existing_emission(tmp_path, body):
    """Compare these controls byte for byte.

    The input proof covers input.int and generic integer input; input.time
    retains conservative guards. A declared float initialized by an integer
    helper changes storage and is checked separately below.
    """
    pine = tmp_path / "finite.pine"
    pine.write_text(
        '//@version=6\nstrategy("Finite integer sources")\n' + body + "plot(observed)\n",
        encoding="utf-8",
    )
    previous = transpile_json(pine, _previous_codegen())
    current = transpile_json(pine)
    assert previous.get("ok"), previous
    assert current.get("ok"), current
    cpp = current["cpp"]
    if body == "items = array.new<int>(3, 0)\nfloat observed = array.size(items) * 2\n":
        # 7f2cdbd1: a missing array size returns an empty array. The present
        # size and fill, including all other invalid-size stops, stay frozen.
        cpp = require_numeric_hunk(
            cpp,
            'if(is_na(_pf_array_new_size)) _PF_ARRAY_STOP("size_invalid", "new", '
            '"cannot create std::vector larger than max_size()");',
            "if(is_na(_pf_array_new_size)) return std::vector<int>();",
        )
    assert cpp == previous["cpp"]


@pytest.mark.parametrize("tail", ["", "observed := 0.375\n"])
def test_declared_float_preserves_storage_after_an_integer_function_result(tmp_path, tail):
    from pineforge_codegen import _parse_bounded
    from pineforge_codegen.ast_nodes import Assignment, Identifier, NumberLiteral, VarDecl

    pine = tmp_path / "declared_float.pine"
    source = (
        '//@version=6\nstrategy("Declared float storage")\n'
        "choose() =>\n    int anchor = bar_index\n    (anchor + 3) * 7\n"
        "float observed = choose()\n" + tail + "plot(observed)\n"
    )
    pine.write_text(source, encoding="utf-8")
    program, _pragmas, _budget = _parse_bounded(source, str(pine))
    declaration, = (
        node for node in program.body
        if isinstance(node, VarDecl) and node.name == "observed"
    )
    assert declaration.type_hint == "float"
    float_writes = [
        node for node in program.body
        if isinstance(node, Assignment) and isinstance(node.target, Identifier)
        and node.target.name == declaration.name
        and isinstance(node.value, NumberLiteral)
        and isinstance(node.value.value, float)
    ]
    # The reference already infers double storage from a float literal write.
    # Without one, it follows the integer helper's result despite the float
    # declaration. The corrected storage follows that declaration in both cases.
    previous_type = "double" if float_writes else "int"
    previous_default = "0.0" if float_writes else "0"
    previous_declaration = f"    {previous_type} observed = {previous_default};"
    previous_reset = f"        this->observed = {previous_default};"
    current_declaration = "    double observed = 0.0;"
    current_reset = "        this->observed = 0.0;"

    previous = transpile_json(pine, _previous_codegen())
    current = transpile_json(pine)
    assert previous.get("ok"), previous
    assert current.get("ok"), current
    assert previous["cpp"].count(previous_declaration) == 1
    assert previous["cpp"].count(previous_reset) == 1
    assert current["cpp"].count(current_declaration) == 1
    assert current["cpp"].count(current_reset) == 1
    for write in float_writes:
        assignment = f"observed = {write.value.value};"
        assert previous["cpp"].count(assignment) == 1
        assert current["cpp"].count(assignment) == 1

    cpp = current["cpp"]
    if not float_writes:
        cpp = require_numeric_hunk(cpp, previous_declaration, current_declaration)
        # prepare_script_run copies the member declaration's default.
        cpp = require_numeric_hunk(cpp, previous_reset, current_reset)
    cpp = require_numeric_hunk(
        cpp, "observed = choose();",
        f"observed = {integer_to_double('choose()')};",
    )
    assert cpp == previous["cpp"]


@pytest.mark.parametrize("payload,element_type,value", [
    ("int(na)", "int", integer_store("na<double>()")),
    ("close", "double", "bar.close"),
    ("false", "bool", "false"),
    (
        "int(na) + 0", "double",
        integer_to_double(integer_arithmetic(integer_store("na<double>()"), "+", "0")),
    ),
])
def test_lower_timeframe_elements_convert_to_their_declared_type(payload, element_type, value):
    # An integer vector keeps the integer encoding; a double vector receives NaN.
    cpp = transpile(
        '//@version=6\nstrategy("Requested element types")\n'
        f'values = request.security_lower_tf(syminfo.tickerid, "1", {payload})\n'
        "plot(array.size(values))\n",
    )
    assert f"std::vector<{element_type}> _req_sec_lower_tf_0{{}};" in cpp
    assert f"_req_sec_lower_tf_0.push_back({value});" in cpp


@pytest.mark.parametrize("coordinate,converted", [
    ("value()", integer_to_double("value()")),
    ("time[1]", integer_to_double("time[1]")),
])
@pytest.mark.parametrize("kind,call", [
    ("line", "pf_line_new(_pf_lines_, (int64_t)(0), (double)({v}), "
             "(int64_t)(1), (double)({v}), XLoc::bar_index, false, false)"),
    ("box", "pf_box_new(_pf_boxes_, (int64_t)(0), (double)({v}), "
            "(int64_t)(1), (double)({v}), XLoc::bar_index)"),
    ("label", "pf_label_new(_pf_labels_, (int64_t)(0), (double)({v}), "
              'std::string(""), XLoc::bar_index, YLoc::price)'),
])
def test_drawing_constructors_preserve_missing_integer_prices(coordinate, converted, kind, call):
    args = f"0, {coordinate}" if kind == "label" else f"0, {coordinate}, 1, {coordinate}"
    cpp = transpile(
        '//@version=6\nstrategy("Missing drawing prices")\n'
        "value() => int(na)\n"
        f"shape_value = {kind}.new({args})\n",
    )
    assert call.format(v=converted) in cpp


@pytest.mark.parametrize("coordinate,converted", [
    ("value()", integer_to_double("value()")),
    ("time[1]", integer_to_double("time[1]")),
])
@pytest.mark.parametrize("functional", [False, True])
@pytest.mark.parametrize("kind,method,has_x", [
    ("line", "set_y1", False),
    ("line", "set_y2", False),
    ("line", "set_xy1", True),
    ("line", "set_xy2", True),
    ("box", "set_top", False),
    ("box", "set_bottom", False),
    ("box", "set_lefttop", True),
    ("box", "set_rightbottom", True),
    ("label", "set_y", False),
    ("label", "set_xy", True),
])
def test_drawing_setters_preserve_missing_integer_prices(
    coordinate, converted, functional, kind, method, has_x,
):
    args = f"0, {coordinate}" if has_x else coordinate
    update = (f"{kind}.{method}(shape_value, {args})" if functional
              else f"shape_value.{method}({args})")
    arena = {"line": "_pf_lines_", "box": "_pf_boxes_", "label": "_pf_labels_"}[kind]
    x = "(int64_t)(0), " if has_x else ""
    cpp = transpile(
        '//@version=6\nstrategy("Missing drawing updates")\n'
        "value() => int(na)\n"
        f"var {kind} shape_value = na\n"
        f"{update}\n",
    )
    assert (
        f"_pf_drawing_set(pf_{kind}_{method}, {arena}, shape_value, "
        f"{x}(double)({converted}))"
    ) in cpp


@pytest.mark.parametrize("coordinate,converted", [
    ("value()", integer_to_double("value()")),
    ("time[1]", integer_to_double("time[1]")),
])
@pytest.mark.parametrize("method,args,fields", [
    ("now", "{v}", ".index=(int64_t)(pine_bar_index()), .time=(int64_t)current_bar_.timestamp"),
    ("from_index", "0, {v}", ".index=(int64_t)(0), .time=na<int64_t>()"),
    ("from_time", "0, {v}", ".index=na<int64_t>(), .time=(int64_t)(0)"),
    ("new", "0, 0, {v}", ".index=(int64_t)(0), .time=(int64_t)(0)"),
])
def test_chart_points_preserve_missing_integer_prices(coordinate, converted, method, args, fields):
    cpp = transpile(
        '//@version=6\nstrategy("Missing point prices")\n'
        "value() => int(na)\n"
        f"point_value = chart.point.{method}({args.format(v=coordinate)})\n",
    )
    assert f"ChartPoint{{ {fields}, .price=({converted}) }}" in cpp


_MUTABLE_INPUT_INTEGER_SOURCE = (
    "alias_value = input.int(7)\nalias_value := int(na)\nobserved = alias_value\n"
)
_PERSISTENT_INTEGER_SOURCE = (
    "var int retained = 0\nretained := int(na)\nobserved = retained\n"
)
_UNPROVED_TIME_INPUT_SOURCE = "observed = input.time(0)\n"


@pytest.mark.parametrize("body", [
    "observed = ta.change(bar_index)\n",
    "values = array.new<int>(1)\nobserved = values.get(0)\n",
    _MUTABLE_INPUT_INTEGER_SOURCE,
    'observed = request.security(syminfo.tickerid, "60", int(na))\n',
    _UNPROVED_TIME_INPUT_SOURCE,
    _PERSISTENT_INTEGER_SOURCE,
])
def test_nullable_integer_sources_do_not_gain_a_non_missing_proof(body):
    from pineforge_codegen import _generate
    from pineforge_codegen.ast_nodes import VarDecl

    generator, context, _cpp, _diagnostics, _sites = _generate(
        '//@version=6\nstrategy("Unproved integer sources")\n' + body,
        True, "<input>",
    )
    assert context.ast.version == 6
    declaration, = (
        node for node in context.ast.body
        if isinstance(node, VarDecl) and node.name == "observed"
    )
    # Unknown integer values require the proof to stay conservative even when
    # a particular emitter already produces a floating NaN for that source.
    assert generator._int_operand_may_be_na(declaration.value) is True


@pytest.mark.parametrize("body", [
    _MUTABLE_INPUT_INTEGER_SOURCE,
    _PERSISTENT_INTEGER_SOURCE,
    _UNPROVED_TIME_INPUT_SOURCE,
])
def test_unproved_integer_sources_keep_the_emitted_arithmetic_guard(body):
    cpp = transpile(
        '//@version=6\nstrategy("Guarded integer division")\n'
        + body + "float quotient = observed / 2\n",
    )
    # Division consumes the original int or int64_t before converting to double.
    # A missing operand must select na before the division, evaluated once.
    assert f"quotient = {integer_arithmetic('observed', '/', '2')};" in cpp


_NATIVE_FLOAT_WRITES = (
    "acceptValue(float item) => item\n"
    "returnValue({source_type} item) => item\n"
    "shadowValue({source_type} item) =>\n"
    "    float shared = na\n"
    "    shared := item\n"
    "    shared\n"
    "method storeValue(array<float> items, float item) =>\n"
    "    items.set(0, item)\n"
    "    items.get(0)\n"
    "var int shared = 19\n"
    "{declaration}\n"
    "if close > open\n"
    "    sourceValue := {missing}\n"
    "float declaredValue = sourceValue\n"
    "float assignedValue = na\n"
    "assignedValue := sourceValue\n"
    "var float retainedValue = na\n"
    "retainedValue := sourceValue\n"
    "float branchValue = 37.25\n"
    "if high > open\n"
    "    branchValue := sourceValue\n"
    "float selectedValue = if high > open\n"
    "    sourceValue\n"
    "else\n"
    "    37.25\n"
    "float argumentValue = acceptValue(sourceValue)\n"
    "float returnedValue = returnValue(sourceValue)\n"
    "float shadowedValue = shadowValue(sourceValue)\n"
    "items = array.new<float>(1, 37.25)\n"
    "float byrefValue = items.storeValue(sourceValue)\n"
    "float loopValue = na\n"
    "for step = 0 to 1\n"
    "    loopValue := sourceValue\n"
    "float identityValue = (sourceValue + 0) * 1\n"
    "float quotientValue = sourceValue / 2\n"
    "bool missingValue = na(assignedValue)\n"
    "float fallbackValue = nz(assignedValue, 37.25)\n"
    "bool comparisonValue = assignedValue < 5\n"
    "plot(declaredValue + assignedValue + retainedValue + branchValue + selectedValue)\n"
    "plot(argumentValue + returnedValue + shadowedValue + byrefValue + loopValue)\n"
    "plot(identityValue + quotientValue + fallbackValue)\n"
)


_NATIVE_FLOAT_WRITES_DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    // Each strategy consumes a different bar. The close selects missingness
    // in Pine, and supplies the finite value for the int32/float cases.
    const double closes[] = {CLOSE_INPUTS};
    for (unsigned index = 0; index < sizeof(closes) / sizeof(closes[0]); ++index) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.sourceValue), SOURCE_CPP_TYPE>);
        static_assert(std::is_same_v<decltype(strategy.declaredValue), double>);
        static_assert(std::is_same_v<decltype(strategy.assignedValue), double>);
        static_assert(std::is_same_v<decltype(strategy.retainedValue), double>);
        static_assert(std::is_same_v<decltype(strategy.branchValue), double>);
        static_assert(std::is_same_v<decltype(strategy.selectedValue), double>);
        static_assert(std::is_same_v<decltype(strategy.argumentValue), double>);
        static_assert(std::is_same_v<decltype(strategy.returnedValue), double>);
        static_assert(std::is_same_v<decltype(strategy.shadowedValue), double>);
        static_assert(std::is_same_v<decltype(strategy.byrefValue), double>);
        static_assert(std::is_same_v<decltype(strategy.loopValue), double>);
        pineforge::Bar bar{
            100.0, 102.0, closes[index] - 1.0, closes[index], 10.0,
            1704067200000LL + index * 60000LL,
        };
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) return 1;
        const bool missing = bar.close > bar.open;
        const double expected = EXPECTED_INPUT;
        const double values[] = {
            strategy.declaredValue, strategy.assignedValue,
            strategy.retainedValue, strategy.branchValue,
            strategy.selectedValue, strategy.argumentValue,
            strategy.returnedValue, strategy.shadowedValue,
            strategy.byrefValue, strategy.loopValue, strategy.identityValue,
        };
        const char* names[] = {
            "declaration", "assignment", "var assignment", "branch", "if expression",
            "float argument", "return", "shadowed local", "collection method",
            "loop", "arithmetic",
        };
        for (unsigned slot = 0; slot < sizeof(values) / sizeof(values[0]); ++slot) {
            if (missing ? !std::isnan(values[slot]) : values[slot] != expected) {
                std::printf("%s input=%u missing=%d expected=%.17g actual=%.17g\n",
                            names[slot], index, missing, expected, values[slot]);
                return 2;
            }
        }
        if (missing ? !std::isnan(strategy.quotientValue)
                    : strategy.quotientValue != expected / 2.0) return 3;
        if (strategy.missingValue != missing) return 4;
        if (strategy.fallbackValue != (missing ? 37.25 : expected)) return 5;
        if (strategy.comparisonValue != (!missing && expected < 5.0)) return 6;
    }
    std::puts("ok");
}
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("declaration,source_type,source_cpp_type,missing,expected", [
    pytest.param(
        # int(<float series>) is a 64-bit int (the row was "int32" while it was 32-bit).
        "int sourceValue = int(close)", "int", "int64_t", "int(na)", "bar.close",
        id="int-of-close",
    ),
    pytest.param(
        "int sourceValue = time", "int", "int64_t", "int(na)",
        "static_cast<double>(bar.timestamp)", id="int64",
    ),
    pytest.param(
        "float sourceValue = close", "float", "double", "na", "bar.close",
        id="float-control",
    ),
])
def test_native_float_write_controls_consume_finite_and_missing_inputs(
    declaration, source_type, source_cpp_type, missing, expected, opt,
):
    """Native witness only; TradingView evidence for these new sources: NONE."""
    from tests._compile import run_emitted_tu

    source = (
        '//@version=6\nstrategy("Input-sensitive numeric writes")\n'
        + _NATIVE_FLOAT_WRITES.format(
            declaration=declaration, source_type=source_type, missing=missing,
        )
    )
    driver = _NATIVE_FLOAT_WRITES_DRIVER.replace(
        "SOURCE_CPP_TYPE", source_cpp_type,
    ).replace("EXPECTED_INPUT", expected).replace(
        "CLOSE_INPUTS",
        # Float values equal to either integer sentinel must stay finite.
        # Fractional input also detects accidental integer storage.
        "-7.0, 0.0, 13.0, 0.375, -2147483648.0, -9223372036854775808.0, 101.0"
        if source_cpp_type == "double" else "-7.0, 0.0, 13.0, 101.0",
    )
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="input-sensitive-float-writes",
    ) == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_finite_integer_arithmetic_result_can_equal_int32_missing_encoding(
    tmp_path, opt,
):
    """Unrun regression hypothesis; TradingView evidence for this source: NONE."""
    from tests._compile import run_emitted_tu

    source = (
        '//@version=6\nstrategy("Finite integer arithmetic boundary")\n'
        "int sourceValue = int(close)\n"
        "float promotedControl = float(sourceValue) - 1.0\n"
        "float observedValue = sourceValue - 1\n"
        "plot(promotedControl)\nplot(observedValue)\n"
    )
    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    // Every source value is finite and distinct from the int32 na encoding.
    // The last subtraction reaches that encoding without integer overflow.
    const double closes[] = {-7.0, 0.0, 13.0, -2147483647.0};
    for (unsigned index = 0; index < sizeof(closes) / sizeof(closes[0]); ++index) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.sourceValue), SOURCE_CPP_TYPE>);
        static_assert(std::is_same_v<decltype(strategy.promotedControl), double>);
        static_assert(std::is_same_v<decltype(strategy.observedValue), double>);
        const double close = closes[index];
        pineforge::Bar bar{
            close, close + 1.0, close - 1.0, close, 10.0,
            1704067200000LL + index * 60000LL,
        };
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) return 1;
        if (pineforge::is_na(strategy.sourceValue)
                || static_cast<double>(strategy.sourceValue) != close) return 2;
        const double expected = close - 1.0;
        if (strategy.promotedControl != expected) {
            std::printf("control input=%.17g expected=%.17g actual=%.17g\n",
                        close, expected, strategy.promotedControl);
            return 3;
        }
        if (std::isnan(strategy.observedValue)
                || strategy.observedValue != expected) {
            std::printf("subtraction input=%.17g expected=%.17g actual=%.17g\n",
                        close, expected, strategy.observedValue);
            return 4;
        }
    }
    std::puts("ok");
}
"""
    # Compare the same consumed bars and independent oracle with the exact
    # main base. A base failure does not establish a candidate regression.
    base_commit = "c12c1877da48629b9c711f745a752ffc20ceef17"
    base = reference_codegen(base_commit)
    assert base is not None, f"Reference {base_commit} unavailable."
    pine = tmp_path / "finite_integer_arithmetic.pine"
    pine.write_text(source, encoding="utf-8")
    previous = transpile_json(pine, base)
    assert previous.get("ok"), previous
    # The same oracle for both: the reference's int(close) is a 32-bit int, the
    # current one is a 64-bit int (Pine's int is 64-bit).
    assert run_emitted_tu(
        previous["cpp"], driver.replace("SOURCE_CPP_TYPE", "int"), opt=opt,
        label="base-finite-integer-arithmetic",
    ) == "ok\n"
    assert run_emitted_tu(
        transpile(source), driver.replace("SOURCE_CPP_TYPE", "int64_t"), opt=opt,
        label="current-finite-integer-arithmetic",
    ) == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("declaration,cpp_type,source_input", [
    # int(<float series>) is a 64-bit int, like the epoch below it.
    ("int sourceValue = int(close)", "int64_t", "static_cast<int64_t>(bar.close)"),
    ("int sourceValue = time", "int64_t", "bar.timestamp"),
])
def test_native_nullable_arithmetic_keeps_semantic_width_and_single_evaluation(
    declaration, cpp_type, source_input, opt,
):
    """Native expectations only; TradingView evidence for these sources: NONE."""
    from tests._compile import run_emitted_tu

    source = (
        '//@version=6\nstrategy("Nullable arithmetic result families")\n'
        "acceptFloat(float item) => item\n"
        "subtractToFloat(int item) =>\n"
        "    float result = item - 1\n"
        "    result\n"
        "countRead(array<int> calls, int item) =>\n"
        "    calls.set(0, calls.get(0) + 1)\n"
        "    item\n"
        + declaration + "\n"
        "if close > open\n"
        "    sourceValue := int(na)\n"
        "float subtractionValue = sourceValue - 1\n"
        "float additionValue = sourceValue + (-1)\n"
        "float productValue = sourceValue * 2\n"
        "float nestedValue = (sourceValue - 1) + 1\n"
        "float negatedValue = -(sourceValue - 1)\n"
        "float quotientValue = (sourceValue - 1) / 2\n"
        "float precisionValue = (sourceValue + 2) - sourceValue\n"
        "float argumentValue = acceptFloat(sourceValue - 1)\n"
        "float helperValue = subtractToFloat(sourceValue)\n"
        "float branchValue = 37.25\n"
        "if high > open\n"
        "    branchValue := sourceValue - 1\n"
        "float selectedValue = if high > open\n"
        "    sourceValue - 1\n"
        "else\n"
        "    37.25\n"
        "float loopValue = 37.25\n"
        "for step = 0 to 1\n"
        "    loopValue := sourceValue - 1\n"
        "calls = array.new<int>(1, 0)\n"
        "float onceValue = countRead(calls, sourceValue) - countRead(calls, 1)\n"
        "int callCount = calls.get(0)\n"
        "plot(subtractionValue + additionValue + productValue + nestedValue)\n"
        "plot(negatedValue + quotientValue + precisionValue + argumentValue)\n"
        "plot(helperValue + branchValue + selectedValue + loopValue + onceValue)\n"
        "plot(callCount)\n"
    )
    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    const double closes[] = {-7.0, 0.0, 13.0, -1073741824.0, -2147483647.0, 101.0};
    const int64_t stamps[] = {
        1704067200000LL, 9007199254740993LL, 9007199254740995LL,
        -1073741824LL, -2147483647LL, 1704067500000LL,
    };
    for (unsigned index = 0; index < sizeof(closes) / sizeof(closes[0]); ++index) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.sourceValue), SOURCE_CPP_TYPE>);
        pineforge::Bar bar{
            100.0, 102.0, closes[index] - 1.0, closes[index], 10.0, stamps[index],
        };
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) return 1;
        const bool missing = bar.close > bar.open;
        const int64_t source = SOURCE_INPUT;
        // Compute the oracle in integers before converting to double. In
        // particular, (source + 2) - source must retain its low bits.
        const int64_t difference = source - 1;
        const double values[] = {
            strategy.subtractionValue, strategy.additionValue,
            strategy.productValue, strategy.nestedValue, strategy.negatedValue,
            strategy.quotientValue, strategy.precisionValue, strategy.argumentValue,
            strategy.helperValue, strategy.branchValue, strategy.selectedValue,
            strategy.loopValue, strategy.onceValue,
        };
        const double expected[] = {
            static_cast<double>(difference), static_cast<double>(difference),
            static_cast<double>(source * 2), static_cast<double>(source),
            static_cast<double>(-difference), static_cast<double>(difference) / 2.0,
            2.0, static_cast<double>(difference), static_cast<double>(difference),
            static_cast<double>(difference), static_cast<double>(difference),
            static_cast<double>(difference), static_cast<double>(difference),
        };
        for (unsigned slot = 0; slot < sizeof(values) / sizeof(values[0]); ++slot) {
            if (missing ? !std::isnan(values[slot]) : values[slot] != expected[slot]) {
                std::printf("input=%u slot=%u missing=%d expected=%.17g actual=%.17g\n",
                            index, slot, missing, expected[slot], values[slot]);
                return 2;
            }
        }
        // Both operands must run once even when the left operand is missing.
        if (strategy.callCount != 2) return 3;
    }
    std::puts("ok");
}
"""
    driver = driver.replace("SOURCE_CPP_TYPE", cpp_type).replace(
        "SOURCE_INPUT", source_input,
    )
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="nullable-arithmetic-result-family",
    ) == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_lower_timeframe_arithmetic_converts_the_result_after_operand_checks(opt):
    """Exercise the vector consumer; TradingView evidence for this source: NONE."""
    from tests._compile import run_emitted_tu

    source = (
        '//@version=6\nstrategy("Requested arithmetic result conversion")\n'
        'missingValues = request.security_lower_tf(syminfo.tickerid, "1", int(na) + 0)\n'
        'values = request.security_lower_tf(syminfo.tickerid, "1", '
        '(close > open ? int(na) : int(close)) - 1)\n'
        "plot(array.size(missingValues) + array.size(values))\n"
    )
    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>
#include <vector>

int main() {
    const double closes[] = {-7.0, 0.0, 13.0, -2147483647.0, 101.0};
    for (unsigned index = 0; index < sizeof(closes) / sizeof(closes[0]); ++index) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy._req_sec_lower_tf_0),
                                     std::vector<double>>);
        static_assert(std::is_same_v<decltype(strategy._req_sec_lower_tf_1),
                                     std::vector<double>>);
        pineforge::Bar requested{
            100.0, 102.0, closes[index] - 1.0, closes[index], 10.0,
            1704067200000LL + index * 60000LL,
        };
        // Call the compiled evaluators with the actual requested bar, without
        // involving sub-bar synthesis in this element-conversion contract.
        strategy.configure_security_evaluators();
        strategy.evaluate_security(0, requested, true);
        strategy.evaluate_security(1, requested, true);
        if (!strategy.last_error().empty()) return 1;
        if (strategy._req_sec_lower_tf_0.size() != 1
                || strategy._req_sec_lower_tf_1.size() != 1) return 2;
        if (!std::isnan(strategy._req_sec_lower_tf_0[0])) return 3;
        const bool missing = requested.close > requested.open;
        const double expected = requested.close - 1.0;
        const double actual = strategy._req_sec_lower_tf_1[0];
        if (missing ? !std::isnan(actual)
                    : !std::isfinite(actual) || actual != expected) {
            std::printf("input=%u missing=%d expected=%.17g actual=%.17g\n",
                        index, missing, expected, actual);
            return 4;
        }
    }
    std::puts("ok");
}
"""
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="lower-timeframe-result-conversion",
    ) == "ok\n"

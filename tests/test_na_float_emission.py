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
    assert current["cpp"] == previous["cpp"]


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

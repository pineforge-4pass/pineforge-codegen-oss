"""Pine v6 refuses additional bool, na and proven type mismatches."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("Boolean type checks", overlay = true)\n'


def _source(body: str) -> str:
    return HEADER + body


def _assert_refusal(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    line: int,
    col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="bool_types.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == arguments
    assert diagnostic.location.file == "bool_types.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


REFUSALS = [
    pytest.param(
        _source(
            "bool flag = true\n"
            "value = flag and 1\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2066",
        "Operator 'and' cannot combine bool and int.",
        # node.op, left.value and right.value supply these three arguments.
        {"op": "and", "left": "bool", "right": "int"},
        4,
        18,  # The numeric right operand starts after "value = flag and ".
        id="logical-number",
    ),
    pytest.param(
        _source(
            "array<bool> flags = array.new<bool>(1, true)\n"
            "array.push(flags, na)\n"
            "plot(array.get(flags, 0) ? 1 : 0)\n"
        ),
        "PF-E2067",
        "Cannot store a value of the 'na' type in array<bool>.",
        # spec.kind is array for the declared bool array.
        {"kind": "array"},
        4,
        19,  # The bare na starts after "array.push(flags, ".
        id="bool-array-push",
    ),
    pytest.param(
        _source(
            "array<bool> flags = array.new<bool>(1, true)\n"
            "array.set(flags, 0, na)\n"
            "plot(array.get(flags, 0) ? 1 : 0)\n"
        ),
        "PF-E2067",
        "Cannot store a value of the 'na' type in array<bool>.",
        # spec.kind is array for the declared bool array.
        {"kind": "array"},
        4,
        21,  # The bare na starts after "array.set(flags, 0, ".
        id="bool-array-set",
    ),
    pytest.param(
        _source(
            "pick(bool selected) =>\n"
            "    if selected\n"
            "        true\n"
            "    else\n"
            "        na\n"
            "bool value = pick(true)\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2068",
        "A bool result cannot be na in an if or switch expression in Pine v6.",
        {},
        7,
        9,  # Eight spaces indent the returned bare na to column 9.
        id="bool-function-result",
    ),
    pytest.param(
        _source(
            "type Flag\n"
            "    bool value\n"
            "Flag obj = Flag.new(na)\n"
            "plot(obj.value ? 1 : 0)\n"
        ),
        "PF-E2069",
        "Cannot call 'Flag.new' with a bare na value for bool field 'value'.",
        # The UDT name is Flag; ".new" is literal template text.
        {"udt_name": "Flag", "field_name": "value"},
        5,
        21,  # The constructor argument starts after "Flag.new(".
        id="bool-field-constructor",
    ),
    pytest.param(
        _source(
            "type Flag\n"
            "    bool value\n"
            "Flag obj = Flag.new(true)\n"
            "obj.value := na\n"
            "plot(obj.value ? 1 : 0)\n"
        ),
        "PF-E2070",
        "Cannot assign a value of the 'na' type to the 'value' variable. "
        "The variable is declared with the 'bool' type.",
        # Only node.target.member varies; na and bool are literal text.
        {"member": "value"},
        6,
        14,  # The bare na starts after "obj.value := ".
        id="bool-field-assignment",
    ),
    pytest.param(
        _source(
            "bool value = if true\n"
            "    true\n"
            "else\n"
            "    na\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2068",
        "A bool result cannot be na in an if or switch expression in Pine v6.",
        {},
        6,
        5,  # Four spaces indent the if expression's bare na to column 5.
        id="bool-if-result",
    ),
    pytest.param(
        _source(
            "matrix<bool> flags = matrix.new<bool>(1, 1, true)\n"
            "matrix.set(flags, 0, 0, na)\n"
            "plot(matrix.get(flags, 0, 0) ? 1 : 0)\n"
        ),
        "PF-E2067",
        "Cannot store a value of the 'na' type in matrix<bool>.",
        # spec.kind is matrix for the declared bool matrix.
        {"kind": "matrix"},
        4,
        25,  # The bare na starts after "matrix.set(flags, 0, 0, ".
        id="bool-matrix-set",
    ),
    pytest.param(
        _source(
            "value = true + 1\n"
            "plot(value)\n"
        ),
        "PF-E2066",
        "Operator '+' cannot combine bool and int.",
        # Addition reads bool from the left operand and int from the right.
        {"op": "+", "left": "bool", "right": "int"},
        3,
        9,  # The bool operand starts after "value = ".
        id="bool-number-addition",
    ),
    pytest.param(
        _source(
            "bool value = switch\n"
            "    true => false\n"
            "    => na\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2068",
        "A bool result cannot be na in an if or switch expression in Pine v6.",
        {},
        5,
        8,  # The default arm's na starts after four spaces and "=> ".
        id="bool-switch-result",
    ),
    pytest.param(
        _source(
            "bool flag = true\n"
            "value = fixnan(flag)\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2071",
        "Cannot call 'fixnan' with a bool argument: its source must be a numeric or color series.",
        {"callee_name": "fixnan"},
        4,
        16,  # The bool argument starts after "value = fixnan(".
        id="fixnan-bool",
    ),
    pytest.param(
        _source(
            "add(int value) => value + 1\n"
            "result = add(1.5)\n"
            "plot(result)\n"
        ),
        "PF-E2073",
        "Cannot call 'add' with a float argument for int parameter 'value'.",
        # function_name is add and the declared parameter name is value.
        {"function_name": "add", "name": "value"},
        4,
        14,  # The float argument starts after "result = add(".
        id="int-parameter-float",
    ),
    pytest.param(
        _source(
            "bool flag = true\n"
            "value = na(flag)\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2072",
        "Cannot call 'na' with a bool argument: Pine v6 bool values cannot be na.",
        {},
        4,
        12,  # The bool argument starts after "value = na(".
        id="na-bool",
    ),
    pytest.param(
        _source(
            "bool flag = true\n"
            "value = nz(flag)\n"
            "plot(value ? 1 : 0)\n"
        ),
        "PF-E2071",
        "Cannot call 'nz' with a bool argument: its source must be a numeric or color series.",
        {"callee_name": "nz"},
        4,
        12,  # The bool argument starts after "value = nz(".
        id="nz-bool",
    ),
    pytest.param(
        _source(
            'string text = "a" + 1\n'
            "plot(str.length(text))\n"
        ),
        "PF-E2066",
        "Operator '+' cannot combine string and int.",
        # Addition reads string from the left operand and int from the right.
        {"op": "+", "left": "string", "right": "int"},
        3,
        21,  # The numeric operand starts after the string and plus sign.
        id="string-number-addition",
    ),
    pytest.param(
        _source(
            "bool flag = true\n"
            "float value = na(flag) ? 2.0 : 1.0\n"
            "plot(value)\n"
        ),
        "PF-E2072",
        "Cannot call 'na' with a bool argument: Pine v6 bool values cannot be na.",
        {},
        4,
        18,  # The bool argument starts after "float value = na(".
        id="na-bool-selection",
    ),
]


@pytest.mark.parametrize(
    "source,code,message,arguments,line,col",
    REFUSALS,
)
def test_additional_bool_and_type_shapes_are_located_refusals(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    line: int,
    col: int,
) -> None:
    _assert_refusal(source, code, message, arguments, line, col)


CONTROLS = [
    pytest.param(
        _source("bool flag = true\nvalue = flag and false\nplot(value ? 1 : 0)\n"),
        id="logical-bool-control",
    ),
    pytest.param(
        _source("bool first = true\nbool second = false\nvalue = first and second\nplot(value ? 1 : 0)\n"),
        id="logical-two-bools-control",
    ),
    pytest.param(
        _source(
            "array<bool> flags = array.new<bool>(1, true)\n"
            "array.push(flags, false)\n"
            "plot(array.get(flags, 1) ? 1 : 0)\n"
        ),
        id="bool-array-push-bool-control",
    ),
    pytest.param(
        _source(
            "array<float> values = array.new<float>(1, 1.0)\n"
            "array.push(values, na)\n"
            "plot(nz(array.get(values, 1)))\n"
        ),
        id="float-array-push-na-control",
    ),
    pytest.param(
        _source(
            "array<bool> flags = array.new<bool>(1, true)\n"
            "array.set(flags, 0, false)\n"
            "plot(array.get(flags, 0) ? 1 : 0)\n"
        ),
        id="bool-array-set-bool-control",
    ),
    pytest.param(
        _source(
            "array<float> values = array.new<float>(1, 1.0)\n"
            "array.set(values, 0, na)\n"
            "plot(nz(array.get(values, 0)))\n"
        ),
        id="float-array-set-na-control",
    ),
    pytest.param(
        _source(
            "pick(bool selected) =>\n"
            "    if selected\n"
            "        true\n"
            "    else\n"
            "        false\n"
            "bool value = pick(true)\n"
            "plot(value ? 1 : 0)\n"
        ),
        id="bool-function-bool-arms-control",
    ),
    pytest.param(
        _source(
            "pick(bool selected) => selected ? true : false\n"
            "bool value = pick(true)\n"
            "plot(value ? 1 : 0)\n"
        ),
        id="bool-ternary-bool-arms-control",
    ),
    pytest.param(
        _source(
            "type Flag\n"
            "    bool value\n"
            "Flag obj = Flag.new(true)\n"
            "plot(obj.value ? 1 : 0)\n"
        ),
        id="bool-field-bool-constructor-control",
    ),
    pytest.param(
        _source(
            "type Value\n"
            "    float value\n"
            "Value obj = Value.new(na)\n"
            "plot(nz(obj.value))\n"
        ),
        id="float-field-na-constructor-control",
    ),
    pytest.param(
        _source(
            "type Flag\n"
            "    bool value\n"
            "Flag obj = Flag.new(true)\n"
            "obj.value := false\n"
            "plot(obj.value ? 1 : 0)\n"
        ),
        id="bool-field-bool-assignment-control",
    ),
    pytest.param(
        _source(
            "type Value\n"
            "    float value\n"
            "Value obj = Value.new(1.0)\n"
            "obj.value := na\n"
            "plot(nz(obj.value))\n"
        ),
        id="float-field-na-assignment-control",
    ),
    pytest.param(
        _source(
            "bool value = if true\n"
            "    true\n"
            "else\n"
            "    false\n"
            "plot(value ? 1 : 0)\n"
        ),
        id="bool-if-bool-arms-control",
    ),
    pytest.param(
        _source(
            "float value = if true\n"
            "    1.0\n"
            "else\n"
            "    na\n"
            "plot(nz(value))\n"
        ),
        id="float-if-na-control",
    ),
    pytest.param(
        _source(
            "matrix<bool> flags = matrix.new<bool>(1, 1, true)\n"
            "matrix.set(flags, 0, 0, false)\n"
            "plot(matrix.get(flags, 0, 0) ? 1 : 0)\n"
        ),
        id="bool-matrix-bool-control",
    ),
    pytest.param(
        _source(
            "matrix<float> values = matrix.new<float>(1, 1, 1.0)\n"
            "matrix.set(values, 0, 0, na)\n"
            "plot(nz(matrix.get(values, 0, 0)))\n"
        ),
        id="float-matrix-na-control",
    ),
    pytest.param(
        _source("value = 1 + 2\nplot(value)\n"),
        id="numeric-addition-control",
    ),
    pytest.param(
        _source('string text = "a" + "b"\nplot(str.length(text))\n'),
        id="string-addition-control",
    ),
    pytest.param(
        _source(
            "bool value = switch\n"
            "    true => false\n"
            "    => true\n"
            "plot(value ? 1 : 0)\n"
        ),
        id="bool-switch-bool-arms-control",
    ),
    pytest.param(
        _source(
            "float value = switch\n"
            "    true => 1.0\n"
            "    => na\n"
            "plot(nz(value))\n"
        ),
        id="float-switch-na-control",
    ),
    pytest.param(
        _source("value = fixnan(close)\nplot(value)\n"),
        id="fixnan-price-control",
    ),
    pytest.param(
        _source("value = fixnan(1.0)\nplot(value)\n"),
        id="fixnan-literal-control",
    ),
    pytest.param(
        _source(
            "add(int value) => value + 1\n"
            "result = add(1)\n"
            "plot(result)\n"
        ),
        id="int-parameter-int-control",
    ),
    pytest.param(
        _source(
            "add(int value) => value + 1\n"
            "result = add(int(1.5))\n"
            "plot(result)\n"
        ),
        id="int-parameter-cast-control",
    ),
    pytest.param(
        _source("value = na(close)\nplot(value ? 1 : 0)\n"),
        id="na-price-control",
    ),
    pytest.param(
        _source("value = na(1.0)\nplot(value ? 1 : 0)\n"),
        id="na-literal-control",
    ),
    pytest.param(
        _source("value = nz(close)\nplot(value)\n"),
        id="nz-price-control",
    ),
    pytest.param(
        _source("value = nz(1.0)\nplot(value)\n"),
        id="nz-literal-control",
    ),
    pytest.param(
        _source('string text = "a" + "b"\nplot(str.length(text))\n'),
        id="string-string-control",
    ),
    pytest.param(
        _source('string text = str.tostring(1) + "b"\nplot(str.length(text))\n'),
        id="string-call-control",
    ),
    pytest.param(
        _source("float value = na(close) ? 2.0 : 1.0\nplot(value)\n"),
        id="numeric-na-selection-control",
    ),
    pytest.param(
        _source("float value = na(1.0) ? 2.0 : 1.0\nplot(value)\n"),
        id="numeric-na-literal-selection-control",
    ),
]


@pytest.mark.parametrize("source", CONTROLS)
def test_nearest_accepted_shapes_still_compile(source: str) -> None:
    cpp = transpile(source)
    compile_cpp(cpp, label="bool and type control")


LEGACY_CASES = [
    pytest.param(
        "export check(bool flag) => flag and 1\n",
        "value = legacy.check(true)\nplot(value)\n",
        id="logical-number-legacy",
    ),
    pytest.param(
        "export check() =>\n"
        "    array<bool> flags = array.new<bool>(1, true)\n"
        "    array.push(flags, na)\n"
        "    array.get(flags, 1)\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-array-push-legacy",
    ),
    pytest.param(
        "export check() =>\n"
        "    array<bool> flags = array.new<bool>(1, true)\n"
        "    array.set(flags, 0, na)\n"
        "    array.get(flags, 0)\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-array-set-legacy",
    ),
    pytest.param(
        "export check(bool selected) =>\n"
        "    if selected\n"
        "        true\n"
        "    else\n"
        "        na\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        id="bool-function-result-legacy",
    ),
    pytest.param(
        "type Flag\n"
        "    bool value\n"
        "export check() =>\n"
        "    Flag obj = Flag.new(na)\n"
        "    obj.value\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-field-constructor-legacy",
    ),
    pytest.param(
        "type Flag\n"
        "    bool value\n"
        "export check() =>\n"
        "    Flag obj = Flag.new(true)\n"
        "    obj.value := na\n"
        "    obj.value\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-field-assignment-legacy",
    ),
    pytest.param(
        "export check(bool selected) =>\n"
        "    if selected\n"
        "        true\n"
        "    else\n"
        "        na\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        id="bool-if-result-legacy",
    ),
    pytest.param(
        "export check() =>\n"
        "    matrix<bool> flags = matrix.new<bool>(1, 1, true)\n"
        "    matrix.set(flags, 0, 0, na)\n"
        "    matrix.get(flags, 0, 0)\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-matrix-legacy",
    ),
    pytest.param(
        "export check() => true + 1\n",
        "value = legacy.check()\nplot(value)\n",
        id="bool-number-addition-legacy",
    ),
    pytest.param(
        "export check() =>\n"
        "    switch\n"
        "        true => false\n"
        "        => na\n",
        "value = legacy.check()\nplot(value ? 1 : 0)\n",
        id="bool-switch-result-legacy",
    ),
    pytest.param(
        "export check(int value) => value + 1\n",
        "result = legacy.check(1.5)\nplot(result)\n",
        id="int-parameter-float-legacy",
    ),
    pytest.param(
        'export check() => "a" + 1\n',
        "value = legacy.check()\nplot(str.length(value))\n",
        id="string-number-addition-legacy",
    ),
]


@pytest.mark.parametrize("library_body,call_body", LEGACY_CASES)
def test_imported_v5_definitions_keep_their_lowering(
    library_body: str, call_body: str,
) -> None:
    source = _source(
        "import local/Legacy/1 as legacy\n"
        + call_body
    )
    library = '//@version=5\nlibrary("Legacy")\n' + library_body
    assert transpile(source, libraries={"local/Legacy/1": library})


LEGACY_REFUSALS = [
    pytest.param(
        # Hand-derived: the existing v5 fixnan observer gate is PF-E5046 at 3:34.
        "export check(bool flag) => fixnan(flag)\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        "fixnan",
        "library 'local/Legacy/1' is //@version=5: fixnan() of a bool reads "
        "v5's third bool state (na), which PineForge's two-state bool does not keep",
        3,
        34,
        35,  # The call's opening parenthesis is column 34.
        id="fixnan-bool-legacy",
    ),
    pytest.param(
        # Hand-derived: the existing v5 na observer gate is PF-E5046 at 3:30.
        "export check(bool flag) => na(flag)\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        "na",
        "library 'local/Legacy/1' is //@version=5: na() of a bool reads "
        "v5's third bool state (na), which PineForge's two-state bool does not keep",
        3,
        30,
        31,  # The na call's opening parenthesis is column 30.
        id="na-bool-legacy",
    ),
    pytest.param(
        # Hand-derived: the existing v5 nz observer gate is PF-E5046 at 3:30.
        "export check(bool flag) => nz(flag)\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        "nz",
        "library 'local/Legacy/1' is //@version=5: nz() of a bool reads "
        "v5's third bool state (na), which PineForge's two-state bool does not keep",
        3,
        30,
        31,  # The nz call's opening parenthesis is column 30.
        id="nz-bool-legacy",
    ),
    pytest.param(
        # Hand-derived: this second na observer row has the same PF-E5046 span.
        "export check(bool flag) => na(flag)\n",
        "value = legacy.check(true)\nplot(value ? 1 : 0)\n",
        "na",
        "library 'local/Legacy/1' is //@version=5: na() of a bool reads "
        "v5's third bool state (na), which PineForge's two-state bool does not keep",
        3,
        30,
        31,  # The selection row uses the same observer call location.
        id="na-bool-selection-legacy",
    ),
]


@pytest.mark.parametrize(
    "library_body,call_body,function_name,message,line,col,end_col",
    LEGACY_REFUSALS,
)
def test_imported_v5_bool_observers_keep_the_existing_refusal(
    library_body: str,
    call_body: str,
    function_name: str,
    message: str,
    line: int,
    col: int,
    end_col: int,
) -> None:
    source = _source(
        "import local/Legacy/1 as legacy\n"
        + call_body
    )
    library = '//@version=5\nlibrary("Legacy")\n' + library_body
    with pytest.raises(CompileError) as caught:
        transpile(source, libraries={"local/Legacy/1": library})
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.code == "PF-E5046"
    assert diagnostic.message == message
    assert f"{function_name}()" in diagnostic.message
    assert diagnostic.location.file == "local/Legacy/1"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    assert diagnostic.location.end_col == end_col

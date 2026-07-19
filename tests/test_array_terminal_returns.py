"""Exact primitive UDF return typing for direct terminal array ``get`` calls."""

from __future__ import annotations

from itertools import product

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


_GET_SHAPES_SOURCE = r'''//@version=6
strategy("Terminal array get shapes")

method_positional() =>
    array<string> values = array.from("method-positional")
    values.get(0)

method_keyword(array<string> values) =>
    values.get(index=0)

functional_positional() =>
    array<string> values = array.from("functional-positional")
    array.get(values, 0)

functional_mixed(array<string> values) =>
    array.get(values, index=0)

functional_keyword() =>
    array<string> values = array.from("functional-keyword")
    array.get(id=values, index=0)

shadowed_namespace() =>
    array<string> array = array.from("shadowed")
    array.get(0)

parameter_values = array.from("parameter")
a = method_positional()
b = method_keyword(parameter_values)
c = functional_positional()
d = functional_mixed(parameter_values)
e = functional_keyword()
f = shadowed_namespace()
if bar_index == 0 and a == "method-positional" and b == "parameter" and c == "functional-positional" and d == "parameter" and e == "functional-keyword" and f == "shadowed"
    strategy.entry("L", strategy.long)
'''


_PRIMITIVE_TYPES_SOURCE = r'''//@version=6
strategy("Terminal array get primitive types")

get_int() =>
    array<int> values = array.from(1)
    values.get(0)

get_float() =>
    array<float> values = array.from(1.5)
    array.get(values, 0)

get_bool() =>
    array<bool> values = array.from(true)
    values.get(index=0)

get_string() =>
    array<string> values = array.from("x")
    array.get(id=values, index=0)

get_color() =>
    array<color> values = array.new<color>()
    values.push(color.red)
    values.get(0)

i = get_int()
f = get_float()
b = get_bool()
s = get_string()
c = get_color()
if bar_index == 0 and i == 1 and f == 1.5 and b and s == "x" and c == color.red
    strategy.entry("L", strategy.long)
'''


def _masking_matrix_source(
    *,
    functional: bool,
    parameter: bool,
    reuse_receiver_name: bool,
    string_first: bool,
) -> str:
    """Build one frozen representation/order cell around the same defect."""

    def function(kind: str) -> list[str]:
        is_string = kind == "string"
        func_name = f"read_{kind}"
        type_name = "string" if is_string else "int"
        literal = '"ok"' if is_string else "7"
        receiver = "values" if reuse_receiver_name else f"{kind}_values"
        if parameter:
            lines = [f"{func_name}(array<{type_name}> {receiver}) =>"]
        else:
            lines = [
                f"{func_name}() =>",
                f"    array<{type_name}> {receiver} = array.from({literal})",
            ]
        terminal = (
            f"array.get({receiver}, 0)"
            if functional
            else f"{receiver}.get(0)"
        )
        lines.append(f"    {terminal}")
        return lines

    ordered = ["string", "int"] if string_first else ["int", "string"]
    lines = [
        "//@version=6",
        'strategy("Terminal array get masking matrix")',
    ]
    for kind in ordered:
        lines.extend(function(kind))
    if parameter:
        lines.extend(
            [
                'string_inputs = array.from("ok")',
                "int_inputs = array.from(7)",
                "observed_string = read_string(string_inputs)",
                "observed_int = read_int(int_inputs)",
            ]
        )
    else:
        lines.extend(
            [
                "observed_string = read_string()",
                "observed_int = read_int()",
            ]
        )
    lines.extend(
        [
            'if bar_index == 0 and observed_string == "ok" and observed_int == 7',
            '    strategy.entry("L", strategy.long)',
        ]
    )
    return "\n".join(lines) + "\n"


def test_all_established_get_shapes_infer_string_and_compile():
    cpp = transpile(_GET_SHAPES_SOURCE)
    for name in (
        "method_positional",
        "method_keyword",
        "functional_positional",
        "functional_mixed",
        "functional_keyword",
        "shadowed_namespace",
    ):
        assert f"std::string {name}(" in cpp
    compile_cpp(cpp)


def test_all_primitive_element_types_emit_exact_udf_returns_and_compile():
    cpp = transpile(_PRIMITIVE_TYPES_SOURCE)
    for signature in (
        "int get_int(",
        "double get_float(",
        "bool get_bool(",
        "std::string get_string(",
        "int get_color(",
    ):
        assert signature in cpp
    compile_cpp(cpp)


def test_exact_return_spec_prevents_stateful_call_revisit_in_array_from():
    source = r'''//@version=6
strategy("Stateful terminal array get")
read_value() =>
    unused = ta.sma(close, 2)
    values = array.from("x")
    values.get(0)
wrapped = array.from(read_value())
if bar_index == 0 and wrapped.get(0) == "x"
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "read_value_cs1" not in cpp
    assert "_ta_sma_1_cs" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("functional", "parameter", "reuse_receiver_name", "string_first"),
    product((False, True), repeat=4),
)
def test_terminal_get_representation_and_order_matrix(
    functional: bool,
    parameter: bool,
    reuse_receiver_name: bool,
    string_first: bool,
):
    """Complete 2^4 input matrix; base/candidate is the fifth publish factor."""
    source = _masking_matrix_source(
        functional=functional,
        parameter=parameter,
        reuse_receiver_name=reuse_receiver_name,
        string_first=string_first,
    )
    cpp = transpile(source)
    assert "std::string read_string(" in cpp
    assert "int read_int(" in cpp
    compile_cpp(cpp)


def test_reference_like_array_elements_are_not_value_return_refined():
    source = r'''//@version=6
strategy("Terminal UDT array get remains identity-gated")
type Item
    int value
pick() =>
    array<Item> values = array.from(Item.new(1))
    values.get(0)
observed = pick()
'''
    cpp = transpile(source)
    assert "Item pick(" not in cpp
    assert "double pick(" in cpp


def test_scalar_array_binding_is_not_misclassified_as_builtin_namespace():
    source = r'''//@version=6
strategy("Array namespace shadow remains fail closed")
global_values = array.from("x")
blocked() =>
    int array = 1
    array.get(global_values, 0)
observed = blocked()
'''
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp
    assert "double blocked(" in cpp


def test_temporary_array_from_receiver_reuses_captured_type_without_new_clones():
    source = r'''//@version=6
strategy("Temporary receiver stays outside terminal get refinement")
producer() =>
    unused = ta.sma(close, 2)
    "x"
outer() =>
    array.get(array.from(producer()), 0)
observed = outer()
'''
    cpp = transpile(source)
    assert "std::string outer_cs0(" in cpp
    assert "double outer_cs0(" not in cpp
    assert "std::vector<std::string>{producer_cs0()}" in cpp
    # Reuse the terminal TypeSpec captured while ``outer``'s lexical scope is
    # active, and treat every analyzer revisit of this exact FuncCall node as
    # the same Pine textual call site. Real caller variants are cloned later
    # by call-path propagation; this one-call source needs exactly cs0.
    assert "producer_cs1" not in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("functional", "keyword", "producer_first"),
    product((False, True), repeat=3),
)
def test_stateful_temporary_receiver_shape_and_source_order_matrix(
    functional: bool,
    keyword: bool,
    producer_first: bool,
):
    """All 2^3 call-shape/order cells retain exact string typing."""
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    if functional:
        terminal = (
            "array.get(id=array.from(produce()), index=0)"
            if keyword
            else "array.get(array.from(produce()), 0)"
        )
    else:
        terminal = (
            "array.from(produce()).get(index=0)"
            if keyword
            else "array.from(produce()).get(0)"
        )
    reader = f"read_value() =>\n    {terminal}\n"
    definitions = (
        producer + reader if producer_first else reader + producer
    )
    source = (
        "//@version=6\n"
        'strategy("Temporary receiver source-order matrix")\n'
        f"{definitions}"
        "observed = read_value()\n"
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert "std::vector<std::string>{produce_cs" in cpp
    expected_clones = 1
    definitions = [
        index
        for index in range(8)
        if f"std::string produce_cs{index}(" in cpp
    ]
    assert definitions == list(range(expected_clones))
    assert f"std::vector<std::string>{{produce_cs{expected_clones - 1}()}}" in cpp
    assert sum(
        f"ta::SMA _ta_sma_1{'' if index == 0 else f'_cs{index}'};" in cpp
        for index in range(8)
    ) == expected_clones
    compile_cpp(cpp)


def test_nested_stateful_temporary_readers_isolate_two_call_paths():
    source = r'''//@version=6
strategy("Nested temporary reader isolation")
produce() =>
    unused = ta.sma(close, 2)
    "x"
middle() =>
    array.get(array.from(produce()), 0)
outer() =>
    array.get(array.from(middle()), 0)
first = outer()
second = outer()
'''
    cpp = transpile(source)
    for index in (0, 1):
        assert f"std::string produce_cs{index}(" in cpp
        assert f"std::string middle_cs{index}(" in cpp
        assert f"std::string outer_cs{index}(" in cpp
        assert f"std::vector<std::string>{{produce_cs{index}()}}" in cpp
        assert f"std::vector<std::string>{{middle_cs{index}()}}" in cpp
    assert "produce_cs2" not in cpp
    assert "middle_cs2" not in cpp
    assert "outer_cs2" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1") == 2
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "definitions",
    (
        '''reader() =>
    array.get(array.from(reader()), 0)
''',
        '''first_reader() =>
    array.get(array.from(second_reader()), 0)
second_reader() =>
    first_reader()
''',
        '''first_reader() =>
    array.get(array.from(second_reader()), 0)
second_reader() =>
    third_reader()
third_reader() =>
    first_reader()
''',
    ),
)
def test_temporary_reader_recursion_is_rejected(definitions: str):
    source = (
        "//@version=6\n"
        'strategy("Temporary reader recursion")\n'
        f"{definitions}"
        + (
            "observed = reader()\n"
            if definitions.startswith("reader")
            else "observed = first_reader()\n"
        )
    )
    with pytest.raises(
        CompileError,
        match="Recursive direct temporary-array reader cycle",
    ):
        transpile(source)


def test_forward_defaulted_parameter_is_not_registered_as_zero_parameter():
    source = r'''//@version=6
strategy("Forward default parameter boundary")
read_value() =>
    array.get(array.from(produce()), 0)
produce(string value = "x") =>
    value
observed = read_value()
'''
    with pytest.raises(CompileError, match="Unknown function 'produce"):
        transpile(source)


@pytest.mark.parametrize(
    "terminal",
    (
        "array.get(make_values(), 0)",
        "array.copy(make_values()).get(0)",
        "array.get(array.slice(values, 0, 1), 0)",
    ),
)
def test_arbitrary_temporary_array_receivers_remain_fail_closed(terminal: str):
    source = f'''//@version=6
strategy("Temporary receiver scope boundary")
values = array.from("x")
make_values() =>
    array.copy(values)
blocked() =>
    {terminal}
observed = blocked()
'''
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp
    assert "double blocked(" in cpp


def test_local_map_named_array_is_not_a_builtin_array_producer():
    source = r'''//@version=6
strategy("Array producer namespace shadow")
blocked() =>
    map<string, string> array = map.new<string, string>()
    map.put(array, "k", "x")
    array.copy().get("k")
observed = blocked()
'''
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp
    assert "double blocked(" in cpp


@pytest.mark.parametrize(
    "terminal",
    (
        "source.copy().get(0)",
        "array.copy(source).get(0)",
        "array.get(id=source.copy(), index=0)",
        "array.get(id=array.copy(source), index=0)",
    ),
)
def test_identifier_backed_copy_representations_return_exact_type(terminal: str):
    source = f'''//@version=6
strategy("Direct copy producer representations")
read_value() =>
    array<string> source = array.from("x")
    {terminal}
observed = read_value()
'''
    cpp = transpile(source)
    assert "std::string read_value(" in cpp
    assert "double read_value(" not in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("functional", "string", "copy_factory", "udf_element"),
    product((False, True), repeat=4),
)
def test_temporary_builtin_array_producer_matrix(
    functional: bool,
    string: bool,
    copy_factory: bool,
    udf_element: bool,
):
    """Complete 2^4 surface matrix around the two masked type gaps."""
    type_name = "string" if string else "int"
    literal = '"ok"' if string else "7"
    expected_cpp = "std::string" if string else "int"
    lines = [
        "//@version=6",
        'strategy("Temporary builtin array producer matrix")',
    ]
    if udf_element:
        lines.extend(
            [
                "produce() =>",
                "    unused = ta.sma(close, 2)",
                f"    {literal}",
            ]
        )
        element = "produce()"
    else:
        element = literal
    lines.append("read_value() =>")
    if copy_factory:
        lines.append(
            f"    array<{type_name}> source = array.from({element})"
        )
        receiver = "array.copy(source)"
    else:
        receiver = f"array.from({element})"
    terminal = (
        f"array.get({receiver}, 0)"
        if functional
        else f"{receiver}.get(0)"
    )
    lines.extend(
        [
            f"    {terminal}",
            "observed = read_value()",
        ]
    )
    source = "\n".join(lines) + "\n"

    cpp = transpile(source)
    suffix = "_cs0" if udf_element else ""
    assert f"{expected_cpp} read_value{suffix}(" in cpp
    if string:
        assert f"double read_value{suffix}(" not in cpp
    vector_type = "std::vector<std::string>" if string else "std::vector<int>"
    assert vector_type in cpp
    compile_cpp(cpp)

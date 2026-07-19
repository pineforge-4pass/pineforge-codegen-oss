"""Exact primitive UDF return typing for direct terminal array ``get`` calls."""

from __future__ import annotations

from itertools import product

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
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
        "array.copy(id=make_values()).get(0)",
        "make_values().copy().get(0)",
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


def test_nested_method_copy_reference_element_remains_fail_closed():
    source = r'''//@version=6
strategy("Nested method copy reference boundary")
type Item
    int value
blocked() =>
    array.from(Item.new(1)).copy().get(0)
observed = blocked()
'''
    cpp = transpile(source)
    assert "Item blocked(" not in cpp
    assert "double blocked(" in cpp


def test_nested_method_copy_temporary_reader_recursion_is_rejected():
    source = r'''//@version=6
strategy("Nested method copy recursion boundary")
reader() =>
    array.get(array.from(reader()).copy(), 0)
observed = reader()
'''
    with pytest.raises(
        CompileError,
        match="Recursive direct temporary-array reader cycle",
    ):
        transpile(source)


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
    ("type_name", "literal", "expected_cpp"),
    (
        ("string", '"x"', "std::string"),
        ("int", "7", "int"),
    ),
)
@pytest.mark.parametrize(
    "terminal",
    (
        "array.copy(id=source).get(0)",
        "array.copy(id=source).get(index=0)",
        "array.get(array.copy(id=source), 0)",
        "array.get(array.copy(id=source), index=0)",
        "array.get(id=array.copy(id=source), index=0)",
    ),
)
def test_keyword_namespace_copy_representations_return_exact_type(
    type_name: str,
    literal: str,
    expected_cpp: str,
    terminal: str,
):
    source = f'''//@version=6
strategy("Direct keyword copy producer representations")
read_value() =>
    array<{type_name}> source = array.from({literal})
    {terminal}
observed = read_value()
'''
    cpp = transpile(source)
    assert f"{expected_cpp} read_value(" in cpp
    if type_name == "string":
        assert "double read_value(" not in cpp
    assert f"std::vector<{expected_cpp}>(source)" in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "copy_call",
    (
        "array.copy()",
        "array.copy(source, id=other)",
        "array.copy(source=source)",
    ),
)
def test_invalid_namespace_copy_receiver_shapes_fail_closed(copy_call: str):
    source = f'''//@version=6
strategy("Invalid direct keyword copy shape")
blocked() =>
    array<string> source = array.from("x")
    array<string> other = array.from("y")
    {copy_call}
observed = blocked()
'''
    with pytest.raises(
        CompileError,
        match="array.copy: expected exactly one receiver 'id'",
    ):
        transpile(source)


@pytest.mark.parametrize("producer_first", (False, True))
def test_stateful_keyword_copy_direct_producer_is_exact_in_both_source_orders(
    producer_first: bool,
):
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    reader = '''read_value() =>
    array.get(id=array.copy(id=array.from(produce())), index=0)
'''
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Stateful direct keyword copy producer")\n'
        f"{definitions}"
        "observed = read_value()\n"
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert "std::vector<std::string>(std::vector<std::string>{produce_cs0()})" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize("producer_first", (False, True))
def test_keyword_copy_of_nested_method_copy_is_exact_in_both_source_orders(
    producer_first: bool,
):
    """Exercise the analyzer, keyword emitter, and nested-copy typer together."""
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    reader = '''read_value() =>
    array.get(id=array.copy(id=array.from(produce()).copy()), index=0)
'''
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Keyword copy of nested method copy")\n'
        f"{definitions}"
        "observed = read_value()\n"
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert cpp.count("std::vector<std::string>{produce_cs0()}") == 1
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "terminal",
    (
        'array.from("x").copy().get(0)',
        'array.from("x").copy().get(index=0)',
        'array.get(array.from("x").copy(), 0)',
        'array.get(id=array.from("x").copy(), index=0)',
    ),
)
def test_nested_method_copy_direct_temporary_returns_exact_string(
    terminal: str,
):
    source = f'''//@version=6
strategy("Nested method copy direct temporary")
read_value() =>
    {terminal}
observed = read_value()
'''
    cpp = transpile(source)
    assert "std::string read_value(" in cpp
    assert "double read_value(" not in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "expression",
    (
        'array.from("x").copy().get(0)',
        'array.copy(id=array.from("x")).get(0)',
        'array.get(id=array.copy(id=array.from("x").copy()), index=0)',
    ),
)
def test_copy_temporary_element_type_is_exact_outside_terminal_return(
    expression: str,
):
    source = f'''//@version=6
strategy("Copy temporary nonterminal typing")
global_value = {expression}
read_local() =>
    local_value = {expression}
    local_value == "x"
observed = read_local()
'''
    cpp = transpile(source)
    assert "std::string global_value =" in cpp
    assert "bool read_local(" in cpp
    assert 'local_value == std::string("x")' in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("functional", "keyword", "producer_first"),
    product((False, True), repeat=3),
)
def test_nested_method_copy_temporary_stateful_matrix(
    functional: bool,
    keyword: bool,
    producer_first: bool,
):
    """All 2^3 get-shape/order cells keep one stateful element call site."""
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    receiver = 'array.from(produce()).copy()'
    if functional:
        terminal = (
            f"array.get(id={receiver}, index=0)"
            if keyword
            else f"array.get({receiver}, 0)"
        )
    else:
        terminal = (
            f"{receiver}.get(index=0)"
            if keyword
            else f"{receiver}.get(0)"
        )
    reader = f"read_value() =>\n    {terminal}\n"
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Nested method copy temporary matrix")\n'
        f"{definitions}"
        "observed = read_value()\n"
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert "std::vector<std::string>(" in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
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


@pytest.mark.parametrize(
    ("literal", "expected_cpp"),
    (
        ("7", "int"),
        ("1.5", "double"),
        ("true", "bool"),
        ('"x"', "std::string"),
        ("color.red", "int"),
    ),
)
def test_forward_local_identity_return_preserves_every_primitive_signature(
    literal: str,
    expected_cpp: str,
):
    source = f'''//@version=6
strategy("Forward local identity primitive")
read_value() =>
    local_value = array.from(produce()).copy().get(0)
    local_value
produce() =>
    {literal}
observed = read_value()
'''

    cpp = transpile(source)
    assert f"{expected_cpp} read_value(" in cpp
    assert f"{expected_cpp} local_value =" in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("functional", "keyword", "copy_layer", "producer_first"),
    product((False, True), repeat=4),
)
def test_forward_local_identity_stateful_representation_matrix(
    functional: bool,
    keyword: bool,
    copy_layer: bool,
    producer_first: bool,
):
    """All 2^4 source-order/get/copy cells preserve one stateful call site."""
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    receiver = "array.from(produce())"
    if copy_layer:
        receiver = (
            f"array.copy(id={receiver})"
            if functional
            else f"{receiver}.copy()"
        )
    if functional:
        initializer = (
            f"array.get(id={receiver}, index=0)"
            if keyword
            else f"array.get({receiver}, 0)"
        )
    else:
        initializer = (
            f"{receiver}.get(index=0)"
            if keyword
            else f"{receiver}.get(0)"
        )
    reader = (
        "read_value() =>\n"
        f"    local_value = {initializer}\n"
        "    local_value\n"
    )
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Forward local identity representation matrix")\n'
        f"{definitions}"
        "observed = read_value()\n"
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert "std::string local_value =" in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "definitions",
    (
        '''reader() =>
    local_value = array.from(reader()).copy().get(0)
    local_value
''',
        '''first_reader() =>
    local_value = array.from(second_reader()).copy().get(0)
    local_value
second_reader() =>
    first_reader()
''',
    ),
)
def test_forward_local_identity_temporary_reader_recursion_is_rejected(
    definitions: str,
):
    source = (
        "//@version=6\n"
        'strategy("Forward local identity recursion")\n'
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


@pytest.mark.parametrize("storage", ("var", "varip"))
def test_forward_local_identity_persistent_local_remains_fail_closed(
    storage: str,
):
    source = f'''//@version=6
strategy("Forward persistent local boundary")
blocked() =>
    {storage} local_value = array.from(produce()).copy().get(0)
    local_value
produce() =>
    "x"
observed = blocked()
'''

    if storage == "varip":
        with pytest.raises(CompileError, match="varip is not supported"):
            transpile(source)
        return
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp


@pytest.mark.parametrize(
    ("type_hint", "literal", "expected_cpp", "compiles"),
    (
        ("float", "7", "double", True),
        ("string", "7", "std::string", False),
    ),
)
def test_forward_local_identity_explicit_type_hint_remains_authoritative(
    type_hint: str,
    literal: str,
    expected_cpp: str,
    compiles: bool,
):
    source = f'''//@version=6
strategy("Forward typed local boundary")
read_value() =>
    {type_hint} local_value = array.from(produce()).copy().get(0)
    local_value
produce() =>
    {literal}
observed = read_value()
'''

    cpp = transpile(source)
    assert f"{expected_cpp} read_value(" in cpp
    assert f"{expected_cpp} local_value =" in cpp
    assert "int read_value(" not in cpp
    if compiles:
        compile_cpp(cpp)
    else:
        # Pine rejects the incompatible declaration. Keep its authoritative
        # hint instead of making the invalid source appear valid by refining
        # the UDF return to the initializer's element type.
        assert "std::vector<int>" in cpp


@pytest.mark.parametrize(
    "earlier_declaration",
    ("float local_value = 0.0", "var local_value = 0.0"),
)
def test_forward_local_identity_requires_one_unique_same_named_declaration(
    earlier_declaration: str,
):
    source = f'''//@version=6
strategy("Forward duplicate local boundary")
blocked() =>
    {earlier_declaration}
    local_value = array.from(produce()).copy().get(0)
    local_value
produce() =>
    "x"
observed = blocked()
'''

    cpp = transpile(source)
    assert "std::string blocked(" not in cpp


def _direct_terminal_element_shadow_source(
    *,
    shadow_kind: str | None,
    producer_later: bool,
    namespace_spelling: bool,
) -> str:
    """Build one direct-terminal lexical-shadow matrix cell."""
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    terminal = (
        "array.get(array.copy(array.from(produce())), 0)"
        if namespace_spelling
        else "array.from(produce()).copy().get(0)"
    )
    if shadow_kind == "local":
        reader = f'''read_value() =>
    produce = 1
    {terminal}
'''
        invocation = "read_value()"
    elif shadow_kind == "parameter":
        reader = f'''read_value(float produce) =>
    {terminal}
'''
        invocation = "read_value(1.0)"
    else:
        reader = f'''read_value() =>
    {terminal}
'''
        invocation = "read_value()"
    definitions = reader + producer if producer_later else producer + reader
    return (
        "//@version=6\n"
        'strategy("Direct terminal element shadow")\n'
        f"{definitions}"
        f"observed = {invocation}\n"
    )


def _direct_shadow_preflight_error(
    source: str,
    *,
    filename: str,
):
    ast = Parser(
        Lexer(source, filename=filename).tokenize(),
        source=source,
        filename=filename,
    ).parse()
    analyzer = Analyzer(ast, filename=filename)
    with pytest.raises(CompileError) as caught:
        analyzer.analyze()

    # This rejection is a whole-program syntactic preflight. No function or
    # call-site analysis may have begun, regardless of source order.
    assert analyzer._func_defs == {}
    assert analyzer._func_infos == []
    assert analyzer._func_call_cs_map == {}
    assert analyzer._func_call_site_count == {}
    assert analyzer._func_ta_ranges == {}
    assert analyzer._ta_call_sites == []
    return caught.value.diagnostics


@pytest.mark.parametrize(
    ("shadow_kind", "producer_later", "namespace_spelling"),
    product(("local", "parameter"), (False, True), (False, True)),
)
def test_direct_terminal_element_callee_shadow_matrix_is_rejected(
    shadow_kind: str,
    producer_later: bool,
    namespace_spelling: bool,
):
    """All 2^3 lexical-shadow/order/spelling cells fail before codegen."""
    source = _direct_terminal_element_shadow_source(
        shadow_kind=shadow_kind,
        producer_later=producer_later,
        namespace_spelling=namespace_spelling,
    )
    filename = (
        "direct-shadow:"
        f"{shadow_kind}-{int(producer_later)}-{int(namespace_spelling)}.pine"
    )

    diagnostics = _direct_shadow_preflight_error(source, filename=filename)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.level.value == "error"
    assert diagnostic.phase.value == "ANALYZER"
    assert diagnostic.location.file == filename
    expected_line = {
        ("local", False): 8,
        ("local", True): 5,
        ("parameter", False): 7,
        ("parameter", True): 4,
    }[(shadow_kind, producer_later)]
    expected_col = 44 if namespace_spelling else 23
    assert (
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (expected_line, expected_col, expected_col + 1)
    assert diagnostic.message == (
        "Direct temporary-array element call 'produce()' resolves to a local "
        "or parameter, not a user-defined function."
    )


@pytest.mark.parametrize(
    ("producer_later", "namespace_spelling"),
    product((False, True), repeat=2),
)
def test_direct_terminal_element_callee_no_shadow_controls(
    producer_later: bool,
    namespace_spelling: bool,
):
    source = _direct_terminal_element_shadow_source(
        shadow_kind=None,
        producer_later=producer_later,
        namespace_spelling=namespace_spelling,
    )

    cpp = transpile(source)
    assert "std::string read_value_cs0(" in cpp
    assert "double read_value_cs0(" not in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


def test_direct_terminal_expired_block_local_does_not_shadow_element_callee():
    source = r'''//@version=6
strategy("Expired block shadow")
produce() =>
    unused = ta.sma(close, 2)
    "x"
reader() =>
    if close > open
        produce = 1
    array.from(produce()).copy().get(0)
observed = reader()
'''

    cpp = transpile(source)
    assert "std::string reader_cs0(" in cpp
    assert "double reader_cs0(" not in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


def test_direct_terminal_top_level_tuple_binding_shadows_element_callee():
    source = r'''//@version=6
strategy("Direct terminal tuple shadow")
produce() =>
    unused = ta.sma(close, 2)
    "x"
reader() =>
    [produce, other] = [1, 2]
    array.from(produce()).copy().get(0)
observed = reader()
'''
    diagnostics = _direct_shadow_preflight_error(
        source,
        filename="direct-shadow:tuple.pine",
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.phase.value == "ANALYZER"
    assert (
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (8, 23, 24)
    assert diagnostic.message == (
        "Direct temporary-array element call 'produce()' resolves to a local "
        "or parameter, not a user-defined function."
    )


@pytest.mark.parametrize(
    ("signature", "shadow_declaration", "call"),
    (
        ("blocked()", "    produce = 1\n", "blocked()"),
        ("blocked(float produce)", "", "blocked(1.0)"),
    ),
)
@pytest.mark.parametrize("producer_first", (False, True))
def test_forward_local_identity_element_callee_shadow_remains_fail_closed(
    signature: str,
    shadow_declaration: str,
    call: str,
    producer_first: bool,
):
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    reader = f'''{signature} =>
{shadow_declaration}    local_value = array.from(produce()).copy().get(0)
    local_value
'''
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Forward element callee shadow boundary")\n'
        f"{definitions}"
        f"observed = {call}\n"
    )

    shadow_kind = "local" if shadow_declaration else "parameter"
    filename = f"alias-shadow:{shadow_kind}-{int(producer_first)}.pine"
    diagnostics = _direct_shadow_preflight_error(
        source,
        filename=filename,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.level.value == "error"
    assert diagnostic.phase.value == "ANALYZER"
    assert diagnostic.location.file == filename
    expected_line = {
        ("local", False): 5,
        ("local", True): 8,
        ("parameter", False): 4,
        ("parameter", True): 7,
    }[(shadow_kind, producer_first)]
    assert (
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (expected_line, 37, 38)
    assert diagnostic.message == (
        "Direct temporary-array element call 'produce()' resolves to a local "
        "or parameter, not a user-defined function."
    )


def test_forward_local_identity_expired_block_local_does_not_shadow_element_callee():
    source = r'''//@version=6
strategy("Expired block alias shadow")
produce() =>
    unused = ta.sma(close, 2)
    "x"
reader() =>
    if close > open
        produce = 1
    local_value = array.from(produce()).copy().get(0)
    local_value
observed = reader()
'''

    cpp = transpile(source)
    assert "std::string reader_cs0(" in cpp
    assert "double reader_cs0(" not in cpp
    assert "std::string local_value =" in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize("producer_first", (False, True))
def test_forward_local_identity_self_named_alias_rhs_uses_global_udf(
    producer_first: bool,
):
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    reader = '''reader() =>
    produce = array.from(produce()).copy().get(0)
    produce
'''
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Self-named alias RHS")\n'
        f"{definitions}"
        "observed = reader()\n"
    )

    cpp = transpile(source)
    assert "std::string reader_cs0(" in cpp
    assert "double reader_cs0(" not in cpp
    assert "std::string produce =" in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize(
    ("producer_first", "namespace_spelling"),
    product((False, True), repeat=2),
)
def test_forward_local_identity_top_level_tuple_binding_shadows_element_callee(
    producer_first: bool,
    namespace_spelling: bool,
):
    producer = '''produce() =>
    unused = ta.sma(close, 2)
    "x"
'''
    initializer = (
        "array.get(array.copy(array.from(produce())), 0)"
        if namespace_spelling
        else "array.from(produce()).copy().get(0)"
    )
    reader = f'''reader() =>
    [produce, other] = [1, 2]
    local_value = {initializer}
    local_value
'''
    definitions = producer + reader if producer_first else reader + producer
    source = (
        "//@version=6\n"
        'strategy("Adjacent alias tuple shadow")\n'
        f"{definitions}"
        "observed = reader()\n"
    )
    filename = (
        "alias-shadow:tuple-"
        f"{int(producer_first)}-{int(namespace_spelling)}.pine"
    )
    diagnostics = _direct_shadow_preflight_error(
        source,
        filename=filename,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.level.value == "error"
    assert diagnostic.phase.value == "ANALYZER"
    assert diagnostic.location.file == filename
    expected_line = 8 if producer_first else 5
    expected_col = 58 if namespace_spelling else 37
    assert (
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (expected_line, expected_col, expected_col + 1)
    assert diagnostic.message == (
        "Direct temporary-array element call 'produce()' resolves to a local "
        "or parameter, not a user-defined function."
    )


@pytest.mark.parametrize("alias_carrier", (False, True))
def test_expired_block_tuple_binding_does_not_shadow_element_callee(
    alias_carrier: bool,
):
    terminal = "array.from(produce()).copy().get(0)"
    return_lines = (
        f"    local_value = {terminal}\n    local_value\n"
        if alias_carrier
        else f"    {terminal}\n"
    )
    source = (
        "//@version=6\n"
        'strategy("Expired block tuple shadow")\n'
        "produce() =>\n"
        "    unused = ta.sma(close, 2)\n"
        '    "x"\n'
        "pair() =>\n"
        "    [1, 2]\n"
        "reader() =>\n"
        "    if close > open\n"
        "        [produce, other] = pair()\n"
        f"{return_lines}"
        "observed = reader()\n"
    )

    cpp = transpile(source)
    assert "std::string reader_cs0(" in cpp
    assert "double reader_cs0(" not in cpp
    assert "produce_cs0()" in cpp
    assert "produce_cs1" not in cpp
    assert cpp.count("ta::SMA _ta_sma_1;") == 1
    compile_cpp(cpp)


@pytest.mark.parametrize(
    "body",
    (
        '''    first_value = array.from(produce()).copy().get(0)
    local_value = first_value
    local_value
''',
        '''    local_value = array.from(produce()).copy().get(0)
    local_value := "replacement"
    local_value
''',
        '''    [local_value, other] = [array.from(produce()).copy().get(0), 1]
    local_value
''',
        '''    if close > open
        local_value = array.from(produce()).copy().get(0)
    local_value
''',
        '''    local_value = array.from(produce()).copy().get(0)
    extras = array.from(local_value)
    local_value
''',
    ),
)
def test_forward_local_identity_non_direct_bindings_remain_fail_closed(
    body: str,
):
    source = (
        "//@version=6\n"
        'strategy("Forward non-direct local boundary")\n'
        "blocked() =>\n"
        f"{body}"
        "produce() =>\n"
        '    "x"\n'
        "observed = blocked()\n"
    )

    cpp = transpile(source)
    assert "std::string blocked(" not in cpp


@pytest.mark.parametrize(
    "initializer",
    (
        'array.from(produce("x")).copy().get(0)',
        "make_values().get(0)",
        "array.slice(array.from(produce()), 0, 1).get(0)",
    ),
)
def test_forward_local_identity_non_direct_producers_remain_fail_closed(
    initializer: str,
):
    producer = (
        '''produce(string value) =>
    value
'''
        if 'produce("x")' in initializer
        else '''produce() =>
    "x"
make_values() =>
    array.from("x")
'''
    )
    source = (
        "//@version=6\n"
        'strategy("Forward non-direct producer boundary")\n'
        "blocked() =>\n"
        f"    local_value = {initializer}\n"
        "    local_value\n"
        f"{producer}"
        "observed = blocked()\n"
    )

    if 'produce("x")' in initializer:
        with pytest.raises(CompileError, match="Unknown function 'produce"):
            transpile(source)
        return
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp


def test_forward_local_identity_defaulted_parameter_still_fails_closed():
    source = r'''//@version=6
strategy("Forward local default parameter boundary")
blocked() =>
    local_value = array.from(produce()).copy().get(0)
    local_value
produce(string value = "x") =>
    value
observed = blocked()
'''

    with pytest.raises(CompileError, match="Unknown function 'produce"):
        transpile(source)


@pytest.mark.parametrize(
    "preamble_and_initializer",
    (
        '''type Item
    int value
blocked() =>
    local_value = array.from(Item.new(1)).copy().get(0)
''',
        '''blocked() =>
    local_value = array.from(array.from("x")).copy().get(0)
''',
        '''blocked() =>
    int array = 1
    local_value = array.get(array.from(produce()), 0)
''',
    ),
)
def test_forward_local_identity_reference_or_shadowed_boundaries_stay_closed(
    preamble_and_initializer: str,
):
    source = (
        "//@version=6\n"
        'strategy("Forward reference boundary")\n'
        f"{preamble_and_initializer}"
        "    local_value\n"
        "produce() =>\n"
        '    "x"\n'
        "observed = blocked()\n"
    )

    if "int array = 1" in preamble_and_initializer:
        with pytest.raises(CompileError, match="Unknown function 'produce"):
            transpile(source)
        return
    cpp = transpile(source)
    assert "std::string blocked(" not in cpp

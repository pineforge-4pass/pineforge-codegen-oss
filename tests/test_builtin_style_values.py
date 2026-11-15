"""Builtin constant values keep their Pine types across value contexts.

The exact public reporter scripts are exercised separately through the native
reproduction harness. These units pin the shared constant, inference and
nominal-type contracts; native rows observe the emitted translation unit.
"""

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import Identifier, MemberAccess
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType, TypeSpec
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("builtin constant values")\n'


def analyze(body):
    source = HEADER + body
    return Analyzer(Parser(Lexer(source).tokenize(), source=source).parse()).analyze()


@pytest.mark.parametrize("namespace,member,value", [
    ("size", "tiny", "tiny"), ("size", "small", "small"),
    ("size", "normal", "normal"), ("size", "large", "large"),
    ("position", "top_right", "top_right"),
    ("position", "bottom_left", "bottom_left"),
    ("line", "style_dashed", "dsh"), ("line", "style_solid", "sol"),
    ("label", "style_label_up", "lup"),
    ("shape", "circle", "shape_circle"), ("shape", "cross", "shape_cross"),
])
def test_constant_member_has_one_string_type_and_value(namespace, member, value):
    context = analyze(f"held = {namespace}.{member}\n")
    assert context.symbols.resolve("held").pine_type == PineType.STRING
    generator = CodeGen(context)
    expression = MemberAccess(object=Identifier(name=namespace), member=member)
    assert generator._infer_type(expression) == "std::string"
    assert generator._visit_member_access(expression) == f'std::string("{value}")'


STRING_DRIVER = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = 100.0;
        bars[i].high = 103.0;
        bars[i].low = 97.0;
        bars[i].close = i % 2 == 0 ? 101.0 : 99.0;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 14;
        }
        std::cout << strategy.held.size() << ":" << strategy.held << "\n";
    }
}
'''


@pytest.mark.parametrize("body,expected", [
    (
        "string held = size.small\nif close > open\n    held := size.large\n",
        "5:large\n5:small\n5:large\n",
    ),
    (
        "string held = position.top_right\n"
        "if close > open\n    held := position.bottom_left\n",
        "11:bottom_left\n9:top_right\n11:bottom_left\n",
    ),
    (
        "held = close > open ? size.large : size.small\n",
        "5:large\n5:small\n5:large\n",
    ),
    (
        'styleOf(string choice) =>\n    switch choice\n'
        '        "Dashed" => line.style_dashed\n        => line.style_solid\n'
        'held = styleOf(close > open ? "Dashed" : "Solid")\n',
        "3:dsh\n3:sol\n3:dsh\n",
    ),
    (
        "held = switch\n    close > open => shape.circle\n    => shape.cross\n",
        "12:shape_circle\n11:shape_cross\n12:shape_circle\n",
    ),
    (
        "echo(string sz) => sz\nheld = echo(size.small)\n",
        "5:small\n5:small\n5:small\n",
    ),
    (
        "echo(string style) => style\nheld = echo(label.style_label_up)\n",
        "3:lup\n3:lup\n3:lup\n",
    ),
], ids=["reassignment", "position", "ternary", "function-return", "shape-switch",
        "parameter", "label-parameter"])
def test_string_constant_value_transport_runs(body, expected):
    result = transpile_full(HEADER + body)
    assert not any(diagnostic.code == "PF-W1078" for diagnostic in result["diagnostics"])
    assert run_emitted_tu(result["cpp"], STRING_DRIVER, opt="-O0",
                          label="builtin constant value transport") == expected


def test_nested_stateful_function_variants_keep_string_returns_and_parameters():
    body = (
        "styleOf(string choice) =>\n"
        "    average = ta.sma(close, 2)\n"
        '    choice == "Dashed" ? line.style_dashed : line.style_solid\n'
        "outer(string choice) => styleOf(choice)\n"
        'first = outer("Dashed")\nsecond = outer("Solid")\n'
        "held = first + second\n"
    )
    cpp = transpile(HEADER + body)
    declarations = re.findall(
        r"^\s*(\S+)\s+((?:styleOf|outer)(?:_cs\d+|__ni\d+)*)\(([^)]*)\)\s*\{",
        cpp, re.MULTILINE,
    )
    assert len(declarations) >= 3, declarations
    assert all(result == "std::string" and "std::string" in parameters
               for result, _name, parameters in declarations)
    assert run_emitted_tu(cpp, STRING_DRIVER, opt="-O0",
                          label="nested style function variants") == (
        "6:dshsol\n6:dshsol\n6:dshsol\n"
    )


def test_direct_drawing_arguments_and_value_arguments_compile_together():
    cpp = transpile(HEADER + """
string held = line.style_dashed
line.new(bar_index, high, bar_index + 1, high, style = line.style_solid)
line.new(bar_index, low, bar_index + 1, low, style = held)
box.new(bar_index, high, bar_index + 1, low, border_style = line.style_dashed)
label.new(bar_index, high, "x", style = label.style_label_up, size = size.small)
""")
    compile_cpp(cpp, label="direct and stored drawing constants")


@pytest.mark.parametrize("call", [
    'input.string(size.small, "Text size", options = [size.tiny, size.small, size.normal])',
    'input.string(options = [size.tiny, size.small, size.normal], title = "Text size", defval = size.small)',
])
def test_size_input_binds_positional_and_named_constant_arguments(call):
    result = transpile_full(HEADER + f"held = {call}\n")
    assert result["inputs"] == [{
        "title": "Text size", "type": "string", "default": "small",
        "options": ["tiny", "small", "normal"], "supported": True,
    }]


def test_string_length_binds_constant_values_by_position_and_name():
    body = (
        "positional = str.length(size.small)\n"
        "named = str.length(string = size.small)\n"
        'held = str.tostring(positional) + ":" + str.tostring(named)\n'
    )
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="named and positional string lengths") == "3:5:5\n" * 3


@pytest.mark.parametrize("call", [
    "array.sort(values, direction)", "array.sort(values, order = direction)",
    "values.sort(direction)", "values.sort(order = direction)",
])
def test_sort_order_reaches_array_sort_in_each_binding(call):
    body = (
        "values = array.from(3.0, 1.0, 2.0)\n"
        "direction = order.descending\n" + call + "\n"
        'held = str.tostring(values.get(0)) + ":" + str.tostring(values.get(1)) '
        '+ ":" + str.tostring(values.get(2))\n'
    )
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="array sort order bindings") == "5:3:2:1\n" * 3


@pytest.mark.parametrize("call", [
    "array.sort_indices(values, direction)",
    "values.sort_indices(order = direction)",
])
def test_sort_order_reaches_sort_indices(call):
    body = (
        "values = array.from(3.0, 1.0, 2.0)\n"
        "direction = order.descending\n" + f"indices = {call}\n"
        'held = str.tostring(indices.get(0)) + ":" + str.tostring(indices.get(1)) '
        '+ ":" + str.tostring(indices.get(2))\n'
    )
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="sort indices order bindings") == "5:0:2:1\n" * 3


@pytest.mark.parametrize("call", [
    "matrix.sort(values, 0, direction)",
    "values.sort(order = direction, column = 0)",
])
def test_sort_order_reaches_matrix_sort_in_each_binding(call):
    body = (
        "values = matrix.new<float>(3, 1, 0.0)\n"
        "values.set(0, 0, 3.0)\nvalues.set(1, 0, 1.0)\nvalues.set(2, 0, 2.0)\n"
        "direction = order.descending\n" + call + "\n"
        'held = str.tostring(values.get(0, 0)) + ":" + str.tostring(values.get(1, 0)) '
        '+ ":" + str.tostring(values.get(2, 0))\n'
    )
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="matrix sort order bindings") == "5:3:2:1\n" * 3


def test_sort_order_preserves_nominal_identity_and_native_selection():
    body = """pick(array<float> values, direction = order.descending) =>
    direction == order.ascending ? values.min() : values.max()
values = array.from(3.0, 1.0, 2.0)
direction = close > open ? order.ascending : order.descending
alias = direction
selected = pick(values, alias)
byDefault = pick(values)
ascending = pick(values, direction = order.ascending)
held = str.tostring(selected) + ":" + str.tostring(byDefault) + ":" + str.tostring(ascending)
"""
    context = analyze(body)
    assert context.symbols.resolve("direction").type_spec == TypeSpec("enum", name="sort_order")
    assert context.symbols.resolve("alias").type_spec == TypeSpec("enum", name="sort_order")
    cpp = transpile(HEADER + body)
    assert run_emitted_tu(cpp, STRING_DRIVER, opt="-O0",
                          label="sort order default and comparison") == (
        "5:1:3:1\n5:3:3:1\n5:1:3:1\n"
    )


def test_sort_order_identity_survives_default_returns_and_wrappers():
    body = (
        "identity(direction = order.descending) => direction\n"
        "relay() => identity(order.ascending)\n"
        "defaulted = identity()\nforwarded = relay()\n"
        'held = str.tostring(defaulted == order.descending) + ":" '
        '+ str.tostring(forwarded == order.ascending) + ":" '
        '+ str.tostring(defaulted != forwarded)\n'
    )
    context = analyze(body)
    for name in ("defaulted", "forwarded"):
        assert context.symbols.resolve(name).type_spec == TypeSpec("enum", name="sort_order")
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="nominal sort order returns") == "14:true:true:true\n" * 3


def test_sort_order_method_default_and_result_keep_their_nominal_type():
    body = (
        "type Bucket\n    int marker\n"
        "method selected(Bucket self, direction = order.descending) =>\n"
        "    self.marker > 0 ? direction : order.ascending\n"
        "bucket = Bucket.new(1)\n"
        "defaulted = bucket.selected()\n"
        "explicit = bucket.selected(direction = order.ascending)\n"
        "held = str.tostring(defaulted != explicit)\n"
    )
    context = analyze(body)
    for name in ("defaulted", "explicit"):
        assert context.symbols.resolve(name).type_spec == TypeSpec("enum", name="sort_order")
    assert run_emitted_tu(transpile(HEADER + body), STRING_DRIVER, opt="-O0",
                          label="nominal sort order method") == "4:true\n" * 3


@pytest.mark.parametrize("body", [
    "string invalid = order.ascending\n",
    "direction = order.ascending\ninvalid = direction == \"ascending\"\n",
    "invalid = str.length(order.ascending)\n",
    'invalid = input.string(order.ascending, "Direction")\n',
    'consume(string value) => value\ninvalid = consume(order.ascending)\n',
    'consume(value) => str.length(value)\ninvalid = consume(order.ascending)\n',
    'consume(value) =>\n    string held = value\n    held\ninvalid = consume(order.ascending)\n',
    "consume(direction = order.descending) => direction == order.ascending\n"
    'invalid = consume("ascending")\n',
    "direction = order.ascending\ninvalid = direction + order.descending\n",
    "direction = order.ascending\ninvalid = direction < order.descending\n",
    'values = array.from(1.0, 2.0)\narray.sort(values, "descending")\n',
    'invalid = input.string("ascending", "Direction", options = [order.ascending])\n',
    'type Config\n    string direction\n'
    'direction = order.ascending\ninvalid = Config.new(direction)\n',
    'array<string> invalid = array.from(order.ascending)\n',
    "identity(direction = order.descending) => direction\n"
    "string invalid = identity()\n",
], ids=["declaration", "comparison", "string-builtin", "string-input", "string-parameter",
        "inferred-parameter", "local-declaration", "enum-parameter", "arithmetic",
        "ordering", "sort-string", "input-options", "constructor-field", "collection-element",
        "returned-enum"])
def test_sort_order_cannot_implicitly_enter_another_value_domain(body):
    with pytest.raises(CompileError) as error:
        transpile(HEADER + body)
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.location is not None
    assert diagnostic.code != "PF-E0000"
    assert "sort_order" in diagnostic.message

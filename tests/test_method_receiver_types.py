"""Lossless parsing and dispatch for typed Pine v6 method receivers.

Primitive and generic receiver hints must survive parsing.  In particular, a
user method whose name collides with a collection builtin must win dispatch;
otherwise the authored declaration can disappear while valid but wrong C++ is
emitted for the builtin body.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import MethodDef
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


@pytest.mark.parametrize(
    ("receiver_hint", "expected_hint"),
    [
        ("int", "int"),
        ("float", "float"),
        ("bool", "bool"),
        ("string", "string"),
        ("array<int>", "array<int>"),
        ("map<string, int>", "map<string,int>"),
        ("matrix<float>", "matrix<float>"),
        ("int[]", "array<int>"),
    ],
)
def test_method_receiver_type_hint_parses_without_recovery(
    receiver_hint: str,
    expected_hint: str,
) -> None:
    source = f'''//@version=6
strategy("typed method receiver")
method inspect({receiver_hint} self) => 1
result = 0
'''

    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert (program.annotations or {}).get("parse_recovery_count", 0) == 0
    methods = [node for node in program.body if isinstance(node, MethodDef)]
    assert len(methods) == 1
    assert methods[0].type_name == expected_hint
    assert methods[0].params == ["self"]
    assert methods[0].annotations["param_type_hints"] == [expected_hint]


def test_primitive_receiver_method_emits_authored_body() -> None:
    source = '''//@version=6
strategy("primitive receiver method")
method add(int self, int delta) => self + delta + 100
result = 1.add(4)
'''

    cpp = transpile(source)
    assert re.search(r"int _udt_int_add\(int self, int delta\)", cpp)
    assert "_udt_int_add(1, 4)" in cpp
    assert "None()" not in cpp
    compile_cpp(cpp, label="primitive-method-receiver")


def test_primitive_history_receiver_uses_series_boundary() -> None:
    source = '''//@version=6
strategy("primitive history receiver")
method previous(int self) => self[1]
current = bar_index
result = current.previous()
'''

    cpp = transpile(source)
    assert re.search(
        r"int64_t _udt_int_previous(?:_cs0)?\(const Series<int64_t>& self\)",
        cpp,
    )
    assert "current.previous()" not in cpp
    assert re.search(r"_udt_int_previous(?:_cs0)?\(\(\[&\]\(\)", cpp)
    compile_cpp(cpp, label="primitive-history-method-receiver")


def test_array_user_method_precedes_same_named_builtin() -> None:
    source = '''//@version=6
strategy("array builtin collision")
method push(array<int> self, int value) => array.unshift(self, value + 100)
var array<int> values = array.new<int>()
values.push(7)
result = values.get(0)
'''

    cpp = transpile(source)
    assert re.search(r"_udt_array_int_+push\(values, 7\)", cpp)
    assert "values.push_back(7);" not in cpp
    assert "self.insert(self.begin(), (value + 100));" in cpp
    compile_cpp(cpp, label="array-method-builtin-collision")


def test_map_receiver_keeps_shared_id_value_handle() -> None:
    source = '''//@version=6
strategy("map builtin collision")
method put(map<string, int> self, string key, int value) => map.put(self, key, value + 100)
var map<string, int> values = map.new<string, int>()
values.put("answer", 7)
result = values.get("answer")
'''

    cpp = transpile(source)
    assert re.search(
        r"_udt_map_string_int_put\(PineMap<std::string, int> self, "
        r"std::string key, int value\)",
        cpp,
    )
    assert '_udt_map_string_int_put(values, std::string("answer"), 7)' in cpp
    assert 'values.put(std::string("answer"), 7);' not in cpp
    compile_cpp(cpp, label="map-method-builtin-collision")


def test_matrix_receiver_emits_by_reference_before_builtin() -> None:
    source = '''//@version=6
strategy("matrix builtin collision")
method set(matrix<int> self, int row, int col, int value) =>
    matrix.set(self, row, col, value + 100)
    matrix.get(self, row, col)
var matrix<int> values = matrix.new<int>(1, 1, 0)
values.set(0, 0, 7)
result = values.get(0, 0)
'''

    cpp = transpile(source)
    assert re.search(
        r"_udt_matrix_int_set\(PineGenericMatrix<int>& self, int row, "
        r"int col, int value\)",
        cpp,
    )
    assert "_udt_matrix_int_set(values, 0, 0, 7)" in cpp
    assert "values.set(0, 0, 7);" not in cpp
    compile_cpp(cpp, label="matrix-method-builtin-collision")

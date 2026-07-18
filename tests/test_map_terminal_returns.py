"""Terminal UDF return inference for established map call forms."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


_TERMINAL_SOURCE = """//@version=6
strategy("Map terminal returns")
var global_int = map.new<string, int>()
var global_string = map.new<string, string>()

clear_functional(map<string, int> values) =>
    map.clear(values)

clear_global() =>
    global_int.clear()

clear_local() =>
    values = map.new<string, int>()
    values.clear()

clear_param(map<string, int> values) =>
    values.clear()

put_all_functional(map<string, int> target, map<string, int> source) =>
    map.put_all(target, source)

put_all_global(map<string, int> source) =>
    global_int.put_all(source)

put_all_local(map<string, int> source) =>
    target = map.new<string, int>()
    target.put_all(source)

put_all_param(map<string, int> target, map<string, int> source) =>
    target.put_all(id2=source)

clear_inferred(inferred_int_map) =>
    map.clear(inferred_int_map)

put_all_inferred(inferred_target, inferred_source) =>
    inferred_target.put_all(inferred_source)

keys_functional(map<string, int> values) =>
    map.keys(values)

keys_global() =>
    global_int.keys()

keys_local() =>
    values = map.new<string, int>()
    values.keys()

keys_param(map<string, int> values) =>
    values.keys()

values_functional(map<string, int> values) =>
    map.values(values)

values_global() =>
    global_int.values()

values_local() =>
    values = map.new<string, int>()
    values.values()

values_param(map<string, int> values) =>
    values.values()

copy_functional(map<string, int> values) =>
    map.copy(values)

copy_global() =>
    global_int.copy()

copy_local() =>
    values = map.new<string, int>()
    values.copy()

copy_param(map<string, int> values) =>
    values.copy()

keys_inferred(inferred_int_map) =>
    map.keys(inferred_int_map)

values_inferred(inferred_int_map) =>
    inferred_int_map.values()

copy_inferred(inferred_int_map) =>
    inferred_int_map.copy()

get_functional(map<string, string> string_map) =>
    map.get(string_map, "key")

get_global() =>
    global_string.get("key")

get_local() =>
    local_strings = map.new<string, string>()
    local_strings.get("key")

get_param(map<string, string> string_map) =>
    string_map.get(key="key")

put_functional(map<string, string> string_map) =>
    map.put(string_map, "key", "value")

put_global() =>
    global_string.put("key", "value")

put_local() =>
    local_strings = map.new<string, string>()
    local_strings.put("key", "value")

put_param(map<string, string> string_map) =>
    string_map.put(key="key", value="value")

remove_functional(map<string, string> string_map) =>
    map.remove(string_map, "key")

remove_global() =>
    global_string.remove("key")

remove_local() =>
    local_strings = map.new<string, string>()
    local_strings.remove("key")

remove_param(map<string, string> string_map) =>
    string_map.remove(key="key")

get_inferred(inferred_string_map) =>
    map.get(inferred_string_map, "key")

put_inferred(inferred_string_map) =>
    inferred_string_map.put("key", "value")

remove_inferred(inferred_string_map) =>
    inferred_string_map.remove("key")

int_values = map.new<string, int>()
int_source = map.new<string, int>()
string_values = map.new<string, string>()
map.put(int_source, "source", 8)
map.put(global_string, "key", "global")
clear_functional(int_values)
clear_global()
clear_local()
clear_param(int_values)
put_all_functional(int_values, int_source)
put_all_global(int_source)
put_all_local(int_source)
put_all_param(int_values, int_source)
clear_inferred(int_values)
put_all_inferred(int_values, int_source)
keys_a = keys_functional(int_values)
keys_b = keys_global()
keys_c = keys_local()
keys_d = keys_param(int_values)
values_a = values_functional(int_values)
values_b = values_global()
values_c = values_local()
values_d = values_param(int_values)
copy_a = copy_functional(int_values)
copy_b = copy_global()
copy_c = copy_local()
copy_d = copy_param(int_values)
keys_e = keys_inferred(int_values)
values_e = values_inferred(int_values)
copy_e = copy_inferred(int_values)
string_a = get_functional(string_values)
string_b = get_global()
string_c = get_local()
string_d = get_param(string_values)
string_e = put_functional(string_values)
string_f = put_global()
string_g = put_local()
string_h = put_param(string_values)
string_i = remove_functional(string_values)
string_j = remove_global()
string_k = remove_local()
string_l = remove_param(string_values)
string_m = get_inferred(string_values)
string_n = put_inferred(string_values)
string_o = remove_inferred(string_values)
"""


_NONTERMINAL_SOURCE = """//@version=6
strategy("Map nonterminal identity")
var global_values = map.new<string, string>()
map.put(global_values, "top", "functional")
top_functional = map.get(global_values, "top")
global_values.put("global", "method")
top_method = global_values.get("global")

probe(map<string, string> values) =>
    first = values.get("x")
    values.put("x", "y")
    removed = values.remove("x")
    key_list = values.keys()
    value_list = values.values()
    copied = values.copy()
    values.clear()
    local_values = map.new<string, string>()
    local_values.put("local", "value")
    local_read = local_values.get("local")
    first == removed and local_read == "value"

observed = probe(global_values)
"""

_KEYWORD_RESIDUAL_SOURCES = {
    "clear": """//@version=6
strategy("Map keyword residual clear")
f(map<string, int> m) =>
    map.clear(id=m)
observed = f(map.new<string, int>())
""",
    "put_all": """//@version=6
strategy("Map keyword residual put all")
f(map<string, int> target, map<string, int> source) =>
    map.put_all(id=target, from=source)
observed = f(map.new<string, int>(), map.new<string, int>())
""",
}

_MIXED_KEYWORD_RESIDUAL_SOURCES = {
    "put_all": """//@version=6
strategy("Map mixed keyword residual put all")
f(map<string, int> target, map<string, int> source) =>
    map.put_all(target, from=source)
observed = f(map.new<string, int>(), map.new<string, int>())
""",
    "get": """//@version=6
strategy("Map mixed keyword residual get")
f(map<string, string> values) =>
    map.get(values, key="x")
observed = f(map.new<string, string>())
""",
    "global_put_all": """//@version=6
strategy("Map named method keyword residual put all")
var target = map.new<string, int>()
f(map<string, int> source) =>
    target.put_all(id2=source)
observed = f(map.new<string, int>())
""",
    "global_get": """//@version=6
strategy("Map named method keyword residual get")
var values = map.new<string, string>()
f() =>
    values.get(key="x")
observed = f()
""",
}

_INVALID_TERMINAL_EXPRESSIONS = {
    "method_keys_extra_pos": (
        "m.keys(1)",
        "cab58e9c278f14d10dab6de813c3ba4fd91b82d7c28ba0291f4bacfabf1d72b4",
    ),
    "method_keys_unknown_kw": (
        "m.keys(extra=1)",
        "fdfce1ceb98aa0c7c5d22aa526ea32d7ac475e406c7a50a337dbebc29d489e50",
    ),
    "method_get_unknown_kw": (
        'm.get("x", extra=1)',
        "5ca1c09c43fc99b29b734c45b19f4fba49452948ef1fced680dd77143649f3c3",
    ),
    "namespace_keys_extra_pos": (
        "map.keys(m, 1)",
        "fdfce1ceb98aa0c7c5d22aa526ea32d7ac475e406c7a50a337dbebc29d489e50",
    ),
    "namespace_get_extra_pos": (
        'map.get(m, "x", "y")',
        "aadff040891ea04bf99b2e8ec7babfeaa55514f1b4d8634be425d4ba6ea85271",
    ),
    "method_clear_extra_pos": (
        "m.clear(1)",
        "bb649b247b7f1b4507f3a40cc06d3337b70c74d0d8ea30139ba7bfeaf86357e8",
    ),
    "namespace_clear_extra_pos": (
        "map.clear(m, 1)",
        "e9143f691ddbf2c4094e331e2e9163ec1b2fdd704b1a6b501701c8bf02e51e48",
    ),
}

_SHADOWED_UNRESOLVED_PARAM_SOURCE = """//@version=6
strategy("Map param shadows global get")
var values = map.new<string, string>()
f(values) =>
    values.get("x")
observed = f(1)
"""


def _body(cpp: str, signature: str) -> str:
    start = cpp.index(signature)
    end = cpp.index("\n    }", start) + len("\n    }")
    return cpp[start:end]


def test_void_map_terminals_emit_statements_with_default_return():
    cpp = transpile(_TERMINAL_SOURCE)
    for name in (
        "clear_functional",
        "clear_global",
        "clear_local",
        "clear_param",
        "put_all_functional",
        "put_all_global",
        "put_all_local",
        "put_all_param",
        "clear_inferred",
        "put_all_inferred",
    ):
        body = _body(cpp, f"double {name}(")
        assert "return 0.0;" in body
        udf_return_lines = [
            line.strip() for line in body.splitlines()
            if line.startswith("        return ")
        ]
        assert udf_return_lines == ["return 0.0;"]
    assert "m.clear" not in cpp


def test_collection_map_terminals_infer_exact_types_across_forms():
    cpp = transpile(_TERMINAL_SOURCE)
    for name in (
        "keys_functional", "keys_global", "keys_local", "keys_param",
        "keys_inferred",
    ):
        assert f"std::vector<std::string> {name}(" in cpp
    for name in (
        "values_functional",
        "values_global",
        "values_local",
        "values_param",
        "values_inferred",
    ):
        assert f"std::vector<int> {name}(" in cpp
    for name in (
        "copy_functional", "copy_global", "copy_local", "copy_param",
        "copy_inferred",
    ):
        assert f"std::unordered_map<std::string, int> {name}(" in cpp

    for name in ("keys_a", "keys_b", "keys_c", "keys_d", "keys_e"):
        assert f"std::vector<std::string> {name};" in cpp
    for name in ("values_a", "values_b", "values_c", "values_d", "values_e"):
        assert f"std::vector<int> {name};" in cpp
    for name in ("copy_a", "copy_b", "copy_c", "copy_d", "copy_e"):
        assert f"std::unordered_map<std::string, int> {name};" in cpp


def test_string_map_terminals_infer_string_across_forms():
    cpp = transpile(_TERMINAL_SOURCE)
    for name in (
        "get_functional",
        "get_global",
        "get_local",
        "get_param",
        "put_functional",
        "put_global",
        "put_local",
        "put_param",
        "remove_functional",
        "remove_global",
        "remove_local",
        "remove_param",
        "get_inferred",
        "put_inferred",
        "remove_inferred",
    ):
        assert f"std::string {name}(" in cpp
    for suffix in "abcdefghijklmno":
        assert f'std::string string_{suffix} = std::string("");' in cpp


def test_map_terminal_return_forms_compile():
    compile_cpp(transpile(_TERMINAL_SOURCE), label="map_terminal_returns")


def test_nonterminal_map_output_stays_byte_identical():
    cpp = transpile(_NONTERMINAL_SOURCE)
    assert sha256(cpp.encode()).hexdigest() == (
        "d207acf7c4f53761cebd1ef6637380131f140ca5566c00d9436303af80a1cf40"
    )


def test_keyword_only_namespace_map_residuals_fail_closed():
    expected = {
        "clear": "keyword arguments are not supported",
        "put_all": "unknown keyword argument 'from'",
    }
    for name, source in _KEYWORD_RESIDUAL_SOURCES.items():
        with pytest.raises(CompileError, match=expected[name]):
            transpile(source)


def test_mixed_keyword_map_residuals_raise_compile_errors():
    for source in _MIXED_KEYWORD_RESIDUAL_SOURCES.values():
        with pytest.raises(CompileError, match=r"map\."):
            transpile(source)


def test_invalid_terminal_map_shapes_raise_compile_errors():
    for name, (expression, _old_output_hash) in _INVALID_TERMINAL_EXPRESSIONS.items():
        source = f"""//@version=6
strategy("Map invalid terminal {name}")
f(map<string, string> m) =>
    {expression}
observed = f(map.new<string, string>())
"""
        with pytest.raises(CompileError, match=r"map\."):
            transpile(source)


def test_unresolved_parameter_keeps_lexical_precedence_over_global_map():
    cpp = transpile(_SHADOWED_UNRESOLVED_PARAM_SOURCE)
    assert sha256(cpp.encode()).hexdigest() == (
        "331d8306eb6d8868e5a25542cbacedcab77b10a13c18daf8dd523a3e283e196d"
    )

"""Fail-closed diagnostics for duplicate kwargs and malformed map calls."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests.test_map_param_methods import _compile_and_run


def test_duplicate_keyword_argument_is_a_parser_compile_error():
    source = '''//@version=6
strategy("duplicate", overlay=true, overlay=false)
'''

    with pytest.raises(CompileError) as caught:
        transpile(source, filename="duplicate-keyword.pine")

    assert str(caught.value) == (
        "duplicate-keyword.pine:2:37: duplicate keyword argument 'overlay'"
    )
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.PARSER
    assert diagnostic.location.line == 2
    assert diagnostic.location.col == 37
    assert diagnostic.hint == (
        "Remove one 'overlay=' binding; "
        "a keyword argument may be specified only once."
    )


_VALID_EXISTING_FORMS = '''//@version=6
strategy("valid map calls")
var map<string, int> global_values = map.new<string, int>()
var map<string, int> global_source = map.new<string, int>()

typed_keywords(map<string, int> target, map<string, int> source) =>
    target.put(key="a", value=1)
    first = target.get(key="a")
    present = target.contains(key="a")
    removed = target.remove(key="a")
    target.put_all(id2=source)
    target.clear()
    first + removed + (present ? 1 : 0)

map.put(global_source, "source", 2)
map.put(global_values, "global", 3)
global_read = map.get(global_values, "global")
global_has = map.contains(global_values, "global")
global_size = map.size(global_values)
global_keys = map.keys(global_values)
global_vals = map.values(global_values)
global_copy = map.copy(global_values)
map.put_all(global_values, global_source)
local_read = global_values.get("global")
observed = typed_keywords(global_values, global_source)
'''


def test_valid_existing_positional_and_typed_keyword_forms_do_not_drift():
    cpp = transpile(_VALID_EXISTING_FORMS)
    assert sha256(cpp.encode()).hexdigest() == (
        "8a7b2f7c4d5d28cbe220677d56b646dd7c75561c504e6eec549de8af961d99db"
    )


@pytest.mark.parametrize(
    ("source", "expected_hash"),
    [
        (
            '''//@version=6
strategy("map parameter shadow")
probe(map<string, int> map) =>
    map.get("key")
observed = probe(map.new<string, int>())
''',
            "a5e0a9ccdddd7e4f540f1598e010b06d65fe3f191a8c750645c012825bdbb44b",
        ),
        (
            '''//@version=6
strategy("local map shadow")
probe() =>
    map = map.new<string, int>()
    map.put("key", 1)
    map.get("key")
observed = probe()
''',
            "b1e0e923dbf0b6a5097b0f6aa2345cda5d13ab6f691a8b7f31a22d0006bc20e7",
        ),
        (
            '''//@version=6
strategy("global map shadow")
map<string, int> map = map.new<string, int>()
map.put("key", 1)
observed = map.get("key")
''',
            "480401819f2dfc7352682e9142612cd6fccec5a049a5f5a0c8bb21d83cbf03c0",
        ),
    ],
)
def test_lexical_identifier_named_map_remains_a_receiver(
    source: str,
    expected_hash: str,
):
    cpp = transpile(source)
    assert sha256(cpp.encode()).hexdigest() == expected_hash


_LATER_GLOBAL_MAP_SOURCE = '''//@version=6
strategy("source order map")
target = map.new<string, int>()
map.put(target, "before", 3)
before = map.get(target, "before")
map<string, int> map = map.new<string, int>()
map.put("after", 4)
after = map.get("after")
observed = before * 10 + after
'''


_SECURITY_TF_CLONED_MAP_CALL_SOURCE = '''//@version=6
strategy("synthetic map source order")
lookup = map.new<string, string>()
map.put(lookup, "D", "D")
tfInput = input.string("D", "TF")
tf = map.get(lookup, tfInput)
foreign = request.security(syminfo.tickerid, tf, close)
map<string, string> map = map.new<string, string>()
observed = foreign
'''


def test_security_timeframe_clone_keeps_preceding_map_namespace_source_order():
    cpp = transpile(
        _SECURITY_TF_CLONED_MAP_CALL_SOURCE,
        filename="synthetic-map-source-order.pine",
    )

    assert sha256(cpp.encode()).hexdigest() == (
        "43146dde90aa1c34a2ab1258bf744f0d6448c98613a8f0ac50950edd6a9b599c"
    )


def test_security_timeframe_clone_keeps_visible_map_receiver_source_order():
    source = '''//@version=6
strategy("synthetic lexical map source order")
map<string, string> map = map.new<string, string>()
map.put("D", "D")
tfInput = input.string("D", "TF")
tf = map.get(tfInput)
foreign = request.security(syminfo.tickerid, tf, close)
observed = foreign
'''

    cpp = transpile(source, filename="synthetic-lexical-map-source-order.pine")

    assert sha256(cpp.encode()).hexdigest() == (
        "38b9b2f1ce68ddc70523e58e089474dd20a1fa54fc517019c0894850c5999ce3"
    )


_NESTED_LEXICAL_MAP_ROOT_SOURCE = '''//@version=6
strategy("nested map root")
type Holder
    map<string, int> values
Holder map = Holder.new(map.new<string, int>())
map.values.put("key", 7)
observed = map.values.get("key")
'''


_BLOCK_LOCAL_MAP_ISOLATION_SOURCE = '''//@version=6
strategy("block map isolation")
target = map.new<string, int>()
observed = 0
if bar_index >= 0
    map = map.new<string, int>()
    map.put("local", 9)
    observed := map.get("local")
if bar_index >= 0
    map.put(target, "sibling", 2)
    observed := observed * 10 + map.get(target, "sibling")
map.put(target, "outside", 3)
observed := observed * 10 + map.get(target, "outside")
'''


_FOR_BLOCK_LOCAL_MAP_SOURCE = '''//@version=6
strategy("for block map")
observed = 0
for i = 0 to 0
    map = map.new<string, int>()
    map.put("key", 8)
    observed := map.get("key")
'''


@pytest.mark.parametrize(
    ("source", "expected_hash", "expected_value"),
    [
        (
            _LATER_GLOBAL_MAP_SOURCE,
            "eaea8c04c1726540c7fc2143d75a0d62e0c08d35daa105e8633b81f4e5af8e81",
            34.0,
        ),
        (
            _NESTED_LEXICAL_MAP_ROOT_SOURCE,
            "bd4dd2ecd32a1eb751a49a3e1048e9eddb20a3c287df0b2480af872ef52f15b7",
            7.0,
        ),
        (
            _BLOCK_LOCAL_MAP_ISOLATION_SOURCE,
            "b3d1448bbfc615de52d7e630c40c7ecde7a528c1ebd1c246993c04cfac102184",
            923.0,
        ),
        (
            _FOR_BLOCK_LOCAL_MAP_SOURCE,
            "b9178676580d67b4b2e1f198242351e179483fa268dd7bae2d51f2cfcf0a060b",
            8.0,
        ),
    ],
)
def test_map_namespace_resolution_respects_source_order_and_lexical_roots(
    source: str,
    expected_hash: str,
    expected_value: float,
):
    cpp = transpile(source)
    assert sha256(cpp.encode()).hexdigest() == expected_hash
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.observed << "\n";
}
'''
    assert float(_compile_and_run(cpp + driver)) == expected_value


def _typed_method_source(expression: str) -> str:
    return f'''//@version=6
strategy("invalid typed map call")
probe(map<string, int> target, map<string, int> source) =>
    {expression}
observed = probe(map.new<string, int>(), map.new<string, int>())
'''


def _functional_source(expression: str) -> str:
    return f'''//@version=6
strategy("invalid functional map call")
target = map.new<string, int>()
source = map.new<string, int>()
observed = {expression}
'''


def _local_method_source(expression: str) -> str:
    return f'''//@version=6
strategy("invalid local map call")
probe() =>
    target = map.new<string, int>()
    {expression}
observed = probe()
'''


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            _typed_method_source("target.put_all(from=source)"),
            "map.put_all: unknown keyword argument 'from'",
        ),
        (
            _functional_source("map.put_all(id=target, from=source)"),
            "map.put_all: unknown keyword argument 'from'",
        ),
        (
            _typed_method_source('target.get(foo="x")'),
            "map.get: unknown keyword argument 'foo'",
        ),
        (
            _typed_method_source("target.get()"),
            "map.get: missing required argument 'key'",
        ),
        (
            _functional_source("target.get()"),
            "map.get: missing required argument 'key'",
        ),
        (
            _local_method_source('target.get("x", "y")'),
            "map.get: too many positional arguments (expected 1, got 2)",
        ),
        (
            _typed_method_source('target.get("x", key="y")'),
            "map.get: argument 'key' passed both positionally and by keyword",
        ),
        (
            _typed_method_source('target.get("x", "y")'),
            "map.get: too many positional arguments (expected 1, got 2)",
        ),
        (
            _functional_source('map.get(id=target, key="x")'),
            (
                "map.get: keyword arguments are not supported "
                "for this functional call form"
            ),
        ),
        (
            _functional_source('map.get(target, key="x")'),
            (
                "map.get: keyword arguments are not supported "
                "for this functional call form"
            ),
        ),
        (
            _functional_source('target.get(key="x")'),
            (
                "map.get: keyword arguments are not supported "
                "for this receiver-method call form"
            ),
        ),
    ],
)
def test_invalid_map_bindings_raise_stable_compile_errors(source: str, message: str):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="invalid-map-call.pine")

    assert caught.value.diagnostics[0].phase is Phase.CODEGEN
    assert caught.value.diagnostics[0].message == message
    assert str(caught.value).startswith("invalid-map-call.pine:")

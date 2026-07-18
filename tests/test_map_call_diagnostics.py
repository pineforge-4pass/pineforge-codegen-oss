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
        "c39c46c7044b95e61648bdbd2029df583ff1f06cb6f65cbc44b6dcb4adc7d846"
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
            "518fd354e1a1365d7957f71f69c7356b4cb0704f796c01cdef5e40f8a44d5aed",
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
            "cfc158b435ea5a9eadaf5c054bea35efa24d6dced16491d61fb5897544b24d3f",
        ),
        (
            '''//@version=6
strategy("global map shadow")
map<string, int> map = map.new<string, int>()
map.put("key", 1)
observed = map.get("key")
''',
            "b1b548564467c9161250d34f1f0eeeccda8c27bccb7881f3860452797fde6aff",
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
        "a996c517c7365761c42184cc803d8db66620e6f2517523b4f30a69a64be9c786"
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
        "27a8da4b19d535a5fffe75acb1618ff878f3e2544ef93cc76289402af3f7dbdc"
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
            "a840df36bd97818e0dfc74d9fac484b8ca194d979ee0e120c091f9ad53c9ea32",
            34.0,
        ),
        (
            _NESTED_LEXICAL_MAP_ROOT_SOURCE,
            "f8ae41ee93773775d6e1008f9890593271c71815a14681ed047787644bea2a48",
            7.0,
        ),
        (
            _BLOCK_LOCAL_MAP_ISOLATION_SOURCE,
            "5324c737eafc26029b64442ea245e7ef576c37353f18716c265f7e1fcb5b427c",
            923.0,
        ),
        (
            _FOR_BLOCK_LOCAL_MAP_SOURCE,
            "a68ce941c1d1d32ccbbc99dc81720c44e645844ab136eace2dcfa2f0bffa7e5e",
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

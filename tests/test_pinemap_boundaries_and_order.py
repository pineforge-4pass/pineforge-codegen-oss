"""Declared PineMap boundaries and cross-call evaluation-order regressions."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests.test_pinemap_semantics import _compile_and_run


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            '''//@version=6
strategy("typed map key boundary")
var map<int, float> values = na
''',
            "map keys must be string",
        ),
        (
            '''//@version=6
strategy("typed map value boundary")
type Payload
    int value
var map<string, Payload> values = na
''',
            "map values must be primitive",
        ),
        (
            '''//@version=6
strategy("UDT field map boundary")
type Payload
    int value
type Holder
    map<string, Payload> values
var Holder holder = Holder.new(na)
''',
            "map values must be primitive",
        ),
        (
            '''//@version=6
strategy("UDF map parameter boundary")
type Payload
    int value
unused(map<string, Payload> values) => 0
observed = 0
''',
            "map values must be primitive",
        ),
        (
            '''//@version=6
strategy("method map parameter boundary")
type Payload
    int value
method unused(Payload self, map<int, float> values) => 0
observed = 0
''',
            "map keys must be string",
        ),
        (
            '''//@version=6
strategy("UDF matrix map-bearing boundary")
type Holder
    map<string, int> values
unused(matrix<Holder> values) => 0
observed = 0
''',
            "matrix<Holder> is not supported when the UDT contains a map field",
        ),
        (
            '''//@version=6
strategy("method matrix map-bearing boundary")
type Holder
    map<string, int> values
method unused(Holder self, matrix<Holder> values) => 0
observed = 0
''',
            "matrix<Holder> is not supported when the UDT contains a map field",
        ),
    ],
)
def test_declared_map_and_matrix_boundaries_fail_closed(
    source: str, message: str
) -> None:
    with pytest.raises(CompileError, match=message):
        transpile(source)


def test_valid_declared_primitive_maps_remain_supported() -> None:
    source = '''//@version=6
strategy("valid declared primitive maps")
type Holder
    map<string, int> values
identity(map<string, int> values) => values
method read(Holder self, map<string, bool> flags) => flags.size()
var map<string, string> names = na
var Holder holder = Holder.new(map.new<string, int>())
var map<string, int> alias = identity(holder.values)
observed = holder.read(map.new<string, bool>())
'''
    cpp = transpile(source)
    assert "PineMap<std::string, int>" in cpp
    assert "PineMap<std::string, bool>" in cpp


_ORDERED_CALL_SOURCE = '''//@version=6
strategy("PineMap generic call order")
observe(int first, int second) => 0
mutate(map<string, int> target, int value) => target.put("x", value)
type Holder
    int marker
method observe_method(Holder self, int first, int second) => 0
var direct_target = map.new<string, int>()
var named_target = map.new<string, int>()
var helper_target = map.new<string, int>()
var method_target = map.new<string, int>()
var Holder holder = Holder.new(0)
direct_seen = observe(direct_target.put("x", 1), direct_target.put("x", 2))
named_seen = observe(second=named_target.put("x", 2), first=named_target.put("x", 1))
helper_seen = observe(mutate(helper_target, 1), mutate(helper_target, 2))
method_seen = holder.observe_method(method_target.put("x", 1), method_target.put("x", 2))
'''


def test_user_and_udt_calls_stage_map_effects_in_pine_source_order() -> None:
    cpp = transpile(_ORDERED_CALL_SOURCE)
    assert "__pf_call_arg_" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.direct_target.get("x") != 2) return 3;
    if (strategy.named_target.get("x") != 1) return 4;
    if (strategy.helper_target.get("x") != 2) return 5;
    if (strategy.method_target.get("x") != 2) return 6;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(cpp + driver, label="pinemap-call-order") == "ok\n"


def test_non_map_user_call_remains_exact_baseline_bytes() -> None:
    source = '''//@version=6
strategy("Non-map ordered call baseline")
combine(int a, int b) => a * 10 + b
left = 1
right = 2
observed = combine(left, right)
'''
    cpp = transpile(source)
    assert "__pf_call_arg_" not in cpp
    assert sha256(cpp.encode()).hexdigest() == (
        "1b45f1b7e8a137e6c2257b11660129f38b9d3c2ba37f353020c52685d757b223"
    )

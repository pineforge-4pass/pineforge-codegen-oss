"""Declared PineMap boundaries and cross-call evaluation-order regressions."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env
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
mutate(map<string, int> target, string key, int value) => target.put(key, value)
inferred_mutate(target, string key, int value) => target.put(key, value)
relay(target, string key, int value) => mutate(target, key, value)
type Holder
    int marker
method observe_method(Holder self, int first, int second) => 0
var direct_target = map.new<string, int>()
var named_target = map.new<string, int>()
var helper_target = map.new<string, int>()
var inferred_target = map.new<string, int>()
var transitive_target = map.new<string, int>()
var method_target = map.new<string, int>()
var Holder holder = Holder.new(0)
direct_seen = observe(direct_target.put("first", 1), direct_target.put("second", 2))
named_seen = observe(second=named_target.put("second", 2), first=named_target.put("first", 1))
helper_seen = observe(mutate(helper_target, "first", 1), mutate(helper_target, "second", 2))
inferred_seen = observe(inferred_mutate(inferred_target, "first", 1), inferred_mutate(inferred_target, "second", 2))
transitive_seen = observe(relay(transitive_target, "first", 1), relay(transitive_target, "second", 2))
method_seen = holder.observe_method(method_target.put("first", 1), method_target.put("second", 2))
'''


_TEMPORARY_UDT_RECEIVER_SOURCE = '''//@version=6
strategy("temporary UDT method receiver")
type H
    map<string, int> data
method get(H self) => self.data
method apply(H self, int value) =>
    self.data.put("method", value)
    self.data
receiver_map(array<int> order, map<string, int> root) =>
    order.push(1)
    root
next_value(array<int> order) =>
    order.push(2)
    7
var order = array.new<int>()
var root = map.new<string, int>()
direct_previous = H.new(root).get().put("direct", 1)
ordered_previous = H.new(receiver_map(order, root)).apply(next_value(order)).put("after", 9)
'''


def test_user_and_udt_calls_stage_map_effects_in_pine_source_order() -> None:
    cpp = transpile(_ORDERED_CALL_SOURCE)
    for target in (
        "direct_seen",
        "named_seen",
        "helper_seen",
        "inferred_seen",
        "transitive_seen",
        "method_seen",
    ):
        assignment = next(
            line
            for line in cpp.splitlines()
            if line.strip().startswith(f"{target} =")
        )
        assert "__pf_call_arg_" in assignment, target
        assert assignment.count('std::string("first")') == 1, target
        assert assignment.count('std::string("second")') == 1, target
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    const std::vector<std::string> forward{"first", "second"};
    const std::vector<std::string> named{"second", "first"};
    if (strategy.direct_target.keys() != forward) return 3;
    if (strategy.named_target.keys() != named) return 4;
    if (strategy.helper_target.keys() != forward) return 5;
    if (strategy.inferred_target.keys() != forward) return 6;
    if (strategy.transitive_target.keys() != forward) return 7;
    if (strategy.method_target.keys() != forward) return 8;
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


def test_temporary_udt_method_receiver_is_staged_once_and_compiles() -> None:
    cpp = transpile(_TEMPORARY_UDT_RECEIVER_SOURCE)
    direct = next(
        line
        for line in cpp.splitlines()
        if line.strip().startswith("direct_previous =")
    )
    ordered = next(
        line
        for line in cpp.splitlines()
        if line.strip().startswith("ordered_previous =")
    )
    assert "_udt_H_get(__pf_call_arg_" in direct
    assert "_udt_H_get(H{" not in direct
    assert "_udt_H_apply(__pf_call_arg_" in ordered
    assert ordered.count("receiver_map(order, root)") == 1
    assert ordered.count("next_value(order)") == 1
    compile_env.compile_cpp(cpp, label="pinemap-temporary-udt-receiver")


def test_temporary_udt_method_receiver_preserves_receiver_first_runtime() -> None:
    cpp = transpile(_TEMPORARY_UDT_RECEIVER_SOURCE)
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.order != std::vector<int>{1, 2}) return 3;
    if (strategy.root.get("direct") != 1) return 4;
    if (strategy.root.get("method") != 7) return 5;
    if (strategy.root.get("after") != 9) return 6;
    if (!is_na(strategy.direct_previous)
            || !is_na(strategy.ordered_previous)) return 7;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label="pinemap-temporary-udt-receiver-runtime",
    ) == "ok\n"

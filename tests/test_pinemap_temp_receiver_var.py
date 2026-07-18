"""Persistent map results returned from temporary UDT method receivers."""

from __future__ import annotations

from pineforge_codegen import transpile
from tests.test_pinemap_semantics import _compile_and_run


_SOURCE = '''//@version=6
strategy("temporary UDT receiver persistent map")
type Holder
    map<string, int> values
method get(Holder self) => self.values
var base = map.new<string, int>()
var inferred = Holder.new(base).get()
var map<string, int> typed_result = Holder.new(base).get()
inferred.put("inferred", 11)
typed_result.put("typed", 22)
observed = Holder.new(base).get().get("inferred")
'''


def test_temporary_udt_receiver_map_results_use_exact_persistent_type() -> None:
    cpp = transpile(_SOURCE)
    assert "PineMap<std::string, int> inferred;" in cpp
    assert "PineMap<std::string, int> typed_result;" in cpp
    assert "Holder inferred;" not in cpp
    assert "Holder typed_result;" not in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.base.get("inferred") != 11) return 3;
    if (strategy.base.get("typed") != 22) return 4;
    if (strategy.inferred.get("typed") != 22) return 5;
    if (strategy.typed_result.get("inferred") != 11) return 6;
    if (strategy.observed != 11) return 7;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver, label="pinemap-temp-receiver-var"
    ) == "ok\n"

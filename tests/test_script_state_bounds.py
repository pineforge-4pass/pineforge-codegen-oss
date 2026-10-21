"""Compiler evidence for A5; a recommendation is not a storage migration.

Purpose: expose sound cardinality/inline-byte proofs in the generated artifact.
Failure shape: growing state is unreported, or a default/handle is called a bound.
Check: inspect the emitted executable metadata and compile it with the engine.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import _generate
from tests._compile import compile_cpp
from tests.test_pinemap_semantics import _compile_and_run


def _generated(body: str, *, settings: str = ""):
    source = f'//@version=6\nstrategy("State bounds"{settings})\n{body}\n'
    gen, _ctx, cpp, _diagnostics, _sites = _generate(source, True, "bounds.pine")
    assert "strategy_script_state_bounds_v1" in cpp
    return gen.script_state_bounds, cpp


def _member(bounds, name):
    return next(row for row in bounds if row["member"] == name)


@pytest.mark.parametrize("method", ["values.push(close)", "array.push(values, close)"])
def test_append_per_bar_requires_external_storage(method):
    bounds, cpp = _generated(f"var values = array.new<float>()\n{method}")
    # Pin the actual old representation and mutation, not a source comment.
    assert "std::vector<double> values;" in cpp
    assert "push_back(" in cpp
    row = _member(bounds, "values")
    assert row["cardinality_bound"] is None
    assert row["storage_requirement"] == "external"
    assert row["current_storage"] == "resident"
    assert row["element_bytes_cpp"] == "sizeof(double)"


@pytest.mark.parametrize("size", [0, 3, 19])
def test_constant_array_has_preallocation_metadata(size):
    bounds, cpp = _generated(
        f"N = {size}\nvar values = array.new<float>(N, na)\n"
        "if values.size() > 0\n    values.set(0, close)"
    )
    row = _member(bounds, "values")
    assert row["cardinality_bound"] == size
    assert row["storage_requirement"] == "preallocate"
    assert row["element_bytes_cpp"] == "sizeof(double)"
    assert f'{{"values", "array", true, {size}ULL, true, sizeof(double),' in cpp


@pytest.mark.parametrize(
    "body",
    [
        "var values = array.new<float>(7)\nvalues.push(close)",
        "var values = array.new<float>(7)\nvalues.unshift(close)",
        "var values = array.new<float>(7)\nvalues.insert(0, close)",
        "var values = array.new<float>(7)\nvalues := array.new<float>(bar_index)",
        "n = input.int(7)\nvar values = array.new<float>(n)",
        "var values = array.new<float>(7)\nf(array<float> x) =>\n    x.push(close)\nf(values)",
        "var values = array.new<float>(7)\nother = values\nother.push(close)",
    ],
)
def test_initial_size_is_not_a_lifetime_bound(body):
    bounds, _cpp = _generated(body)
    assert _member(bounds, "values")["cardinality_bound"] is None
    assert _member(bounds, "values")["storage_requirement"] == "external"


def test_history_horizon_does_not_bound_array_payload():
    bounds, cpp = _generated(
        "var values = array.new<float>()\nvalues.push(close)\n"
        "old_size = array.size(values[1])",
        settings=", max_bars_back = 29",
    )
    assert _member(bounds, "values")["storage_requirement"] == "external"
    history = next(row for row in bounds if row["kind"] == "collection_history")
    assert history["cardinality_bound"] == 2
    assert history["element_bytes_cpp"] is None
    assert history["storage_requirement"] == "external"
    assert "strategy_script_state_bounds_v1" in cpp


def test_inline_string_object_size_does_not_bound_string_elements():
    bounds, _cpp = _generated('var values = array.new<string>(4, "x")')
    row = _member(bounds, "values")
    assert row["cardinality_bound"] == 4
    assert row["element_bytes_cpp"] is None
    assert row["storage_requirement"] == "external"


def test_scalar_history_publishes_its_explicit_emitted_horizon():
    bounds, _cpp = _generated("previous = close[2]", settings=", max_bars_back = 31")
    row = _member(bounds, "_s_close")
    assert row["cardinality_bound"] == 31
    assert row["element_bytes_cpp"] == "sizeof(double)"
    assert row["storage_requirement"] == "preallocate"


def test_fixed_matrix_shape_and_mutation_of_elements():
    bounds, _cpp = _generated(
        "var values = matrix.new<float>(3, 5, na)\nvalues.set(0, 0, close)"
    )
    row = _member(bounds, "values")
    assert row["cardinality_bound"] == 15
    assert row["element_bytes_cpp"] == "sizeof(double)"
    assert row["storage_requirement"] == "preallocate"


def test_nested_udt_has_no_object_graph_bound():
    bounds, _cpp = _generated(
        "type Bucket\n    array<float> xs\n    string text\n"
        'var xs = array.new<float>()\nxs.push(close)\nobj = Bucket.new(xs, "x")'
    )
    arenas = [row for row in bounds if row["kind"] == "udt_arena"]
    assert arenas
    assert all(row["cardinality_bound"] is None for row in arenas)
    assert all(row["storage_requirement"] == "external" for row in arenas)
    checkpoint = _member(bounds, "_pf_script_state_checkpoint_")
    assert checkpoint["storage_requirement"] == "external"


def test_proofs_are_emitted_as_compilable_metadata():
    _bounds, cpp = _generated(
        "var fixed_values = array.new<float>(11)\n"
        "var growing_values = array.new<float>()\ngrowing_values.push(close)"
    )
    assert "sizeof(double)" in cpp
    driver = r'''
#include <cstring>
#include <iostream>
int main() {
    size_t count = 0;
    const auto* bounds = strategy_script_state_bounds_v1(&count);
    size_t second_count = 0;
    if (strategy_script_state_bounds_v1(&second_count) != bounds) return 1;
    if (count != second_count) return 2;
    bool fixed = false, growing = false;
    for (size_t i = 0; i < count; ++i) {
        const auto& row = bounds[i];
        if (std::strcmp(row.member, "fixed_values") == 0) {
            fixed = row.cardinality_known && row.cardinality_bound == 11
                && row.element_bytes_known
                && row.element_bytes == sizeof(decltype(GeneratedStrategy::fixed_values)::value_type)
                && std::strcmp(row.storage_requirement, "preallocate") == 0;
        }
        if (std::strcmp(row.member, "growing_values") == 0) {
            growing = !row.cardinality_known
                && std::strcmp(row.storage_requirement, "external") == 0
                && std::strcmp(row.current_storage, "resident") == 0;
        }
    }
    if (!fixed || !growing) return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(cpp + driver, label="script-state-bound-metadata") == "ok\n"


def test_udt_ids_use_the_arena_index_without_a_narrow_capacity_stop():
    bounds, cpp = _generated("type Item\n    float x\nvar item = Item.new(close)")
    row = next(row for row in bounds if row["kind"] == "udt_arena")
    assert row["element_bytes_cpp"] == "sizeof(_PFUdtRecord_Item)"
    assert row["cardinality_bound"] is None
    assert "UDT object-ID capacity exceeded" not in cpp
    assert "const auto id = _pf_records_.size();" in cpp
    compile_cpp(cpp + '''
static_assert(std::is_same_v<decltype(Item::__pf_id), std::size_t>);
''', label="udt-id-index-width")

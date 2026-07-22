"""Shared matrix-ID lowering and calc-on-order-fills rollback regressions."""

from __future__ import annotations

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests.test_pinemap_semantics import _compile_and_run


_SOURCE = r'''//@version=6
strategy("Matrix identity checkpoint", calc_on_order_fills=true)
type Holder
    matrix<int> grid
var matrix<float> root = matrix.new<float>(1, 1, 1.0)
var matrix<float> alias = root
var matrix<float> independent = root.copy()
var matrix<int> ints = matrix.new<int>(1, 1, 2)
var matrix<int> intsAlias = ints
var Holder holder = Holder.new(ints)
var matrix<Holder> holders = matrix.new<Holder>(1, 1, Holder.new(ints))
'''


def test_matrix_only_scripts_emit_recursive_identity_checkpoints() -> None:
    cpp = transpile(_SOURCE)

    assert "struct _PFCheckpointTraits<PineMatrix>" in cpp
    assert "std::optional<typename matrix_type::Snapshot>" in cpp
    assert "struct _PFCheckpointTraits<PineGenericMatrix<_PFElement>>" in cpp
    assert "typename matrix_type::Snapshot outer;" in cpp
    assert "element_traits::take(value.get(row, column))" in cpp
    assert "element_traits::restore(element, snapshot->elements[index++])" in cpp
    assert "struct _PFCheckpointTraits<_PFUdtRecord_Holder>" in cpp
    assert (
        "struct _PFCheckpointTraits<"
        "_PFUdtArena<Holder, _PFUdtRecord_Holder>>" in cpp
    )
    assert "_PFCheckpointTraits<decltype(GeneratedStrategy::root)>::take(root)" in cpp
    assert "this->root, _pf_script_state_checkpoint_" in cpp
    assert "alias = root;" in cpp
    assert "intsAlias = ints;" in cpp


def test_matrix_identity_checkpoint_source_compiles() -> None:
    compile_env.compile_cpp(
        transpile(_SOURCE), label="matrix-identity-checkpoint"
    )


def test_matrix_identity_checkpoint_restores_alias_graph() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.root.get(0, 0) != 1.0) return 3;
    if (strategy.alias.get(0, 0) != 1.0) return 4;
    if (strategy.ints.get(0, 0) != 2) return 5;

    strategy.snapshot_script_state();

    strategy.alias.set(0, 0, 9.0);
    strategy.root = PineMatrix::new_(1, 1, 7.0);
    strategy.intsAlias.set(0, 0, 8);
    strategy.ints = PineGenericMatrix<int>::new_(1, 1, 6);
    strategy._pf_udt_Holder.get(strategy.holder).grid =
        PineGenericMatrix<int>::new_(1, 1, 5);
    Holder nested = strategy.holders.get(0, 0);
    strategy._pf_udt_Holder.get(nested).grid.set(0, 0, 4);

    strategy.restore_script_state();

    if (strategy.root.get(0, 0) != 1.0) return 6;
    if (strategy.alias.get(0, 0) != 1.0) return 7;
    if (strategy.ints.get(0, 0) != 2) return 8;
    if (strategy.intsAlias.get(0, 0) != 2) return 9;
    if (strategy._pf_udt_Holder.get(strategy.holder).grid.get(0, 0) != 2) return 10;
    if (strategy._pf_udt_Holder.get(strategy.holders.get(0, 0)).grid.get(0, 0) != 2) return 11;

    strategy.root.set(0, 0, 3.0);
    if (strategy.alias.get(0, 0) != 3.0) return 12;
    strategy.ints.set(0, 0, 13);
    if (strategy.intsAlias.get(0, 0) != 13) return 13;
    if (strategy.independent.get(0, 0) != 1.0) return 14;

    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_SOURCE) + driver,
        label="matrix-identity-checkpoint-runtime",
    ) == "ok\n"

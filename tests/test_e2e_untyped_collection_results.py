"""An untyped variable bound to a call that returns an array is an array.

``r = m.row(0)`` declared ``r`` as ``std::vector<double>``, but the
namespace forms ``matrix.row(m, 0)``, ``matrix.col`` and
``matrix.eigenvalues``, and both forms of ``array.abs``,
``array.standardize`` and ``array.sort_indices``, fell through
``_type_spec_from_expr`` to the scalar default: ``double r`` then took a
vector and the TU did not compile (lane W2's open findings: matrix.row).
Each now yields an array spec -- a row or column of the matrix's element
type, eigenvalues and the new arrays of the array methods as the vectors of
doubles their templates build.

TradingView's tape of ``fixtures/open_items_tv/untyped_collections`` (lab tv
--no-note) spells a read of each binding on every exit bar; the replay
compares every exit comment. Each shape also compiles bound to a global and
to a function local.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


def test_the_untyped_collection_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    feed = window_feed(engine, tmp_path)
    runs = execute_all(engine, feed, tmp_path, {
        "untyped_collections": Build((FIXTURES / "untyped_collections.pine").read_text())})
    ok(runs, "untyped_collections")
    trades = tape("untyped_collections")
    assert len(trades) == 7
    misses = exit_misses(engine, tmp_path / "untyped_collections", feed, trades)
    assert not misses, misses[:3]


SETUP = ("m = matrix.new<float>(2, 2, 1.0)\n"
         "a = array.from(3.0, -1.0, 2.0, close)\n")
CALLS = {
    "matrix_row": "matrix.row(m, 1)",
    "matrix_col": "matrix.col(m, 0)",
    "matrix_eigenvalues": "matrix.eigenvalues(m)",
    "array_abs": "array.abs(a)",
    "array_abs_method": "a.abs()",
    "array_standardize": "array.standardize(a)",
    "array_standardize_method": "a.standardize()",
    "array_sort_indices": "array.sort_indices(a)",
    "array_sort_indices_method": "a.sort_indices()",
}


def _script(call: str, local: bool) -> str:
    head = '//@version=6\nstrategy("untyped collection", overlay = true)\n'
    if local:
        body = ("f() =>\n" + "".join(f"    {line}\n" for line in SETUP.splitlines())
                + f"    r = {call}\n    array.sum(r)\nv = f()\n")
    else:
        body = SETUP + f"r = {call}\nv = array.sum(r)\n"
    return head + body + "if v > 1\n    strategy.entry(\"L\", strategy.long)\n"


@pytest.mark.parametrize("local", [False, True], ids=["global", "local"])
@pytest.mark.parametrize("shape", sorted(CALLS))
def test_an_untyped_array_result_is_declared_an_array(shape, local):
    cpp = transpile(_script(CALLS[shape], local))
    assert "std::vector<double> r" in cpp
    assert "double r = 0.0;" not in cpp
    compile_env.compile_cpp(cpp, label=f"untyped {shape}")

"""Numeric matrix sum overloads agree with the advertised support surface."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen.support_checker import SUPPORTED_MATRIX
from tests._compile import compile_cpp
from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env, trade_count


HEADER = '//@version=6\nstrategy("matrix overloads", overlay=true)\n'
MATRICES = 'm1 = matrix.new<float>(2, 2, 1.0)\nm2 = matrix.new<float>(2, 2, 2.0)\n'


def witness(body: str, condition: str) -> str:
    return HEADER + body + f"\nvalid = {condition}\n" + """
if bar_index == 3 and valid
    strategy.entry("L", strategy.long)
if bar_index == 5
    strategy.close_all()
// @pf-trace valid=valid
"""


SUM_EXPRESSIONS = {
    "matrix_untyped": "matrix.sum(m1, m2)",
    "scalar_float": "matrix.sum(m1, 2.0)",
    "scalar_int": "matrix.sum(m1, 2)",
    "method_matrix": "m1.sum(m2)",
    "method_scalar": "m1.sum(2.0)",
    "mixed_keywords": "matrix.sum(m1, id2=m2)",
    "all_keywords": "matrix.sum(id1=m1, id2=m2)",
    "method_keywords": "m1.sum(id2=m2)",
    "nested": "matrix.sum(matrix.sum(m1, 1.0), 1.0)",
    "fresh_receiver": "matrix.sum(matrix.new<float>(2, 2, 1.0), m2)",
    "copy_receiver": "matrix.sum(matrix.copy(m1), m2)",
    "selection": "bar_index % 2 == 0 ? matrix.sum(m1, m2) : matrix.sum(m2, 1.0)",
}

SUCCESS = {
    name: witness(MATRICES + f"result = {expression}\n", "result.get(0, 0) == 3.0 and result.get(1, 1) == 3.0")
    for name, expression in SUM_EXPRESSIONS.items()
}
SUCCESS.update({
    "matrix_typed": witness(MATRICES + "matrix<float> result = matrix.sum(m1, m2)\n", "result.get(0, 0) == 3.0"),
    "aggregate_control": witness(MATRICES + "result = matrix.sum(m1)\n", "result == 4.0"),
    "method_aggregate_control": witness(MATRICES + "result = m1.sum()\n", "result == 4.0"),
    "detached_result": witness(MATRICES + "result = matrix.sum(m1, m2)\nresult.set(0, 0, 9.0)\n", "result.get(0, 0) == 9.0 and result.get(1, 1) == 3.0 and m1.get(0, 0) == 1.0 and m2.get(0, 0) == 2.0"),
    "namespace_helper": witness("combine(matrix<float> left, matrix<float> right) => matrix.sum(left, right)\n" + MATRICES + "result = combine(m1, m2)\n", "result.get(0, 0) == 3.0"),
    "method_helper": witness("combine(matrix<float> left, matrix<float> right) => left.sum(right)\n" + MATRICES + "result = combine(m1, m2)\n", "result.get(0, 0) == 3.0"),
    "block_local": witness(MATRICES + "bool valid_inner = false\nif bar_index >= 0\n    result = matrix.sum(m1, m2)\n    valid_inner := result.get(0, 0) == 3.0\n", "valid_inner"),
    "empty_matrix": witness("m1 = matrix.new<float>()\nm2 = matrix.new<float>()\nresult = matrix.sum(m1, m2)\n", "not na(result) and result.rows() == 0 and result.columns() == 0"),
})

for method, expected in (("diff", "-1.0"), ("mult", "2.0")):
    for form, expression in (
        ("namespace", f"matrix.{method}(m1, 2.0)"),
        ("method", f"m1.{method}(2.0)"),
        ("integer", f"matrix.{method}(m1, 2)"),
        ("keywords", f"matrix.{method}(m1, id2=2.0)"),
        ("all_keywords", f"matrix.{method}(id1=m1, id2=2.0)"),
    ):
        SUCCESS[f"{method}_scalar_{form}"] = witness(
            MATRICES + f"result = {expression}\n",
            f"result.get(0, 0) == {expected} and m1.get(0, 0) == 1.0",
        )
SUCCESS["diff_matrix_control"] = witness(MATRICES + "result = matrix.diff(m1, m2)\n", "result.get(0, 0) == -1.0")
SUCCESS["mult_matrix_control"] = witness(MATRICES + "result = matrix.mult(m1, m2)\n", "result.get(0, 0) == 4.0")
SUCCESS["chained_sum"] = witness(MATRICES, "matrix.sum(m1, m2).get(0, 0) == 3.0")
SUCCESS["history_sum"] = witness(MATRICES + "result = bar_index > 0 ? matrix.sum(m1[1], m2) : matrix.sum(m1, m2)\n", "result.get(0, 0) == 3.0")
SUCCESS["history_method_sum"] = witness(MATRICES + "result = bar_index > 0 ? (m1[1]).sum(m2) : matrix.sum(m1, m2)\n", "result.get(0, 0) == 3.0")
SUCCESS["diff_scalar_field"] = witness(MATRICES + "result = matrix.diff(m1, close)\n", "result.get(0, 0) == 1.0 - close")
SUCCESS["mult_scalar_field"] = witness(MATRICES + "result = matrix.mult(m1, close)\n", "result.get(0, 0) == close")

OPTIONAL = {}
for method, dimension, other in (("add_row", "rows", "columns"), ("add_col", "columns", "rows")):
    for form, arguments in (("default", ""), ("index", "0")):
        OPTIONAL[f"{method}_{form}"] = witness(MATRICES + f"m1.{method}({arguments})\n", f"m1.{dimension}() == 3 and m1.{other}() == 2 and na(m1.get({'0, 0' if form == 'index' else '2, 0' if method == 'add_row' else '0, 2'}))")
OPTIONAL["submatrix_default"] = witness(MATRICES + "result = matrix.submatrix(m1)\n", "result.rows() == 2 and result.columns() == 2 and result.get(0, 0) == 1.0")
OPTIONAL["submatrix_partial"] = witness(MATRICES + "result = matrix.submatrix(m1, 0, 1)\n", "result.rows() == 1 and result.columns() == 2 and result.get(0, 0) == 1.0")
OPTIONAL["sort_default"] = witness("m1 = matrix.new<float>(2, 1, 2.0)\nm1.set(1, 0, 1.0)\nmatrix.sort(m1)\n", "m1.get(0, 0) == 1.0 and m1.get(1, 0) == 2.0")
OPTIONAL["array_fill_start"] = witness("values = array.from(1.0, 2.0, 3.0)\narray.fill(values, 7.0, 1)\n", "values.get(0) == 1.0 and values.get(1) == 7.0 and values.get(2) == 7.0")
OPTIONAL["array_concat_statement_control"] = witness("values = array.from(1.0, 2.0)\nother = array.from(3.0)\narray.concat(values, other)\n", "values.size() == 3 and values.get(2) == 3.0")

ERRORS = {
    "dimension_mismatch": (HEADER + "m1 = matrix.new<float>(1, 1, 1.0)\nm2 = matrix.new<float>(2, 1, 2.0)\nresult = matrix.sum(m1, m2)\n", "Cannot sum matrices with different dimensions."),
    "na_left": (HEADER + "matrix<float> m1 = na\nm2 = matrix.new<float>(1, 1, 2.0)\nresult = matrix.sum(m1, m2)\n", "matrix operation on na ID"),
    "na_right": (HEADER + "m1 = matrix.new<float>(1, 1, 1.0)\nmatrix<float> m2 = na\nresult = matrix.sum(m1, m2)\n", "matrix operation on na ID"),
    "missing_history_sum": (HEADER + MATRICES + "result = matrix.sum(m1[1], m2)\n", "Cannot call matrix methods when id of matrix is na."),
}


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_matrix_sum_shapes_transpile(name):
    assert "sum" in SUPPORTED_MATRIX
    cpp = transpile(SUCCESS[name])
    if "aggregate_control" in name:
        assert "_pf_sum_result" not in cpp
    elif "matrix_control" in name:
        assert f"m1.{name.split('_')[0]}(m2)" in cpp
    elif name.startswith(("diff_scalar", "mult_scalar")):
        assert "_pf_matrix_result.data()" in cpp
    else:
        assert "_pf_sum_result.data()" in cpp


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_matrix_sum_shapes_compile(name):
    compile_cpp(transpile(SUCCESS[name]), label=name)


@pytest.mark.parametrize("name", sorted(ERRORS))
def test_matrix_sum_runtime_errors_compile(name):
    compile_cpp(transpile(ERRORS[name][0]), label=name)


def test_matrix_sum_overloads_run(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 24)
    sources = {**SUCCESS, **OPTIONAL}
    builds = {name: Build(source, trace=True) for name, source in sources.items()}
    runs = execute_all(engine, feed, tmp_path, builds)
    for name in sources:
        run = ok(runs, name)
        assert trade_count(run.trades["default"]) == 1, name
        values = [record["value"] for record in run.traces["default"] if record["name"] == "valid"]
        assert values == [1] * 24, name


def test_matrix_sum_invalid_inputs_raise_without_signals(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 4)
    runs = execute_all(engine, feed, tmp_path, {name: Build(source) for name, (source, _) in ERRORS.items()})
    for name, (_, message) in ERRORS.items():
        assert runs[name].error is not None and message in runs[name].error, name
        assert "-11" not in runs[name].error, name


@pytest.mark.parametrize("expression", ["matrix.mult(m1, values)", "m1.mult(values)"])
def test_matrix_vector_overload_uses_existing_unsupported_diagnostic(expression):
    source = HEADER + MATRICES + f"values = array.from(1.0, 2.0)\nresult = {expression}\n"
    with pytest.raises(CompileError, match=r"matrix\.mult\(\.\.\.\) is not implemented in PineForge runtime\.") as error:
        transpile(source)
    assert any(diagnostic.code for diagnostic in error.value.diagnostics)


@pytest.mark.parametrize("name", sorted(OPTIONAL))
def test_optional_overloads_transpile_and_compile(name):
    compile_cpp(transpile(OPTIONAL[name]), label=name)


def test_array_concat_bound_result_uses_existing_unsupported_diagnostic():
    source = HEADER + "values = array.from(1.0)\nother = array.from(2.0)\nresult = array.concat(values, other)\n"
    with pytest.raises(CompileError, match=r"array\.concat\(\.\.\.\) is not implemented in PineForge runtime\.") as error:
        transpile(source)
    assert any(diagnostic.code for diagnostic in error.value.diagnostics)

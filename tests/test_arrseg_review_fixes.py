"""Collection keywords bind by position; array history at a callable boundary.

Keyword arguments of the matrix methods and the checked array methods bind to
their own parameter slot, the omitted optional parameters taking their
defaults in place; a keyword the lowering does not consume is refused with the
diagnostic the namespace already reports for a call that does not bind. An
array history that reaches a user function's or method's parameter stays the
copy it was (an empty array before history exists), as before the nullable
history values: the parameter is a ``std::vector&`` that holds no na array.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.collection_history import NA_MATRIX_MESSAGE
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env, trade_count
from tests.test_matrix_arithmetic_overloads import HEADER, witness


# Rows [10, 1] and [20, 0].
MATRIX = """m1 = matrix.new<float>(2, 2, 0.0)
m1.set(0, 0, 10.0)
m1.set(1, 0, 20.0)
m1.set(0, 1, 1.0)
"""

# Four rows whose first-column order differs under all four sorts:
# column 0 ascending 1,2,3,4; descending 4,3,2,1; column 1 ascending 2,4,1,3;
# descending 3,1,4,2.
ROWS = """m1 = matrix.new<float>(4, 2, 0.0)
m1.set(0, 0, 1.0)
m1.set(0, 1, 30.0)
m1.set(1, 0, 4.0)
m1.set(1, 1, 20.0)
m1.set(2, 0, 3.0)
m1.set(2, 1, 40.0)
m1.set(3, 0, 2.0)
m1.set(3, 1, 10.0)
"""


def first_column(*values):
    return " and ".join(f"m1.get({row}, 0) == {value}" for row, value in enumerate(values))


# --- P1-1: every keyword binds to its own slot ---------------------------------

SORTS = {
    "sort_default_control": ("matrix.sort(m1)", (1, 2, 3, 4)),
    "sort_column": ("matrix.sort(m1, column = 1)", (2, 4, 1, 3)),
    "sort_order_method": ("m1.sort(order = order.descending)", (4, 3, 2, 1)),
    "sort_ascending_keyword": ("m1.sort(column = 1, order = order.ascending)", (2, 4, 1, 3)),
    "sort_both": ("matrix.sort(m1, column = 1, order = order.descending)", (3, 1, 4, 2)),
    "sort_mixed": ("matrix.sort(m1, 1, order = order.descending)", (3, 1, 4, 2)),
    "sort_reversed": ("matrix.sort(order = order.descending, column = 1, id = m1)", (3, 1, 4, 2)),
}

KEYWORD_CASES = {
    "submatrix_from_row": (
        "result = matrix.submatrix(m1, from_row = 1)",
        "result.rows() == 1 and result.columns() == 2 and result.get(0, 0) == 20 and result.get(0, 1) == 0"),
    "submatrix_to_row": (
        "result = m1.submatrix(to_row = 1)",
        "result.rows() == 1 and result.columns() == 2 and result.get(0, 0) == 10"),
    "submatrix_to_column": (
        "result = m1.submatrix(to_column = 1)",
        "result.rows() == 2 and result.columns() == 1 and result.get(0, 0) == 10 and result.get(1, 0) == 20"),
    "submatrix_mixed": (
        "result = m1.submatrix(1, to_column = 1)",
        "result.rows() == 1 and result.columns() == 1 and result.get(0, 0) == 20"),
    "submatrix_reversed": (
        "result = matrix.submatrix(to_column = 1, from_row = 1, id = m1)",
        "result.rows() == 1 and result.columns() == 1 and result.get(0, 0) == 20"),
    "submatrix_all_keywords": (
        "result = m1.submatrix(from_row = 0, to_row = 1, from_column = 1, to_column = 2)",
        "result.rows() == 1 and result.columns() == 1 and result.get(0, 0) == 1"),
    "insert_row": (
        "m1.add_row(row = 0)",
        "m1.rows() == 3 and na(m1.get(0, 0)) and m1.get(1, 0) == 10 and m1.get(2, 0) == 20"),
    "insert_row_legacy_keyword": (
        "m1.add_row(row_index = 0)",
        "m1.rows() == 3 and na(m1.get(0, 0)) and m1.get(1, 0) == 10 and m1.get(2, 0) == 20"),
    "insert_col": (
        "matrix.add_col(m1, column = 0)",
        "m1.columns() == 3 and na(m1.get(0, 0)) and m1.get(0, 1) == 10"),
    "insert_col_legacy_keyword": (
        "matrix.add_col(m1, col_index = 0)",
        "m1.columns() == 3 and na(m1.get(0, 0)) and m1.get(0, 1) == 10"),
    "append_row_array_keyword": (
        "m1.add_row(array_id = array.from(3.0, 4.0))",
        "m1.rows() == 3 and m1.get(2, 0) == 3 and m1.get(2, 1) == 4"),
    "insert_row_array_keyword": (
        "m1.add_row(row = 1, array_id = array.from(3.0, 4.0))",
        "m1.rows() == 3 and m1.get(1, 0) == 3 and m1.get(2, 0) == 20"),
    "insert_row_mixed": (
        "m1.add_row(0, array_id = array.from(3.0, 4.0))",
        "m1.rows() == 3 and m1.get(0, 0) == 3 and m1.get(1, 0) == 10"),
    "append_col_array_keyword": (
        "matrix.add_col(m1, array_id = array.from(5.0, 6.0))",
        "m1.columns() == 3 and m1.get(0, 2) == 5 and m1.get(1, 2) == 6"),
    "array_fill_end": (
        "a = array.from(1.0, 2.0, 3.0)\na.fill(7.0, index_to = 1)",
        "a.get(0) == 7 and a.get(1) == 2 and a.get(2) == 3"),
    "array_fill_namespace": (
        "a = array.from(1.0, 2.0, 3.0)\narray.fill(id = a, index_to = 1, value = 7.0)",
        "a.get(0) == 7 and a.get(1) == 2 and a.get(2) == 3"),
    "array_fill_mixed": (
        "a = array.from(1.0, 2.0, 3.0)\na.fill(7.0, 1, index_to = 2)",
        "a.get(0) == 1 and a.get(1) == 7 and a.get(2) == 3"),
    "array_fill_start": (
        "a = array.from(1.0, 2.0, 3.0)\na.fill(value = 7.0, index_from = 1)",
        "a.get(0) == 1 and a.get(1) == 7 and a.get(2) == 7"),
}

SUCCESS = {name: witness(ROWS + call + "\n", first_column(*order)) for name, (call, order) in SORTS.items()}
SUCCESS.update({name: witness(("" if "a = " in call else MATRIX) + call + "\n", condition)
                for name, (call, condition) in KEYWORD_CASES.items()})

# --- P2-4: an index-only add_row / add_col takes any numeric index -------------

INDEX_SHAPES = {
    "dynamic": "bar_index % 2",
    "function": "pos()",
    "input": "input.int(0)",
    "builtin": "math.min(1, m1.rows())",
}
for method, index_name, count, access in (
    ("add_row", "row", "rows", "m1.get(position, 0)"),
    ("add_col", "column", "columns", "m1.get(0, position)"),
):
    for shape, expression in INDEX_SHAPES.items():
        for form, argument in (("positional", expression), ("keyword", f"{index_name} = {expression}")):
            SUCCESS[f"{method}_{shape}_{form}"] = witness(
                "pos() => 0\n" + MATRIX + f"position = {expression}\nm1.{method}({argument})\n",
                f"m1.{count}() == 3 and na({access})")

# --- P2-1: a defaulted submatrix evaluates its receiver once --------------------

RECEIVER_ONCE = """var calls = array.new<int>(1, 0)
mk() =>
    calls.set(0, calls.get(0) + 1)
    matrix.new<float>(3, 3, 1.0)
"""
SUCCESS["receiver_once_method"] = witness(
    RECEIVER_ONCE + "matrix<float> result = mk().submatrix()\n",
    "calls.get(0) == bar_index + 1 and result.rows() == 3 and result.columns() == 3")
SUCCESS["receiver_once_namespace"] = witness(
    RECEIVER_ONCE + "result = matrix.submatrix(mk(), 1)\n",
    "calls.get(0) == bar_index + 1 and result.rows() == 2 and result.columns() == 3")
SUCCESS["receiver_once_keyword"] = witness(
    RECEIVER_ONCE + "result = matrix.submatrix(mk(), to_column = 1)\n",
    "calls.get(0) == bar_index + 1 and result.rows() == 3 and result.columns() == 1")

# --- P1-2: an array history given to a callable stays a copy -------------------

CALLEES = {
    "typed": "f(array<float> x) => bar_index > 0 ? x.size() : 0",
    "untyped": "f(x) => bar_index > 0 ? array.size(x) : 0",
    "method": "method cnt(array<float> x) => bar_index > 0 ? x.size() : 0",
}
CALLS = {
    "direct": ("typed", "", "f(a[1])"),
    "direct_untyped": ("untyped", "", "f(a[1])"),
    "bound": ("typed", "pb = a[1]\n", "f(pb)"),
    "bound_untyped": ("untyped", "pb = a[1]\n", "f(pb)"),
    "method": ("method", "pb = a[1]\n", "pb.cnt()"),
    "method_direct": ("method", "", "(a[1]).cnt()"),
}
CALLABLE = {}
for shape, (callee, binding, call) in CALLS.items():
    # A growing var array, as TradingView's history copies it (bar k reads
    # the k elements bar k - 1 left) and a fresh one-element array each bar.
    CALLABLE[f"callable_{shape}"] = witness(
        CALLEES[callee] + "\nvar a = array.new<float>()\na.push(close)\n" + binding + f"result = {call}\n",
        "result == bar_index")
    CALLABLE[f"callable_{shape}_fresh"] = witness(
        CALLEES[callee] + "\na = array.from(close)\n" + binding + f"result = {call}\n",
        "result == (bar_index == 0 ? 0 : 1)")
SUCCESS.update(CALLABLE)

ERRORS = {
    "matrix_bound": (HEADER + MATRIX + "pb = m1[20]\nresult = pb.get(0, 0)\n", NA_MATRIX_MESSAGE),
    "matrix_alias": (HEADER + MATRIX + "pb = m1[20]\nalias = pb\nresult = matrix.get(alias, 0, 0)\n", NA_MATRIX_MESSAGE),
}


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_review_shape_transpiles(name):
    cpp = transpile(SUCCESS[name])
    if name in CALLABLE:
        assert "_pf_collection_hist_a.value(1)" in cpp
        assert "_pf_collection_hist_a.reference(1)" not in cpp
    if name.startswith("receiver_once"):
        assert cpp.count("}(mk())") == 1
        assert "mk().rows()" not in cpp and "mk().columns()" not in cpp


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_review_shape_compiles(name):
    compile_cpp(transpile(SUCCESS[name]), label=name)


# A keyword form lowers to exactly the C++ of its positional twin.
TWINS = [
    ("r = m1.get(row = 0, column = 1)", "r = m1.get(0, 1)"),
    ("r = matrix.get(id = m1, row = 0, column = 1)", "r = matrix.get(m1, 0, 1)"),
    ("m1.set(row = 0, column = 1, value = 5.0)", "m1.set(0, 1, 5.0)"),
    ("matrix.set(m1, 0, 1, value = 5.0)", "matrix.set(m1, 0, 1, 5.0)"),
    ("m1.fill(value = 3.0)", "m1.fill(3.0)"),
    ("r = m1.row(row = 1)", "r = m1.row(1)"),
    ("r = matrix.col(m1, column = 1)", "r = matrix.col(m1, 1)"),
    ("m1.remove_row(row = 1)", "m1.remove_row(1)"),
    ("matrix.remove_col(m1, column = 1)", "matrix.remove_col(m1, 1)"),
    ("m1.swap_rows(row1 = 0, row2 = 1)", "m1.swap_rows(0, 1)"),
    ("matrix.swap_columns(m1, column2 = 1, column1 = 0)", "matrix.swap_columns(m1, 0, 1)"),
    ("m1.reshape(rows = 1, columns = 4)", "m1.reshape(1, 4)"),
    ("r = m1.concat(id2 = m2)", "r = m1.concat(m2)"),
    ("r = matrix.concat(id1 = m1, id2 = m2)", "r = matrix.concat(m1, m2)"),
    ("r = matrix.pow(sq, power = 2)", "r = matrix.pow(sq, 2)"),
    ("r = matrix.kron(id1 = m1, id2 = m2)", "r = matrix.kron(m1, m2)"),
    ("r = m1.diff(id2 = m2)", "r = m1.diff(m2)"),
    ("r = matrix.mult(id1 = m1, id2 = m2)", "r = matrix.mult(m1, m2)"),
    ("m1.add_row(row = 1, array_id = array.from(3.0, 4.0))", "m1.add_row(1, array.from(3.0, 4.0))"),
    ("m1.add_row(row_index = 1, array_id = array.from(3.0, 4.0))", "m1.add_row(1, array.from(3.0, 4.0))"),
    ("matrix.add_col(m1, column = 1, array_id = array.from(3.0, 4.0))",
     "matrix.add_col(m1, 1, array.from(3.0, 4.0))"),
    ("m1.add_row(array_id = array.from(3.0, 4.0))", "m1.add_row(array.from(3.0, 4.0))"),
    ("matrix.add_col(m1, array_id = array.from(5.0, 6.0))", "matrix.add_col(m1, array.from(5.0, 6.0))"),
    ("matrix.fill(m1, 1.0, to_row = 1)", "matrix.fill(m1, 1.0, 0, 1)"),
    ("m1.fill(to_row = 1, value = 2.0)", "m1.fill(2.0)"),
    ("matrix.sort(m1, column = 1, order = order.descending)", "matrix.sort(m1, 1, order.descending)"),
    ("r = m1.submatrix(from_row = 0, to_row = 1, from_column = 1, to_column = 2)", "r = m1.submatrix(0, 1, 1, 2)"),
    ("a.fill(value = 7.0, index_from = 1, index_to = 2)", "a.fill(7.0, 1, 2)"),
    ("array.fill(id = a, value = 7.0, index_from = 1, index_to = 2)", "array.fill(a, 7.0, 1, 2)"),
    ("r = a.get(index = 1)", "r = a.get(1)"),
    ("a.set(value = 4.0, index = 1)", "a.set(1, 4.0)"),
    ("a.insert(index = 1, value = 9.0)", "a.insert(1, 9.0)"),
    ("r = a.remove(index = 1)", "r = a.remove(1)"),
]
TWIN_PRELUDE = HEADER + """m1 = matrix.new<float>(2, 2, 1.0)
m2 = matrix.new<float>(2, 2, 2.0)
sq = matrix.new<float>(2, 2, 1.0)
a = array.from(1.0, 2.0, 3.0)
"""


@pytest.mark.parametrize("keyword,positional", TWINS, ids=[twin[0] for twin in TWINS])
def test_keyword_form_lowers_like_its_positional_twin(keyword, positional):
    assert transpile(TWIN_PRELUDE + keyword + "\n") == transpile(TWIN_PRELUDE + positional + "\n")


@pytest.mark.parametrize("call", [
    "matrix.sort(m1, columm = 1)", "m1.sort(order = order.descending, bogus = 1)",
    "matrix.submatrix(m1, row = 1)", "m1.add_row(index = 0)",
    "matrix.add_col(m1, row = 0)", "matrix.rows(m1, ignored = 1)",
    "matrix.sort(m1, 1, column = 0)", "matrix.fill(m1, 1.0, rows = 1)",
    "matrix.add_row(m1, row = 0, row_index = 1)", "m1.add_row(0, row = 1)",
    "matrix.sort(column = 1)", "m1.set(row = 0, value = 1.0)",
    "m1.get(0, column = 0, row = 1)",
])
def test_unconsumed_matrix_keyword_uses_existing_diagnostic(call):
    with pytest.raises(CompileError, match="wrong number of arguments") as error:
        transpile(HEADER + MATRIX + call + "\n")
    assert all(diagnostic.code for diagnostic in error.value.diagnostics)


@pytest.mark.parametrize("call,message", [
    ("a.fill(7.0, end = 1)", "unknown keyword argument"),
    ("array.fill(a, 7.0, ignored = 1)", "unknown keyword argument"),
    ("a.first(ignored = 1)", "unknown keyword argument"),
    ("a.fill(7.0, 0, index_from = 1)", "passed both positionally and by keyword"),
    ("a.slice(index_to = 1)", "missing required argument"),
])
def test_unconsumed_array_keyword_uses_existing_diagnostic(call, message):
    with pytest.raises(CompileError, match=message) as error:
        transpile(HEADER + "a = array.from(1.0)\n" + call + "\n")
    assert all(diagnostic.code for diagnostic in error.value.diagnostics)


def test_array_methods_outside_the_checked_table_keep_their_keywords_as_before():
    # Pine's ``array.sort(id, order)`` has an ``order`` keyword the lowering
    # does not read: such a call keeps the lowering it had instead of newly
    # failing to transpile.
    source = HEADER + "a = array.from(2.0, 1.0)\narray.sort(a, order = order.descending)\n"
    assert "std::sort" in transpile(source)


@pytest.mark.parametrize("shape", ["direct", "bound", "method", "untyped", "untyped_bound"])
def test_callee_na_preserves_base_refusal(shape):
    definition = ("method cnt(array<float> x)" if shape == "method"
                  else "f(x)" if shape.startswith("untyped") else "f(array<float> x)")
    definition += " => na(x) ? 0 : array.size(x)\n"
    binding = "pb = a[1]\n" if shape in ("bound", "method", "untyped_bound") else ""
    call = {"direct": "f(a[1])", "bound": "f(pb)", "method": "pb.cnt()",
            "untyped": "f(a[1])", "untyped_bound": "f(pb)"}[shape]
    with pytest.raises(CompileError, match="both as an array") as error:
        transpile(HEADER + definition + "a = array.from(close)\n" + binding + f"result = {call}\n")
    assert all(diagnostic.code for diagnostic in error.value.diagnostics)


@pytest.mark.parametrize("binding", [
    "c = a.concat(b)",
    "c = array.concat(a, b)",
    "c = array.new<float>()\nc := array.concat(a, b)",
    "c = array.new<float>()\nc := a.concat(b)",
    "var c = a.concat(b)",
    "array<float> d = na\nc = d.concat(b)",
    "c = array.copy(a).concat(b)",
])
def test_bound_concat_spellings_refuse(binding):
    with pytest.raises(CompileError, match=r"array\.concat\(\.\.\.\) is not implemented") as error:
        transpile(HEADER + "a = array.from(1.0)\nb = array.from(2.0)\n" + binding + "\n")
    assert all(diagnostic.code for diagnostic in error.value.diagnostics)


@pytest.mark.parametrize("statement", [
    "a.concat(b)", "array.concat(a, b)",
    "m3 = m1.concat(m2)", "m3 = matrix.concat(m1, m2)",
])
def test_concat_statement_and_matrix_forms_still_transpile(statement):
    transpile(HEADER + "a = array.from(1.0)\nb = array.from(2.0)\n"
              "m1 = matrix.new<float>(1, 1, 1.0)\nm2 = matrix.new<float>(1, 1, 2.0)\n" + statement + "\n")


@pytest.mark.parametrize("source", [
    # A user method named concat is not the array built-in.
    "method concat(array<float> self, array<float> other) => self.size() + other.size()\n"
    "a = array.from(1.0)\nb = array.from(2.0)\nn = a.concat(b)\n",
    # An element an array function returns is not an array, whatever it holds.
    "ms = array.new<matrix<float>>()\nms.push(matrix.new<float>(1, 1, 1.0))\nm2 = matrix.new<float>(1, 1, 2.0)\n"
    "m3 = array.get(ms, 0)\nc = m3.concat(m2)\n",
    # A parameter named like a global array is that parameter's matrix.
    "a = array.from(1.0)\nf(matrix<float> a, matrix<float> b) =>\n    c = a.concat(b)\n    c.rows()\n"
    "r = f(matrix.new<float>(1, 1, 1.0), matrix.new<float>(1, 1, 2.0))\n",
])
def test_concat_of_something_else_than_an_array_still_transpiles(source):
    transpile(HEADER + source)


def test_index_only_add_row_on_an_untyped_receiver_is_not_an_internal_error():
    source = (HEADER + "tup() => [matrix.new<float>(2, 2, 1.0), 1]\n"
              "[mm, n] = tup()\nmatrix.add_row(mm, bar_index % 2)\n")
    try:
        transpile(source)
    except CompileError:
        pass


def test_untyped_index_or_array_argument_is_told_by_its_cpp_type():
    # ``pos()`` has no resolvable spec at the call: the lambda picks the
    # index or the array form by the argument's C++ type.
    cpp = transpile(HEADER + "pos() => 0\nm1 = matrix.new<float>(2, 2, 1.0)\nm1.add_row(pos())\n")
    assert "if constexpr (std::is_arithmetic_v" in cpp


def test_review_native_values(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 24)
    runs = execute_all(engine, feed, tmp_path, {name: Build(source, trace=True) for name, source in SUCCESS.items()})
    for name in SUCCESS:
        run = ok(runs, name)
        assert trade_count(run.trades["default"]) == 1, name
        values = [record["value"] for record in run.traces["default"] if record["name"] == "valid"]
        assert values == [1] * 24, name


def test_review_bound_na_matrix_errors(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 4)
    runs = execute_all(engine, feed, tmp_path, {name: Build(source) for name, (source, _) in ERRORS.items()})
    for name, (_, message) in ERRORS.items():
        assert runs[name].error is not None and message in runs[name].error, name
        assert "-11" not in runs[name].error, name

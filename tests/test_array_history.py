"""History references on arrays and matrices.

TradingView (``fixtures/array_history_tv``; Pine v6 User Manual, "Arrays":
history referencing): ``a[k]`` on an array or a matrix is a copy of the
collection as the variable left it at the end of its scope's execution k
executions back (the bar k bars back, for a variable of the script's top
level), not the array the variable holds now. The copy is read-only: a change
to it stops the run (RE10051), and a method on it before the variable has a
history stops the run (RE10052 for an array, RE10053 for a matrix). A
``for...in`` loop over ``a[k]`` iterates the array the variable holds now.

The codegen keeps such a variable's own ``std::vector`` or matrix and, beside
it, the copies its bars left (``_PFCollectionHistory``). ``a[1]`` used to
lower to the current array's element 1 and ``m[1]`` to a ``Series<double>``;
neither compiled where an array or a matrix is read.

TradingView refuses a method straight after the history of an array or a
matrix (``a[1].size()``: CE10011), the history of an object's array or
matrix field (``h.xs[1]``, ``(h.xs)[1]``: CE10290), ``==`` on arrays
(CE10123) and an array where a number or a condition is expected (CE10123,
CE10173, CE10101), and so does the transpiler, before generating C++. Forms
TradingView accepts that PineForge does not lower are refused by name: they
never compiled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp

FIXTURES = Path(__file__).parent / "fixtures" / "array_history_tv"

HEADER = (
    "//@version=6\n"
    'strategy("shape", overlay = true, max_lines_count = 500)\n'
)
FOOTER = (
    "if r > 0\n"
    '    strategy.entry("L", strategy.long)\n'
)


def _script(body: str) -> str:
    return HEADER + body + FOOTER


def _refusal(source: str) -> CompileError:
    with pytest.raises(CompileError) as exc:
        transpile(source)
    return exc.value


def _diag(error: CompileError, needle: str):
    found = [d for d in error.diagnostics if needle in d.message]
    assert found, [d.message for d in error.diagnostics]
    return found[0]


# Every spelling below compiles on TradingView (pine-facade, 2026-10-01).
SHAPES = {
    # --- arrays: methods and namespace functions reading the history ---
    "method_size": (
        "a = array.new<float>()\na.push(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := (a[1]).size()\n"),
    "namespace_size": (
        "a = array.new<float>()\na.push(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := array.size(a[1])\n"),
    "get_first_last": (
        "a = array.from(close, open)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (a[1]).get(0) + array.get(a[1], 1) + (a[1]).first() + array.last(a[1])\n"),
    "reductions": (
        "a = array.from(close, open, high)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (a[1]).sum() + (a[1]).avg() + (a[1]).min() + (a[1]).max()"
        " + (a[1]).range() + (a[1]).median() + (a[1]).mode() + (a[1]).stdev()"
        " + (a[1]).variance() + array.stdev(a[1], false)"
        " + (a[1]).percentile_linear_interpolation(50)"
        " + (a[1]).percentile_nearest_rank(50) + (a[1]).percentrank(0)"
        " + (a[1]).abs().sum() + (a[1]).standardize().sum()"
        " + (a[1]).covariance(a) + array.covariance(a, a[1])\n"),
    "searches": (
        "a = array.from(1.0, 2.0, 3.0)\nab = array.from(true, false)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (a[1]).indexof(2.0) + (a[1]).lastindexof(2.0)"
        " + (a[1]).binary_search(2.0) + (a[1]).binary_search_leftmost(2.0)"
        " + (a[1]).binary_search_rightmost(2.0)"
        " + ((a[1]).includes(2.0) ? 1 : 0) + ((ab[1]).every() ? 1 : 0)"
        " + (array.some(ab[1]) ? 1 : 0) + (a[1]).sort_indices().size()\n"),
    "copies": (
        "a = array.from(close, open)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    c = (a[1]).copy()\n    c.push(1.0)\n"
        "    s = array.slice(a[1], 0, 1)\n"
        "    r := c.size() + s.size() + array.copy(a[1]).size() + (a[1]).slice(0, 1).size()\n"),
    "var_array": (
        "var a = array.new<float>()\na.push(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := a.size() - (a[1]).size()\n"),
    "block_local": (
        "float r = 0.0\nif close > open\n    bl = array.from(close)\n"
        "    r := na(bl[1]) ? 0.0 : (bl[1]).get(0)\n"),
    "two_bars_back": (
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 1\n    r := (a[2]).get(0) + array.get(a[2], 0)\n"),
    "dynamic_offset": (
        "a = array.from(close)\nint k = bar_index % 3\n"
        "float r = 0.0\nif bar_index > 3\n    r := (a[k]).get(0)\n"),
    "zero_offset": (
        "a = array.from(close)\n(a[0]).push(open)\nr = (a[0]).size()\n"),
    "for_in": (
        "a = array.from(close)\nfloat r = 0.0\n"
        "for v in a[1]\n    r += v\n"
        "for [i, v] in a[1]\n    r += v + i\n"),
    "na_check": (
        "a = array.from(close)\nr = na(a[1]) ? 0 : 1\n"),
    "bound": (
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 0\n    pb = a[1]\n    r := pb.get(0) + pb.size()\n"),
    "rebound": (
        "a = array.from(close)\narray<float> pb = array.new<float>()\n"
        "if bar_index > 0\n    pb := a[1]\nr = pb.size()\n"),
    "bound_to_its_own_history": (
        "ca = array.from(close)\nif bar_index % 2 == 1\n    ca := ca[1]\n"
        "r = ca.get(0)\n"),
    "bound_history_read": (
        "a = array.from(close)\nb = a[1]\n"
        "float r = 0.0\nif bar_index > 1\n    r := na(b[1]) ? 0.0 : (b[1]).size()\n"),
    "user_function_argument": (
        "firstOf(array<float> x) =>\n    x.get(0) * 10 + x.size()\n"
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := firstOf(a[1])\n"),
    "untyped_function_argument": (
        "sizeOf(x) =>\n    x.size()\n"
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := sizeOf(a[1])\n"),
    "user_method_receiver": (
        "method firstOf(array<float> this) =>\n    this.get(0) + this.size()\n"
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := (a[1]).firstOf()\n"),
    "element_types": (
        "type Cell\n    float v\n"
        "ai = array.from(1, 2)\nast = array.from(\"x\")\nab = array.from(true)\n"
        "ac = array.from(Cell.new(close))\n"
        "al = array.from(line.new(bar_index, close, bar_index + 1, close))\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (ai[1]).get(0) + str.length((ast[1]).get(0))"
        " + ((ab[1]).get(0) ? 1 : 0) + (ac[1]).get(0).v"
        " + line.get_y1((al[1]).get(0)) + str.length((ast[1]).join(\",\"))\n"),
    "read_into_other_collections": (
        "a = array.from(close, open)\nm = matrix.new<float>(0, 2)\n"
        "c = array.new<float>()\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    c.concat(a[1])\n    array.concat(c, a[1])\n"
        "    m.add_row(0, a[1])\n    matrix.add_col(m, 0, array.from(1.0))\n"
        "    r := c.size() + m.rows()\n"),
    # --- matrices ---
    "matrix_method_get": (
        "m = matrix.new<float>(2, 2, close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := (m[1]).get(0, 0)\n"),
    "matrix_namespace_get": (
        "m = matrix.new<float>(2, 2, close)\n"
        "float r = 0.0\nif bar_index > 0\n    r := matrix.get(m[1], 0, 0)\n"),
    "matrix_reads": (
        "m = matrix.new<float>(2, 2, close)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (m[1]).rows() + matrix.columns(m[1]) + (m[1]).elements_count()"
        " + (m[1]).row(0).size() + array.size(matrix.col(m[1], 0))"
        " + (m[1]).avg() + (m[1]).min() + (m[1]).max() + (m[1]).mode()"
        " + (m[1]).det() + matrix.det(m[1]) + (m[1]).trace() + (m[1]).rank()"
        " + (m[1]).eigenvalues().size()"
        " + ((m[1]).is_square() ? 1 : 0) + ((m[1]).is_zero() ? 1 : 0)\n"
        # A built-in's new matrix, bound to a variable (a method on a call's
        # result is a gap of its own, history or not).
        "    c = (m[1]).copy()\n    t = (m[1]).transpose()\n"
        "    s = (m[1]).submatrix(0, 1, 0, 1)\n"
        "    d = (m[1]).diff(m)\n    mu = (m[1]).mult(m)\n    p = (m[1]).pow(2)\n"
        "    k = (m[1]).kron(m)\n    iv = (m[1]).inv()\n    pi = matrix.pinv(m[1])\n"
        "    ev = (m[1]).eigenvectors()\n"
        "    r += c.get(0, 0) + t.get(0, 0) + s.get(0, 0) + d.get(0, 0)"
        " + mu.get(0, 0) + p.get(0, 0) + k.get(0, 0) + iv.rows() + pi.rows() + ev.rows()\n"),
    "matrix_var_and_types": (
        "type Cell\n    float v\n"
        "var vm = matrix.new<float>(1, 1, 0)\nvm.set(0, 0, close)\n"
        "mi = matrix.new<int>(1, 1, 1)\nms = matrix.new<string>(1, 1, \"x\")\n"
        "mc = matrix.new<Cell>(1, 1, Cell.new(close))\n"
        "mb = matrix.new<bool>(1, 1, true)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (vm[1]).get(0, 0) - vm.get(0, 0) + (mi[1]).get(0, 0)"
        " + str.length((ms[1]).get(0, 0)) + (mc[1]).get(0, 0).v"
        " + ((mb[1]).get(0, 0) ? 1 : 0)\n"),
    "matrix_block_local_na_bound": (
        "float r = 0.0\nif close > open\n    bm = matrix.new<float>(1, 1, close)\n"
        "    r := na(bm[1]) ? 0.0 : (bm[1]).get(0, 0)\n"
        "m = matrix.new<float>(1, 1, close)\n"
        "if bar_index > 0\n    pm = m[1]\n    r += pm.get(0, 0) + (na(m[2]) ? 0 : 1)\n"),
    # --- an object's array or matrix field, through the object's history ---
    "object_fields": (
        "type H\n    array<float> xs\n    matrix<float> m\n"
        "h = H.new(array.from(close), matrix.new<float>(1, 1, close))\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (h[1]).xs.size() + array.size((h[1]).xs) + (h[1]).m.get(0, 0)"
        " + matrix.get((h[1]).m, 0, 0) + (h[1]).xs.get(0)\n"),
    # --- a change to the history compiles and stops the run (RE10051) ---
    "array_changes": (
        "a = array.from(close, open)\nfloat r = 0.0\n"
        "if bar_index > 5\n"
        "    (a[1]).push(1.0)\n    array.push(a[1], 2.0)\n    (a[1]).set(0, 1.0)\n"
        "    (a[1]).insert(0, 1.0)\n    (a[1]).unshift(1.0)\n    (a[1]).fill(0.0)\n"
        "    (a[1]).sort()\n    array.sort(a[1], order.descending)\n    (a[1]).reverse()\n"
        "    (a[1]).clear()\n    (a[1]).concat(a)\n"
        "    r := (a[1]).pop() + (a[1]).shift() + (a[1]).remove(0) + array.pop(a[1])\n"),
    "matrix_changes": (
        "m = matrix.new<float>(2, 2, close)\nfloat r = 0.0\n"
        "if bar_index > 5\n"
        "    (m[1]).set(0, 0, 1.0)\n    matrix.set(m[1], 0, 0, 2.0)\n    (m[1]).fill(0.0)\n"
        "    (m[1]).add_row(0, array.from(1.0, 2.0))\n    (m[1]).add_col(0, array.from(1.0, 2.0))\n"
        "    (m[1]).remove_row(0)\n    (m[1]).remove_col(0)\n    (m[1]).swap_rows(0, 1)\n"
        "    (m[1]).swap_columns(0, 1)\n    (m[1]).reshape(1, 4)\n    (m[1]).reverse()\n"
        "    (m[1]).sort(0)\n    (m[1]).concat(m)\n"
        "    r := (m[1]).get(0, 0)\n"),
    "dynamic_offset_change": (
        "a = array.from(close)\nint k = bar_index % 2\n"
        "(a[k]).push(open)\nr = a.size()\n"),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_collection_history_shape_compiles(name):
    compile_cpp(transpile(_script(SHAPES[name])), label=name)


def test_a_history_read_array_keeps_its_vector_and_its_copies():
    cpp = transpile(_script(
        "a = array.new<float>()\na.push(close)\n"
        "var vm = matrix.new<float>(1, 1, 0)\nvm.set(0, 0, close)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    r := (a[1]).size() + (vm[1]).get(0, 0)\n"))
    # The variable is the array it holds, as before; its history is a
    # sequence of copies beside it.
    assert "std::vector<double> a;" in cpp
    assert "PineMatrix vm;" in cpp
    # The history holds copies of the variable's own C++ type.
    assert "_PFCollectionHistory<decltype(a)> _pf_collection_hist_a{2};" in cpp
    assert "_PFCollectionHistory<decltype(vm)> _pf_collection_hist_vm{2};" in cpp
    assert "_pf_collection_hist_a.at(1)" in cpp
    assert "_pf_collection_hist_vm.at(1)" in cpp
    # Each execution of the declaration opens a slot; the bar's end closes
    # it with a copy of the value.
    assert "_pf_collection_hist_a.open(a, history_advances_new_bar());" in cpp
    assert "_pf_collection_hist_a.close(a);" in cpp
    compile_cpp(cpp, label="history members")


def test_a_script_without_collection_history_has_none():
    cpp = transpile(_script(
        "a = array.from(close)\nm = matrix.new<float>(1, 1, close)\n"
        "r = a.size() + m.rows()\n"))
    assert "_PFCollectionHistory" not in cpp
    assert "_pf_collection_hist_" not in cpp


def test_a_for_in_loop_over_the_history_iterates_the_current_array():
    # TradingView's for...in over a[1] reads the array a holds now
    # (fixtures/array_history_tv ahist_loop).
    cpp = transpile(_script(
        "a = array.from(close)\nfloat r = 0.0\nfor v in a[1]\n    r += v\n"))
    assert "_pf_collection_hist_a" not in cpp
    compile_cpp(cpp, label="for in history")


# The probes TradingView's compiler refused (fixtures/array_history_tv
# README), each with the code it answered.
TRADINGVIEW_REFUSED = {
    "ahist_noparen": ("CE10011", 8),
    "ahist_m_noparen": ("CE10011", 7),
    "ahist_field_noparen": ("CE10290", 10),
    "ahist_field_paren": ("CE10290", 10),
    "ahist_mfield": ("CE10290", 9),
    "ahist_eq": ("CE10123", 7),
}


@pytest.mark.parametrize("name", sorted(TRADINGVIEW_REFUSED))
def test_every_probe_tradingview_refused_is_refused(name):
    code, line = TRADINGVIEW_REFUSED[name]
    error = _refusal((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    diag = _diag(error, code)
    assert diag.location is not None and diag.location.line == line


@pytest.mark.parametrize("spelling", ["h.xs[1]", "(h.xs)[1]"])
def test_the_history_of_an_array_field_is_refused_by_the_analyzer(spelling):
    error = _refusal(_script(
        "type H\n    array<float> xs\n"
        "h = H.new(array.from(close))\n"
        "float r = 0.0\nif bar_index > 0\n"
        f"    r := array.size({spelling})\n"))
    diag = _diag(error, "CE10290")
    assert diag.phase.name == "ANALYZER"
    assert diag.location.line == 8
    assert "(h[1]).xs" in (diag.hint or "")


@pytest.mark.parametrize("decl, spelling", [
    ("matrix<float> m", "matrix.get(h.m[1], 0, 0)"),
    ("map<string, float> mp", "map.size(h.mp[1])"),
])
def test_the_history_of_a_matrix_or_map_field_is_refused(decl, spelling):
    error = _refusal(_script(
        f"type H\n    {decl}\n"
        "h = H.new()\n"
        "float r = 0.0\nif bar_index > 0\n"
        f"    r := {spelling}\n"))
    _diag(error, "CE10290")


@pytest.mark.parametrize("body, line", [
    ("a = array.from(close)\nfloat r = 0.0\nif bar_index > 0\n    r := a[1].size()\n", 6),
    ("m = matrix.new<float>(1, 1, close)\nfloat r = 0.0\n"
     "if bar_index > 0\n    r := m[1].get(0, 0)\n", 6),
])
def test_a_method_straight_after_a_collection_history_cites_ce10011(body, line):
    error = _refusal(_script(body))
    diag = _diag(error, "history-referencing")
    assert "CE10011" in diag.message
    assert diag.location.line == line


# An array where TradingView expects something else: each refused there with
# the code named (pine-facade, 2026-10-01).
SCALAR_CONTEXTS = {
    "equality": ("r = a == a[1] ? 1 : 0\n", "CE10123"),
    "inequality": ("r = a[1] != a ? 1 : 0\n", "CE10123"),
    "arithmetic": ("r = a[1] + 1\n", "CE10123"),
    "comparison": ("r = a[1] > 0 ? 1 : 0\n", "CE10123"),
    "negation": ("r = -a[1]\n", "CE10123"),
    "ternary_condition": ("r = a[1] ? 1 : 0\n", "CE10123"),
    "builtin_argument": ("r = math.abs(a[1])\n", "CE10123"),
    "nz_argument": ("r = nz(a[1])\n", "CE10123"),
    "float_declaration": ("float r = a[1]\n", "CE10173"),
    "float_reassignment": ("float r = 0.0\nr := a[1]\n", "CE10173"),
    "compound_assignment": ("b = array.from(1.0)\nb += a[1]\nr = b.size()\n", "CE10123"),
    "if_condition": ("float r = 0.0\nif a[1]\n    r := 1\n", "CE10101"),
    "while_condition": ("float r = 0.0\nwhile a[1]\n    r := 1\n    break\n", "CE10101"),
}


@pytest.mark.parametrize("name", sorted(SCALAR_CONTEXTS))
def test_an_array_history_where_tradingview_expects_a_number_is_refused(name):
    body, code = SCALAR_CONTEXTS[name]
    error = _refusal(_script("a = array.from(close)\n" + body))
    diag = _diag(error, code)
    assert diag.location is not None


def test_a_bare_history_statement_is_refused():
    # TradingView reads ``a[1]`` at the start of a line as a declaration and
    # refuses it (CE10009, "Extraneous input").
    error = _refusal(_script("a = array.from(close)\na[1]\nr = a.size()\n"))
    _diag(error, "CE10009")


# Forms TradingView accepts that PineForge does not lower; none compiled
# before (fixtures/array_history_tv ahist_fn, ahist_method: TradingView's
# answers for a follow-up).
NOT_SUPPORTED = {
    "function_parameter": (
        "f(array<float> x) =>\n    na(x[1]) ? -1 : (x[1]).size()\n"
        "a = array.from(close)\nr = f(a)\n", "parameter"),
    "function_local": (
        "f(float y) =>\n    la = array.from(y)\n    na(la[1]) ? -1.0 : (la[1]).get(0)\n"
        "r = f(close)\n", "function"),
    "method_receiver": (
        "method prevSize(array<float> this) =>\n"
        "    na(this[1]) ? -1 : (this[1]).size()\n"
        "a = array.from(close)\nr = a.prevSize()\n", "parameter"),
    "untyped_parameter": (
        "f(x) =>\n    na(x[1]) ? -1 : (x[1]).size()\n"
        "a = array.from(close)\nr = f(a)\n", "parameter"),
    "matrix_parameter": (
        "f(matrix<float> x) =>\n    na(x[1]) ? -1.0 : (x[1]).get(0, 0)\n"
        "m = matrix.new<float>(1, 1, close)\nr = f(m)\n", "parameter"),
    "global_read_in_a_function": (
        "a = array.from(close)\n"
        "f() =>\n    na(a[1]) ? -1 : (a[1]).size()\nr = f()\n", "function"),
    "call_result": (
        "mk(float k) =>\n    array.from(k)\n"
        "float r = 0.0\nif bar_index > 0\n    r := (mk(close)[1]).get(0)\n", "call"),
    "selection": (
        "a = array.from(close)\nb = array.from(open)\n"
        "float r = 0.0\nif bar_index > 0\n    r := ((close > open ? a : b)[1]).get(0)\n",
        "selection"),
    "history_of_history": (
        "a = array.from(close)\n"
        "float r = 0.0\nif bar_index > 1\n    r := ((a[1])[1]).size()\n", "expression"),
    "ternary_arm": (
        "a = array.from(close)\nb = array.from(open)\n"
        "c = close > open ? a[1] : b\nr = c.size()\n", "a[1]"),
    "switch_arm": (
        "a = array.from(close)\nb = array.from(open)\n"
        "c = switch\n    close > open => a[1]\n    => b\nr = c.size()\n", "a[1]"),
    "object_field_value": (
        "type H\n    array<float> xs\n"
        "a = array.from(close)\nh = H.new(a[1])\nr = h.xs.size()\n", "a[1]"),
    "tostring": (
        "a = array.from(close)\n"
        "s = str.tostring(a[1])\nr = str.length(s)\n", "a[1]"),
    "request_security": (
        "a = array.from(close)\n"
        'r = request.security(syminfo.tickerid, "60", na(a[1]) ? 0 : 1)\n',
        "request.security"),
    "block_var": (
        "float r = 0.0\nif close > open\n    var bv = array.new<float>()\n"
        "    bv.push(close)\n    r := na(bv[1]) ? 0.0 : (bv[1]).size()\n", "var"),
    "loop_local": (
        "float r = 0.0\nfor i = 0 to 2\n    la = array.from(close + i)\n"
        "    r += na(la[1]) ? 0.0 : (la[1]).get(0)\n", "loop"),
    # A variable bound to the history, or a function's parameter given it,
    # that the script changes: TradingView stops the run there (RE10051,
    # fixtures ahist_bound_push / ahist_param_push), which PineForge does not
    # check through a variable.
    "bound_then_changed": (
        "a = array.from(close)\nfloat r = 0.0\n"
        "if bar_index > 0\n    b = a[1]\n    b.push(1.0)\n    r := b.size()\n", "RE10051"),
    "argument_changed": (
        "f(array<float> y) =>\n    y.push(1.0)\n    y.size()\n"
        "a = array.from(close)\nfloat r = 0.0\nif bar_index > 0\n    r := f(a[1])\n",
        "RE10051"),
    "receiver_changed": (
        "method grow(array<float> this) =>\n    this.push(1.0)\n    this.size()\n"
        "a = array.from(close)\nfloat r = 0.0\nif bar_index > 0\n    r := (a[1]).grow()\n",
        "RE10051"),
}


@pytest.mark.parametrize("name", sorted(NOT_SUPPORTED))
def test_a_collection_history_pineforge_does_not_lower_is_refused(name):
    body, needle = NOT_SUPPORTED[name]
    error = _refusal(_script(body))
    diag = _diag(error, needle)
    assert diag.location is not None
    assert "not supported in PineForge" in diag.message or "RE10051" in diag.message


@pytest.mark.parametrize("name", ["ahist_fn", "ahist_method", "ahist_bound_push",
                                  "ahist_param_push"])
def test_the_function_scope_probes_are_refused_by_name(name):
    error = _refusal((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    assert any(d.location is not None for d in error.diagnostics)

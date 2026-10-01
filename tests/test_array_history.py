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
(CE10123), an array where a number, a condition or an element is expected
(CE10123, CE10173, CE10101, CE10122), and so does the transpiler, before
generating C++. A form TradingView accepts that PineForge does not lower
keeps the lowering it had where that compiled (an element of the current
array, a parameter's current array, with a warning), and is refused by name
where it did not.
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
    "bound_in_loop_and_switch": (
        "a = array.from(close, open)\nm = matrix.new<float>(2, 2, close)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    for i = 0 to 1\n        pb = a[1]\n        pm = m[1]\n"
        "        r += pb.size() + pm.get(0, 0)\n"
        "    switch\n        close > open =>\n            q = a[1]\n"
        "            r += q.size()\n        =>\n            r += 1\n"),
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
    "same_named_methods_of_two_types": (
        "type Cell\n    float v\n"
        "method sz(array<float> this) =>\n    this.size()\n"
        "method sz(Cell this) =>\n    this.v + 1\n"
        "a = array.from(close, open)\n"
        "float r = 0.0\nif bar_index > 0\n    r := (a[1]).sz()\n"),
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
    # A matrix function's new matrix keeps the history's element type, in
    # the namespace form too (it was declared a matrix of floats).
    "matrix_functions_of_other_element_types": (
        "mi = matrix.new<int>(2, 2, bar_index)\nms = matrix.new<string>(2, 2, \"a\")\n"
        "mb = matrix.new<bool>(2, 2, true)\nmc = matrix.new<color>(2, 2, color.red)\n"
        "float r = 0.0\nif bar_index > 0\n"
        "    ct = matrix.copy(mi[1])\n    ts = matrix.transpose(ms[1])\n"
        "    sb = matrix.submatrix(mb[1], 0, 1, 0, 1)\n"
        "    for i = 0 to 1\n        cc = matrix.copy(mc[1])\n        r += cc.rows()\n"
        "    r += ct.get(0, 0) + str.length(ts.get(0, 0)) + (sb.get(0, 0) ? 1 : 0)\n"),
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
        "float r = 0.0\nif bar_index > 1\n    r := ((a[1])[1]).size()\n", "array's history"),
    "ternary_arm": (
        "a = array.from(close)\nb = array.from(open)\n"
        "c = close > open ? a[1] : b\nr = c.size()\n", "a[1]"),
    "switch_arm": (
        "a = array.from(close)\nb = array.from(open)\n"
        "c = switch\n    close > open => a[1]\n    => b\nr = c.size()\n", "a[1]"),
    "object_field_value": (
        "type H\n    array<float> xs\n"
        "a = array.from(close)\nh = H.new(a[1])\nr = h.xs.size()\n", "a[1]"),
    "string_tostring": (
        "a = array.from(\"x\")\n"
        "s = str.tostring(a[1])\nr = str.length(s)\n", "a[1]"),
    "request_security_size": (
        "a = array.from(close)\n"
        'r = request.security(syminfo.tickerid, "60", (a[1]).size())\n',
        "request.security"),
    "slice_changed": (
        "a = array.from(close, open)\nfloat r = 0.0\n"
        "if bar_index > 0\n    s = (a[1]).slice(0, 1)\n    s.set(0, 5.0)\n"
        "    r := s.get(0)\n", "RE10051"),
    "namespace_slice_changed": (
        "a = array.from(close, open)\nfloat r = 0.0\n"
        "if bar_index > 0\n    s = array.slice(a[1], 0, 1)\n    s.set(0, 5.0)\n"
        "    r := s.get(0)\n", "RE10051"),
    # The earlier lowering read an element where these need the array: it
    # did not compile.
    "element_namespace_read_in_a_function": (
        "a = array.from(close, open)\nf() => array.sum(a[1])\nr = f()\n", "function"),
    "copy_receiver_in_a_function": (
        "a = array.from(close, open)\nf() => array.copy(a[1]).size()\nr = f()\n", "function"),
    "selection_arm_into_a_namespace_call": (
        "a = array.from(close, open)\nb = array.from(high)\n"
        "r = array.sum(close > open ? a[1] : b)\n", "a[1]"),
    "if_value_into_a_namespace_call": (
        "a = array.from(close, open)\nb = array.from(high)\n"
        "c = if close > open\n    a[1]\nelse\n    b\nr = array.sum(c)\n", "a[1]"),
    "function_result_into_a_namespace_call": (
        "a = array.from(close, open)\ng() => a[1]\nr = array.sum(g())\n", "function"),
    "tuple_element_method": (
        "a = array.from(close, open)\nf() => [a[1], 1]\n[x, y] = f()\nr = x.size()\n",
        "function"),
    "copy_returned_by_a_function": (
        "a = array.from(close, open)\nf() => array.copy(a[1])\nr = array.size(f())\n",
        "function"),
    "copy_rendered": (
        "a = array.from(close, open)\nf() => str.length(str.tostring(array.copy(a[1])))\n"
        "r = f()\n", "function"),
    "string_pop_value_used": (
        "s = array.from(\"a\", \"bc\")\nf() => array.pop(s[1])\nr = str.length(f())\n",
        "function"),
    "string_get_returned": (
        "s = array.from(\"a\", \"bc\")\nf() => array.get(s[1], 0)\nr = str.length(f())\n",
        "function"),
    "string_parameter_get_bound": (
        "f(array<string> x) =>\n    v = array.get(x[1], 0)\n    str.length(v)\n"
        "s = array.from(\"a\")\nr = f(s)\n", "parameter"),
    "bound_read_both_ways": (
        "a = array.from(close)\nfloat r = 0.0\n"
        "if bar_index > 0\n    pb = a[1]\n    r := pb.size() + (na(pb) ? 1 : 0)\n",
        "na()"),
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
    # A function that declares the name in a later block still reads the
    # script variable before it.
    "bound_then_changed_in_a_function_declaring_the_name_later": (
        "a = array.from(close)\npb = a[1]\n"
        "f() =>\n    pb.push(1.0)\n    if close > open\n        pb = 3\n    1\n"
        "r = f()\n", "RE10051"),
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


# Forms TradingView accepts (pine-facade, 2026-10-01) that PineForge does not
# lower here, whose earlier lowering compiled: they keep it, with its
# warning. An array variable's history outside the scopes kept, or read
# through na() or str.tostring() where only an element was lowered, reads an
# element of the current array; a parameter's reads the parameter's current
# array.
CURRENT_ELEMENT = "array history indexing uses the current collection element"
CURRENT_PARAMETER = "reads the current array in PineForge"
EARLIER_LOWERING = {
    "tostring": (
        "a = array.from(close)\ns = str.tostring(a[1])\nr = str.length(s)\n",
        CURRENT_ELEMENT),
    "format": (
        "a = array.from(close)\nr = str.length(str.format(\"{0}\", a[1]))\n",
        CURRENT_ELEMENT),
    "bound_na": (
        "a = array.from(close)\npb = a[1]\nr = na(pb) ? 0 : 1\n", CURRENT_ELEMENT),
    "bound_tostring": (
        "a = array.from(close)\npb = a[1]\nr = str.length(str.tostring(pb))\n",
        CURRENT_ELEMENT),
    "untyped_argument_na": (
        "isNa(v) => na(v) ? 1 : 0\na = array.from(close)\nr = isNa(a[1])\n",
        CURRENT_ELEMENT),
    "request_security_na": (
        "a = array.from(close)\n"
        'r = request.security(syminfo.tickerid, "60", na(a[1]) ? 0 : 1)\n',
        CURRENT_ELEMENT),
    "global_read_in_a_function_na": (
        "a = array.from(close)\nf() => na(a[1]) ? 0 : 1\nr = f()\n", CURRENT_ELEMENT),
    "function_local_tostring": (
        "f(float y) =>\n    la = array.from(y)\n    str.length(str.tostring(la[1]))\n"
        "r = f(close)\n", CURRENT_ELEMENT),
    "block_var_na": (
        "float r = 0.0\nif close > open\n    var bv = array.new<float>()\n"
        "    bv.push(close)\n    r := na(bv[1]) ? 0.0 : 1.0\n", CURRENT_ELEMENT),
    "loop_local_na": (
        "float r = 0.0\nfor i = 0 to 2\n    la = array.from(close + i)\n"
        "    r += na(la[1]) ? 0.0 : 1.0\n", CURRENT_ELEMENT),
    "selection_na": (
        "a = array.from(close)\nb = array.from(open)\n"
        "r = na((close > open ? a : b)[1]) ? 0 : 1\n",
        "element of the selected current array"),
    "string_loop_in_a_function": (
        "s = array.from(\"x\", \"y\")\nf() =>\n    int n = 0\n"
        "    for v in s[1]\n        n += 1\n    n\nr = f()\n", CURRENT_ELEMENT),
    "parameter_namespace_size": (
        "f(array<float> x) => array.size(x[1])\na = array.from(close)\nr = f(a)\n",
        CURRENT_PARAMETER),
    "parameter_loop": (
        "f(array<float> x) =>\n    float t = 0.0\n    for v in x[1]\n        t += v\n    t\n"
        "a = array.from(close)\nr = f(a)\n", CURRENT_PARAMETER),
    "receiver_namespace_get": (
        "method prev(array<float> this) => array.get(this[1], 0)\n"
        "a = array.from(close)\nr = a.prev()\n", CURRENT_PARAMETER),
    # array.copy of an element built a vector of the element's size: a
    # namespace call taking it compiled (fixtures/array_history_tv README).
    "copy_into_a_namespace_call": (
        "a = array.from(close, open)\nf() => array.size(array.copy(a[1]))\nr = f()\n",
        CURRENT_ELEMENT),
    "copy_in_a_request": (
        "a = array.from(close, open)\n"
        'r = request.security(syminfo.tickerid, "60", array.sum(array.copy(a[1])))\n',
        CURRENT_ELEMENT),
    "copy_of_a_block_var": (
        "float r = 0.0\nif close > open\n    var bv = array.from(close)\n"
        "    r := array.size(array.copy(bv[1]))\n", CURRENT_ELEMENT),
    # A string parameter's element read where a string is expected compiled.
    "string_parameter_get_into_a_string_call": (
        "f(array<string> x) => str.length(array.get(x[1], 0))\n"
        "s = array.from(\"a\")\nr = f(s)\n", CURRENT_PARAMETER),
    "copy_into_a_function_s_local": (
        "a = array.from(close, open)\nf() =>\n    bb = array.copy(a[1])\n"
        "    array.size(bb)\nr = f()\n", CURRENT_ELEMENT),
    # A parameter's new array taken by a namespace call compiled.
    "parameter_abs_into_a_namespace_call": (
        "f(array<float> x) => array.sum(array.abs(x[1]))\n"
        "a = array.from(close, open)\nr = f(a)\n", CURRENT_PARAMETER),
    # An overloaded method took the element through its number overload.
    "overloaded_method_argument": (
        "type T\n    float v\n"
        "method add(T this, array<float> xs) => this.v + xs.size()\n"
        "method add(T this, float z) => this.v + z\n"
        "a = array.from(close, open)\nt = T.new(1.0)\nr = t.add(a[1])\n",
        CURRENT_ELEMENT),
    # A string element has std::string's members; a dropped pop compiled.
    "string_pop_statement_in_a_function": (
        "s = array.from(\"a\", \"bc\")\nf() =>\n    array.pop(s[1])\n    1\nr = f()\n",
        CURRENT_ELEMENT),
    "string_clear_in_a_function": (
        "s = array.from(\"a\")\nf() =>\n    array.clear(s[1])\n    1\nr = f()\n",
        CURRENT_ELEMENT),
}


@pytest.mark.parametrize("name", sorted(EARLIER_LOWERING))
def test_a_form_the_earlier_lowering_compiled_keeps_it(name):
    body, warning = EARLIER_LOWERING[name]
    from pineforge_codegen import transpile_full
    result = transpile_full(_script(body))
    messages = [d.message for d in result["diagnostics"]]
    assert any(warning in m for m in messages), messages
    assert "_PFCollectionHistory" not in result["cpp"]
    compile_cpp(result["cpp"], label=name)


def test_a_function_nothing_calls_keeps_any_history_read():
    # The codegen emits no function nothing calls, so every read in one
    # compiled: a matrix's history in one no longer types the matrix a
    # number either.
    cpp = transpile(_script(
        "a = array.from(close)\nm = matrix.new<float>(1, 1, close)\n"
        "unused() => (a[1]).size() + matrix.rows(m[1]) + (m[1]).get(0, 0)\n"
        "r = a.size() + m.rows()\n"))
    assert "PineMatrix m;" in cpp
    compile_cpp(cpp, label="dead function")


# The codegen does emit every method, called or not, and every function a
# call names, the call sitting in a function nothing calls included: a read
# there is decided as in any emitted body (the reads below never compiled).
EMITTED_UNCALLED = {
    "uncalled_method": (
        "type T\n    float f\n"
        "method m(T this) => (a[1]).size() + this.f\n"),
    "called_only_from_an_uncalled_function": (
        "g() => (a[1]).size()\nf() => g()\n"),
    # The read sits in the uncalled f, but its call types the emitted
    # helper's untyped parameter with the element: v.size() on a number.
    "an_uncalled_function_s_argument_to_a_called_helper": (
        "sizeOf(v) => v.size()\nf() => sizeOf(a[1])\n"),
}


@pytest.mark.parametrize("name", sorted(EMITTED_UNCALLED))
def test_a_read_in_an_emitted_uncalled_body_is_decided_as_emitted(name):
    error = _refusal(_script(
        "a = array.from(close)\n" + EMITTED_UNCALLED[name] + "r = a.size()\n"))
    diag = _diag(error, "not supported in PineForge")
    assert diag.location is not None
    assert diag.location.line == {"uncalled_method": 6,
                                  "called_only_from_an_uncalled_function": 4,
                                  "an_uncalled_function_s_argument_to_a_called_helper": 5}[name]


# Uses TradingView refuses (pine-facade, 2026-10-01), each with its code: an
# array where an element, a number or a string is expected.
TRADINGVIEW_REFUSED_USES = {
    "push_into_another": ("c = array.new<float>()\nc.push(a[1])\nr = c.size()\n", "CE10123"),
    "namespace_push_into_another": (
        "c = array.new<float>()\narray.push(c, a[1])\nr = c.size()\n", "CE10123"),
    "includes": ("r = a.includes(a[1]) ? 1 : 0\n", "CE10123"),
    "indexof": ("r = a.indexof(a[1])\n", "CE10123"),
    "fill": ("a.fill(a[1])\nr = a.size()\n", "CE10123"),
    "array_new_initial_value": ("c = array.new<float>(2, a[1])\nr = c.size()\n", "CE10123"),
    "matrix_new_initial_value": (
        "m = matrix.new<float>(2, 2, a[1])\nr = m.rows()\n", "CE10123"),
    "array_from_element": ("c = array.from(a[1])\nr = c.size()\n", "CE10122"),
    "bound_nz": ("pb = a[1]\nx = nz(pb)\nr = 1\n", "CE10123"),
    "log_message": ("log.info(a[1])\nr = 1\n", "CE10123"),
    "label_text": ("label.new(bar_index, high, a[1])\nr = 1\n", "CE10123"),
    "colors_tostring": (
        "cs = array.from(color.red)\nlabel.new(bar_index, high, str.tostring(cs[1]))\nr = 1\n",
        "CE10123"),
    "float_field": (
        "type H\n    float f = 0.0\nh = H.new()\nh.f := a[1]\nr = h.f\n", "CE10173"),
}


@pytest.mark.parametrize("name", sorted(TRADINGVIEW_REFUSED_USES))
def test_a_use_tradingview_refuses_is_refused_with_its_code(name):
    body, code = TRADINGVIEW_REFUSED_USES[name]
    error = _refusal(_script("a = array.from(close)\n" + body))
    diag = _diag(error, code)
    assert diag.location is not None


def test_sibling_blocks_keep_a_history_each():
    # Two blocks each declare x: each block's x has its own history, and a
    # block's declaration closes the other's before it writes the member
    # they share (TradingView: a block's local reads its block's previous
    # run, fixtures/array_history_tv ahist_more t3).
    cpp = transpile(_script(
        "float r = 0.0\n"
        "if bar_index % 2 == 0\n    x = array.from(1.0)\n"
        "    r := na(x[1]) ? -1 : (x[1]).size()\n"
        "if bar_index % 3 == 0\n    x = array.from(1.0, 2.0, 3.0)\n"
        "    r += na(x[1]) ? -1 : (x[1]).size()\n"))
    assert "_pf_collection_hist_x{2};" in cpp
    assert "_pf_collection_hist_1_x{2};" in cpp
    first = cpp.index("_pf_collection_hist_x.open(x,")
    second = cpp.index("_pf_collection_hist_1_x.open(x,")
    # Each declaration first closes the other's history.
    assert cpp.rindex("_pf_collection_hist_1_x.close(x);", 0, first) >= 0
    assert cpp.rindex("_pf_collection_hist_x.close(x);", 0, second) > first
    compile_cpp(cpp, label="sibling blocks")


def test_a_later_declaration_s_history_is_named_apart_from_every_variable():
    # The second declaration of x and a variable x_1 each keep a history; the
    # members were both _pf_collection_hist_x_1, a duplicate member.
    cpp = transpile(_script(
        "float r = 0.0\n"
        "if bar_index % 2 == 0\n    x = array.from(1.0)\n    r := na(x[1]) ? -1 : 1\n"
        "if bar_index % 3 == 0\n    x = array.from(1.0, 2.0)\n    r += na(x[1]) ? -1 : 1\n"
        "x_1 = array.from(3.0, 4.0, 5.0)\nr += na(x_1[1]) ? -1 : 1\n"))
    for member in ("_pf_collection_hist_x{", "_pf_collection_hist_1_x{",
                   "_pf_collection_hist_x_1{"):
        assert cpp.count(member) == 1, member
    compile_cpp(cpp, label="history member names")


def test_a_diamond_of_helpers_is_walked_once_per_helper():
    # Each helper passes its parameter to the one below twice: a walk per
    # call path visits 2**22 paths.
    lines = ["f0(x) => x.size()"]
    for depth in range(1, 23):
        lines.append(f"f{depth}(x) => f{depth - 1}(x) + f{depth - 1}(x)")
    body = "\n".join(lines) + "\na = array.from(close)\nfloat r = 0.0\nif bar_index > 0\n    r := f22(a[1])\n"
    import time
    started = time.monotonic()
    transpile(_script(body))
    assert time.monotonic() - started < 30


def test_many_bindings_are_walked_once_each():
    # Each binding's uses come from an index of the script's names: a walk of
    # the rest of the script per binding took 57 s for these 4,000 (7 s now,
    # 4 s for the same script binding array.copy(a) instead).
    lines = ["a = array.from(close, open)", "float r = 0.0"]
    for i in range(4000):
        lines.append(f"p{i} = a[1]")
        lines.append(f"r += p{i}.size()")
    import time
    started = time.monotonic()
    transpile(_script("\n".join(lines) + "\n"))
    assert time.monotonic() - started < 40


def test_the_changing_methods_are_the_codegen_s_mutating_ones():
    # The checker's change tables follow the codegen's own list of the
    # built-ins that mutate a collection.
    from pineforge_codegen.codegen.tables import ARRAY_METHODS, MATRIX_METHODS
    from pineforge_codegen.codegen.types import COLLECTION_MUTATING_METHODS
    from pineforge_codegen.collection_history import (
        ARRAY_CHANGING_METHODS, MATRIX_CHANGING_METHODS,
    )
    assert ARRAY_CHANGING_METHODS == COLLECTION_MUTATING_METHODS & set(ARRAY_METHODS)
    assert MATRIX_CHANGING_METHODS == COLLECTION_MUTATING_METHODS & set(MATRIX_METHODS)


def test_the_runtime_stops_spell_the_message_constants():
    # The C++ stops with TradingView's texts, which the module's constants
    # hold (fixtures/array_history_tv README).
    from pineforge_codegen.collection_history import (
        COLLECTION_HISTORY_CLASS_CPP, COLLECTION_HISTORY_CPP,
        COLLECTION_HISTORY_GENERIC_MATRIX_CPP, COLLECTION_HISTORY_MATRIX_CPP,
        HISTORICAL_CHANGE_MESSAGE, NA_ARRAY_MESSAGE, NA_MATRIX_MESSAGE,
    )
    assert f'"{HISTORICAL_CHANGE_MESSAGE}"' in COLLECTION_HISTORY_CLASS_CPP
    assert f'"{NA_ARRAY_MESSAGE}"' in COLLECTION_HISTORY_CPP
    assert f'"{NA_MATRIX_MESSAGE}"' in COLLECTION_HISTORY_MATRIX_CPP
    assert f'"{NA_MATRIX_MESSAGE}"' in COLLECTION_HISTORY_GENERIC_MATRIX_CPP
    assert "@" not in COLLECTION_HISTORY_CPP + COLLECTION_HISTORY_CLASS_CPP


def test_an_array_function_s_result_is_named_in_a_refusal():
    error = _refusal(_script(
        "a = array.from(close, open)\nr = array.slice(a[1], 0, 1) + 1\n"))
    _diag(error, "array.slice(a[1])")

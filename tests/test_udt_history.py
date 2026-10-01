"""History references on user-defined objects and drawing references.

TradingView (``fixtures/udt_history_tv``; Pine v6 User Manual, "Type system":
value vs. reference types): a variable of a user-defined type or of a drawing
type holds a reference, and its history holds the references it held, so
``(c[1]).v`` reads the object the variable held one bar back as it is now.
The codegen stores such a variable's history as a ``Series`` of the handles
it holds (an arena id, ``-1`` for na); it declared ``Series<double>`` and
pushed handles into it, which did not compile.

TradingView refuses a field read or a method call straight after the
history-referencing operator (``c[1].v``: CE10011; ``b[1].get_top()``:
CE10010) and ``==`` / ``!=`` on every reference type but ``line`` and
``label`` (CE10123), and so does the transpiler, before generating C++.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp

FIXTURES = Path(__file__).parent / "fixtures" / "udt_history_tv"


def _refusal(source: str) -> CompileError:
    with pytest.raises(CompileError) as exc:
        transpile(source)
    return exc.value


# The web lane's four probes (blog fact-check, codegen 1.0.0): each reads a
# field straight after the history operator, which TradingView refuses with
# CE10011 at the dot (pine-facade, 2026-10-01).
LANE_PROBES = {
    "t13_ne_hist": (
        "//@version=6\n"
        'strategy("t13 ne and udt history", overlay=true)\n'
        "type Box\n"
        "    float value\n"
        "float x = na\n"
        "if bar_index % 2 == 0\n"
        "    x := close\n"
        "ne = x != open\n"
        "b = Box.new(value = close)\n"
        "prevVal = {hist}.value\n"
        "if ne and prevVal > 0\n"
        '    strategy.entry("L", strategy.long)\n',
        "b[1]", 10),
    "t14a_udt_hist_typed": (
        "//@version=6\n"
        'strategy("t14a udt history typed", overlay=true)\n'
        "type Cell\n"
        "    float v\n"
        "Cell c = Cell.new(v = close)\n"
        "prevV = {hist}.v\n"
        "if prevV > 0\n"
        '    strategy.entry("L", strategy.long)\n',
        "c[1]", 6),
    "t14b_udt_hist_untyped": (
        "//@version=6\n"
        'strategy("t14b udt history untyped", overlay=true)\n'
        "type Cell\n"
        "    float v\n"
        "c = Cell.new(v = close)\n"
        "prevV = {hist}.v\n"
        "if prevV > 0\n"
        '    strategy.entry("L", strategy.long)\n',
        "c[1]", 6),
    "t14c_udt_hist_var": (
        "//@version=6\n"
        'strategy("t14c udt history var", overlay=true)\n'
        "type Acc\n"
        "    float total = 0.0\n"
        "var acc = Acc.new()\n"
        "acc.total += close\n"
        "prevT = {hist}.total\n"
        "if prevT > 0\n"
        '    strategy.entry("L", strategy.long)\n',
        "acc[1]", 7),
}


@pytest.mark.parametrize("name", sorted(LANE_PROBES))
def test_a_field_straight_after_a_history_reference_is_refused(name):
    template, hist, line = LANE_PROBES[name]
    error = _refusal(template.replace("{hist}", hist))
    [diag] = [d for d in error.diagnostics if "history-referencing" in d.message]
    assert "CE10011" in diag.message
    assert diag.location.line == line
    assert f"({hist})." in (diag.hint or "")


@pytest.mark.parametrize("name", sorted(LANE_PROBES))
def test_the_parenthesized_lane_probes_compile(name):
    template, hist, _line = LANE_PROBES[name]
    cpp = transpile(template.replace("{hist}", f"({hist})"))
    compile_cpp(cpp, label=name)


def test_a_method_straight_after_a_history_reference_is_refused():
    error = _refusal(
        "//@version=6\n"
        'strategy("m", overlay = true)\n'
        "b = box.new(bar_index, high, bar_index + 1, low)\n"
        "t = bar_index > 0 ? b[1].get_top() : 0.0\n"
        "if t > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    [diag] = [d for d in error.diagnostics if "history-referencing" in d.message]
    assert "CE10010" in diag.message
    assert diag.location.line == 4
    assert "(b[1]).get_top()" in (diag.hint or "")


@pytest.mark.parametrize("decl, expr, kind", [
    ("type Cell\n    int v\nc = Cell.new(v = bar_index)\n", "c != c[1]", "Cell"),
    ("type Cell\n    int v\nvar Cell keep = Cell.new(v = 0)\n", "keep == keep[1]", "Cell"),
    ("b = box.new(bar_index, high, bar_index + 1, low)\n", "b != b[1]", "box"),
    ("b = box.new(bar_index, high, bar_index + 1, low)\n"
     "var box vb = box.new(bar_index, close, bar_index + 1, close)\n", "vb == b", "box"),
    ("p = chart.point.from_index(bar_index, close)\n"
     "q = chart.point.from_index(bar_index, close)\n", "p == q", "chart.point"),
    ("var line l1 = line.new(0, 0, 1, 1)\nvar line l2 = line.new(0, 1, 1, 2)\n"
     "var linefill f1 = linefill.new(l1, l2, color.red)\nvar linefill f2 = f1\n",
     "f1 == f2", "linefill"),
])
def test_equality_of_references_tradingview_refuses_is_refused(decl, expr, kind):
    error = _refusal(
        "//@version=6\n"
        'strategy("eq", overlay = true)\n'
        f"{decl}"
        f"s = {expr}\n"
        "if s\n"
        '    strategy.entry("L", strategy.long)\n')
    [diag] = [d for d in error.diagnostics if "CE10123" in d.message]
    assert kind in diag.message
    assert "line and label" in diag.message


def test_line_and_label_references_compare_by_identity():
    cpp = transpile(
        "//@version=6\n"
        'strategy("eq", overlay = true, max_lines_count = 500, max_labels_count = 500)\n'
        "l = line.new(bar_index, close, bar_index + 1, open)\n"
        "var line vl = line.new(0, 0, 1, 1)\n"
        "lb = label.new(bar_index, close, \"x\")\n"
        "s1 = l != l[1]\n"
        "s2 = vl == l\n"
        "s3 = lb != lb[1]\n"
        "if s1 and not s2 and s3\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "Series<Line> l;" in cpp
    assert "Series<Label> lb;" in cpp
    assert "(l[0]).id != (l[1]).id" in cpp
    assert "(vl).id == (l[0]).id" in cpp
    compile_cpp(cpp, label="line/label identity")


# Every spelling below compiles on TradingView (pine-facade, 2026-10-01).
SHAPES = {
    "typed": (
        "type Cell\n    float v\n"
        "Cell c = Cell.new(v = close)\n"
        "r = bar_index > 0 ? (c[1]).v : 0.0\n"),
    "untyped_na_guard": (
        "type Cell\n    float v\n"
        "c = Cell.new(v = close)\n"
        "float r = 0.0\n"
        "if not na(c[1])\n    r := (c[1]).v\n"),
    "var_object": (
        "type Acc\n    float total = 0.0\n"
        "var acc = Acc.new()\n"
        "acc.total += close\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (acc[1]).total\n"),
    "history_bound_and_changed": (
        "type Cell\n    float v\n    int n = 0\n"
        "c = Cell.new(v = close)\n"
        "if bar_index >= 1\n    p = c[1]\n    p.n += 1\n"
        "float r = 0.0\n"
        "if bar_index >= 2\n    r := (c[2]).n\n"),
    "method_on_history": (
        "type Cell\n    float v\n"
        "method dbl(Cell this) =>\n    this.v * 2\n"
        "c = Cell.new(v = close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (c[1]).dbl()\n"),
    "object_or_na": (
        "type Cell\n    int v\n"
        "Cell maybe = bar_index % 3 == 0 ? Cell.new(v = bar_index) : na\n"
        "r = na(maybe[1]) ? 0 : (maybe[1]).v\n"),
    "nested_object": (
        "type Cell\n    int v\n"
        "type Outer\n    Cell inner\n"
        "o = Outer.new(Cell.new(v = bar_index * 10))\n"
        "int r = 0\n"
        "if bar_index > 0\n    r := (o[1]).inner.v\n"),
    "function_local": (
        "type Cell\n    float v\n"
        "f(float x) =>\n    c = Cell.new(v = x)\n    na(c[1]) ? 0.0 : (c[1]).v\n"
        "r = f(close)\n"),
    "function_parameter": (
        "type Cell\n    float v\n"
        "f(Cell x) =>\n    na(x[1]) ? 0.0 : (x[1]).v\n"
        "c = Cell.new(v = close)\n"
        "r = f(c)\n"),
    "history_as_argument": (
        "type Cell\n    float v\n"
        "g(Cell x) =>\n    na(x) ? 0.0 : x.v\n"
        "c = Cell.new(v = close)\n"
        "r = g(c[1])\n"),
    "carried_forward": (
        "type Cell\n    float v\n"
        "Cell s = na\n"
        "s := s[1]\n"
        "if bar_index == 5\n    s := Cell.new(v = close)\n"
        "float r = na(s) ? 0.0 : s.v\n"),
    "pushed_into_array": (
        "type Cell\n    float v\n"
        "var arr = array.new<Cell>()\n"
        "c = Cell.new(v = close)\n"
        "if bar_index > 0\n    arr.push(c[1])\n"
        "float r = arr.size() > 0 ? arr.last().v : 0.0\n"),
    "selected_by_ternary": (
        "type Cell\n    float v\n"
        "c = Cell.new(v = close)\n"
        "Cell pick = bar_index % 2 == 0 ? c : c[1]\n"
        "float r = na(pick) ? 0.0 : pick.v\n"),
    "method_receiver": (
        "type Cell\n    float v\n"
        "method prevv(Cell this) =>\n    na(this[1]) ? 0.0 : (this[1]).v\n"
        "c = Cell.new(v = close)\n"
        "r = c.prevv()\n"),
    "block_local": (
        "type Cell\n    float v\n"
        "float r = 0.0\n"
        "if close > open\n    cc = Cell.new(v = close)\n"
        "    r := na(cc[1]) ? 0.0 : (cc[1]).v\n"),
    "box": (
        "b = box.new(bar_index, high, bar_index + 1, low)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := box.get_top(b[1])\n"),
    "box_methods": (
        "b = box.new(bar_index, high, bar_index + 1, low)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (b[1]).get_top()\n    (b[1]).set_bottom(-1.0)\n"),
    "var_box": (
        "var box vb = box.new(bar_index, close, bar_index + 1, close)\n"
        "box.set_top(vb, close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := box.get_top(vb[1])\n"),
    "line_and_label": (
        "l = line.new(bar_index, high, bar_index + 1, low)\n"
        "lb = label.new(bar_index, close, \"x\")\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := line.get_y1(l[1]) + label.get_y(lb[1])\n"),
    "box_carried_forward": (
        "box s = na\n"
        "s := s[1]\n"
        "if bar_index == 5\n    s := box.new(bar_index, high, bar_index + 1, low)\n"
        "float r = na(s) ? 0.0 : box.get_top(s)\n"),
    "box_block_local": (
        "float r = 0.0\n"
        "if close > open\n    bx = box.new(bar_index, high, bar_index + 1, low)\n"
        "    r := na(bx[1]) ? 0.0 : box.get_top(bx[1])\n"),
    "box_function_local": (
        "f(float y) =>\n    bx = box.new(bar_index, y, bar_index + 1, y - 1)\n"
        "    na(bx[1]) ? -1.0 : box.get_top(bx[1])\n"
        "r = f(close)\n"),
    "chart_point": (
        "p = chart.point.from_index(bar_index, close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (p[1]).price\n"),
    # The history of an expression whose value is a reference: the reference
    # it produced at its previous evaluation (udth_expr).
    "call_result": (
        "type Cell\n    float v\n"
        "mk() =>\n    Cell.new(v = close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (mk()[1]).v\n"),
    "method_result": (
        "type Cell\n    float v\n"
        "method twin(Cell this) =>\n    Cell.new(v = this.v * 2)\n"
        "c = Cell.new(v = close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (c.twin()[1]).v\n"),
    "box_call_result": (
        "mkb() =>\n    box.new(bar_index, high, bar_index + 1, low)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := box.get_top(mkb()[1])\n"),
    "selected_object": (
        "type Cell\n    float v\n"
        "a = Cell.new(v = close)\n"
        "b = Cell.new(v = open)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := ((close > open ? a : b)[1]).v\n"),
    "object_field": (
        "type Cell\n    float v\n"
        "type Outer\n    Cell inner\n"
        "o = Outer.new(Cell.new(v = close))\n"
        "float r = 0.0\n"
        "if bar_index > 0\n    r := (o.inner[1]).v\n"),
    # A function's history-read var object at two call sites: every
    # per-call-site copy keeps its references (udth_fn2).
    "function_var_two_calls": (
        "type State\n    float v\n"
        "keep(float x) =>\n    var s = State.new(0.0)\n    s.v := x\n"
        "    na(s[1]) ? 0.0 : (s[1]).v\n"
        "r = keep(close) + keep(open)\n"),
    "parameter_object_field": (
        "type Cell\n    float v\n"
        "type Outer\n    Cell inner\n"
        "f(Outer p) =>\n    na(p.inner[1]) ? 0.0 : (p.inner[1]).v\n"
        "o = Outer.new(Cell.new(close))\n"
        "r = f(o)\n"),
    "parameter_selection": (
        "type Cell\n    float v\n"
        "g(Cell a, Cell b) =>\n    t = (close > open ? a : b)[1]\n"
        "    na(t) ? 0.0 : t.v + ((close > open ? a : b)[1]).v\n"
        "r = g(Cell.new(close), Cell.new(open))\n"),
    "lazy_arm_reads": (
        "type Cell\n    int v\n"
        "type Outer\n    Cell inner\n"
        "mk(int k) =>\n    Cell.new(v = k)\n"
        "var o = Outer.new(Cell.new(v = -5))\n"
        "o.inner := Cell.new(v = bar_index)\n"
        "r = na(o.inner[1]) ? -1 : (o.inner[1]).v + (mk(bar_index)[1]).v\n"),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_history_shape_compiles(name):
    cpp = transpile(
        "//@version=6\n"
        'strategy("shape", overlay = true, max_boxes_count = 500, '
        "max_lines_count = 500, max_labels_count = 500)\n"
        f"{SHAPES[name]}"
        "if r > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    compile_cpp(cpp, label=name)


def test_a_history_read_object_is_a_series_of_handles():
    cpp = transpile(
        "//@version=6\n"
        'strategy("handles", overlay = true)\n'
        "type Cell\n    float v\n"
        "type Acc\n    float total = 0.0\n"
        "c = Cell.new(v = close)\n"
        "var acc = Acc.new()\n"
        "acc.total += close\n"
        "b = box.new(bar_index, high, bar_index + 1, low)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n"
        "    r := (c[1]).v + (acc[1]).total + box.get_top(b[1])\n"
        "if r > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "Series<Cell> c;" in cpp
    assert "Series<Acc> acc;" in cpp
    assert "Series<Box> b;" in cpp
    # A field read through the history reads the arena record it names now.
    assert "_pf_udt_Cell.read(c[1]).v" in cpp
    assert "_pf_udt_Acc.read(acc[1]).total" in cpp
    assert "_pf_drawing_get(pf_box_get_top, _pf_boxes_, b[1])" in cpp
    compile_cpp(cpp, label="handles")


def test_a_na_object_series_starts_na():
    cpp = transpile(
        "//@version=6\n"
        'strategy("na", overlay = true)\n'
        "type Cell\n    float v\n"
        "Cell s = na\n"
        "s := s[1]\n"
        "if bar_index == 5\n    s := Cell.new(v = close)\n"
        "if not na(s) and s.v > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "s.push(Cell{})" in cpp
    assert "s.update(s[1]);" in cpp
    compile_cpp(cpp, label="na object series")


@pytest.mark.parametrize("spelling", ["c.v[1]", "(c.v)[1]"])
def test_history_of_a_value_field_is_refused(spelling):
    # TradingView refuses both spellings (CE10290) and takes (c[1]).v.
    error = _refusal(
        "//@version=6\n"
        'strategy("f", overlay = true)\n'
        "type Cell\n    float v\n"
        "c = Cell.new(v = close)\n"
        "float r = 0.0\n"
        "if bar_index > 0\n"
        f"    r := {spelling}\n"
        "if r > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    [diag] = [d for d in error.diagnostics if "CE10290" in d.message]
    assert diag.location.line == 8
    assert "(c[1]).v" in (diag.hint or "")


def test_history_of_a_changed_chart_point_is_refused():
    # PineForge holds a chart.point as a value: the history of a point the
    # script changes a field of would read the old value.
    error = _refusal(
        "//@version=6\n"
        'strategy("cp", overlay = true)\n'
        "var p = chart.point.from_index(0, 0)\n"
        "p.price := close\n"
        "float r = 0.0\n"
        "if bar_index > 0\n"
        "    r := (p[1]).price - close\n"
        "if r == 0\n"
        '    strategy.entry("L", strategy.long)\n')
    [diag] = [d for d in error.diagnostics if "chart.point" in d.message]
    assert diag.location.line == 7


@pytest.mark.parametrize("payload, helper", [
    ("na(c[1]) ? 0.0 : (c[1]).v", ""),
    ("f()", "f() =>\n    k = Cell.new(v = close)\n    na(k[1]) ? 0.0 : (k[1]).v\n"),
    ("na(mk()[1]) ? 0.0 : 1.0", "mk() =>\n    Cell.new(v = close)\n"),
])
def test_reference_history_in_a_request_is_refused(payload, helper):
    # TradingView reads the references the variable held on the requested
    # timeframe's bars, which PineForge does not keep; the payload read the
    # chart's history (and never compiled).
    error = _refusal(
        "//@version=6\n"
        'strategy("s", overlay = true)\n'
        "type Cell\n    float v\n"
        "c = Cell.new(v = close)\n"
        f"{helper}"
        f'r = request.security(syminfo.tickerid, "60", {payload})\n'
        "if r > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    [diag] = [d for d in error.diagnostics if "request.security" in d.message]
    assert "Cell reference" in diag.message


def test_a_requested_field_value_still_compiles():
    cpp = transpile(
        "//@version=6\n"
        'strategy("s", overlay = true)\n'
        "type Cell\n    float v\n"
        "c = Cell.new(v = close)\n"
        'r = request.security(syminfo.tickerid, "60", c.v)\n'
        "if r > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    compile_cpp(cpp, label="requested field")


# The probes TradingView's compiler refused (fixtures/udt_history_tv README),
# each with the code it answered.
TRADINGVIEW_REFUSED = {
    "udth_noparen": "CE10011",
    "udth_method_noparen": "CE10010",
    "udth_eq": "CE10123",
    "udth_box_eq": "CE10123",
    "udth_field_noparen": "CE10290",
    "udth_field_paren": "CE10290",
}


@pytest.mark.parametrize("name", sorted(TRADINGVIEW_REFUSED))
def test_every_probe_tradingview_refused_is_refused(name):
    error = _refusal((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
    assert any(TRADINGVIEW_REFUSED[name] in d.message for d in error.diagnostics), (
        [d.message for d in error.diagnostics])


def test_a_request_without_an_expression_still_transpiles():
    # request.security(sym, tf) with no expression (TradingView: CE10165)
    # lowered to na before the reference-history check, which must not
    # trip on it.
    cpp = transpile(
        "//@version=6\n"
        'strategy("s", overlay = true)\n'
        'r = request.security(syminfo.tickerid, "D")\n'
        "if close > open\n"
        '    strategy.entry("L", strategy.long)\n')
    compile_cpp(cpp, label="request without expression")

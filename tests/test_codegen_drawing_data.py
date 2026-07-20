"""Drawing-objects-as-data: full-feature lowering + compile probes.

Covers the line/box/label/linefill/chart.point surface that the public
corpus does not exercise (only ``line.new`` appears there). Transpile-level
assertions pin the emitted ``pf_*`` arena calls; the compile-gated probes
prove the generated C++ type-checks against ``pineforge/drawing.hpp``.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, skip_if_no_compile_env


def _cpp(body: str, header: str = "") -> str:
    hdr = '//@version=6\nstrategy("t"' + (", " + header if header else "") + ")\n"
    return transpile(hdr + body + "\n")


# ---------------------------------------------------------------------------
# Non-drawing strategies stay byte-clean (no include, no arenas).
# ---------------------------------------------------------------------------
def test_non_drawing_strategy_has_no_drawing_machinery():
    cpp = _cpp("x = ta.ema(close, 9)\nplot(x)")
    assert "pineforge/drawing.hpp" not in cpp
    assert "DrawingArena" not in cpp
    assert "_pf_lines_" not in cpp


# ---------------------------------------------------------------------------
# line
# ---------------------------------------------------------------------------
def test_line_new_drops_visual_kwargs_keeps_geometry():
    cpp = _cpp(
        "ln = line.new(bar_index, close, bar_index + 1, open, "
        "xloc=xloc.bar_index, extend=extend.both, color=color.red, "
        "style=line.style_dashed, width=2)"
    )
    assert ("pf_line_new(_pf_lines_, (int64_t)(pine_bar_index()), (double)(current_bar_.close), "
            "(int64_t)((pine_bar_index() + 1)), (double)(current_bar_.open), "
            "XLoc::bar_index, true, true)") in cpp
    assert "color" not in cpp.split("pf_line_new")[1].split(";")[0]


def test_line_getters_setters_delete_copy():
    cpp = _cpp(
        "ln = line.new(bar_index, close, bar_index, close)\n"
        "y = ln.get_y2()\n"
        "x = ln.get_x1()\n"
        "ln.set_y2(close)\n"
        "ln.set_x2(bar_index)\n"
        "c = ln.copy()\n"
        "ln.delete()"
    )
    # getter return types drive the member decl; the arena call is the RHS.
    assert "double y" in cpp and "pf_line_get_y2(_pf_lines_, ln)" in cpp
    assert "int64_t x" in cpp and "pf_line_get_x1(_pf_lines_, ln)" in cpp
    assert "pf_line_set_y2(_pf_lines_, ln, (double)(current_bar_.close))" in cpp
    assert "pf_line_set_x2(_pf_lines_, ln, (int64_t)(pine_bar_index()))" in cpp
    assert "Line c" in cpp and "pf_line_copy(_pf_lines_, ln)" in cpp
    assert "pf_line_delete(_pf_lines_, ln)" in cpp


# ---------------------------------------------------------------------------
# box
# ---------------------------------------------------------------------------
def test_box_new_and_getters():
    cpp = _cpp(
        "bx = box.new(bar_index, high, bar_index + 5, low, "
        "border_color=color.red, bgcolor=color.blue)\n"
        "t = box.get_top(bx)\n"
        "l = box.get_left(bx)\n"
        "box.set_bottom(bx, low)"
    )
    assert ("pf_box_new(_pf_boxes_, (int64_t)(pine_bar_index()), (double)(current_bar_.high), "
            "(int64_t)((pine_bar_index() + 5)), (double)(current_bar_.low), XLoc::bar_index)") in cpp
    assert "double t" in cpp and "pf_box_get_top(_pf_boxes_, bx)" in cpp
    assert "int64_t l" in cpp and "pf_box_get_left(_pf_boxes_, bx)" in cpp
    assert "pf_box_set_bottom(_pf_boxes_, bx, (double)(current_bar_.low))" in cpp


# ---------------------------------------------------------------------------
# label
# ---------------------------------------------------------------------------
def test_label_new_text_and_getters():
    cpp = _cpp(
        'lb = label.new(bar_index, close, "hi", yloc=yloc.abovebar, '
        "color=color.green, style=label.style_label_down)\n"
        "label.set_text(lb, \"bye\")\n"
        "s = lb.get_text()\n"
        "yy = lb.get_y()"
    )
    assert ('pf_label_new(_pf_labels_, (int64_t)(pine_bar_index()), (double)(current_bar_.close), '
            'std::string("hi"), XLoc::bar_index, YLoc::abovebar)') in cpp
    assert 'pf_label_set_text(_pf_labels_, lb, std::string("bye"))' in cpp
    assert "std::string s" in cpp and "pf_label_get_text(_pf_labels_, lb)" in cpp
    assert "double yy" in cpp and "pf_label_get_y(_pf_labels_, lb)" in cpp


# ---------------------------------------------------------------------------
# chart.point + line-from-points + linefill
# ---------------------------------------------------------------------------
def test_chart_point_and_line_pts_and_linefill():
    cpp = _cpp(
        "p1 = chart.point.now(close)\n"
        "p2 = chart.point.from_index(bar_index + 3, high)\n"
        "ln1 = line.new(p1, p2)\n"
        "ln2 = line.new(bar_index, low, bar_index + 3, low)\n"
        "lf = linefill.new(ln1, ln2, color.new(color.red, 80))\n"
        "g = linefill.get_line1(lf)"
    )
    assert "ChartPoint{ .index=(int64_t)(pine_bar_index()), .time=(int64_t)current_bar_.timestamp, .price=(current_bar_.close) }" in cpp
    assert "ChartPoint{ .index=(int64_t)((pine_bar_index() + 3)), .time=na<int64_t>(), .price=(current_bar_.high) }" in cpp
    assert "pf_line_new_pts(_pf_lines_, p1, p2, XLoc::bar_index)" in cpp
    # linefill drops the color arg.
    assert "pf_linefill_new(_pf_linefills_, ln1, ln2)" in cpp
    assert "Line g" in cpp and "pf_linefill_get_line1(_pf_linefills_, lf)" in cpp


# ---------------------------------------------------------------------------
# arena caps from the strategy() header.
# ---------------------------------------------------------------------------
def test_arena_caps_from_header():
    cpp = _cpp(
        "ln = line.new(bar_index, close, bar_index, close)\n"
        "bx = box.new(bar_index, high, bar_index, low)\n"
        'lb = label.new(bar_index, close, "x")',
        header="max_lines_count=300, max_boxes_count=100, max_labels_count=200",
    )
    assert "DrawingArena<LineRec> _pf_lines_{300};" in cpp
    assert "DrawingArena<BoxRec> _pf_boxes_{100};" in cpp
    assert "DrawingArena<LabelRec> _pf_labels_{200};" in cpp
    assert "DrawingArena<LinefillRec> _pf_linefills_{50};" in cpp


def test_var_handle_na_default_no_ctor_init():
    """``var line x = na`` -> a plain ``Line x;`` member (na = id -1); the
    constructor must NOT emit ``x(na<double>())`` against the handle struct."""
    cpp = _cpp("var line x = na\nx := line.new(bar_index, close, bar_index, close)")
    assert "Line x;" in cpp
    assert "x(na<double>())" not in cpp


def test_drawing_handle_ternary_na_uses_exact_handle_types():
    """A drawing constructor selected against ``na`` is target-typed.

    Without both TypeSpec propagation and typed arm lowering, the declaration
    becomes ``double`` and C++ sees incompatible ``Handle``/``double`` arms.
    Cover every real drawing handle type while leaving visual-only types out.
    """
    cpp = _cpp(
        "cond = close > open\n"
        "ln1 = cond ? line.new(bar_index, close, bar_index + 1, close) : na\n"
        "ln2 = line.new(bar_index, open, bar_index + 1, open)\n"
        "bx = cond ? box.new(bar_index, high, bar_index + 1, low) : na\n"
        'lb = cond ? label.new(bar_index, close, "x") : na\n'
        "lf = cond ? linefill.new(ln1, ln2, color.red) : na\n"
        "pt = cond ? chart.point.now(close) : na"
    )
    for cpp_type, name in (
        ("Line", "ln1"),
        ("Box", "bx"),
        ("Label", "lb"),
        ("Linefill", "lf"),
        ("ChartPoint", "pt"),
    ):
        assert f"{cpp_type} {name} =" in cpp
        assert f": ({cpp_type}{{}})" in cpp
        assert f"double {name} =" not in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-all-handles")


def test_drawing_handle_ternary_na_reverse_arm_and_reassignment_compile():
    src = '''//@version=6
strategy("drawing ternary na")
cond = close > open
reverse = cond ? na : label.new(bar_index, close, "reverse")
var label reassigned = na
reassigned := cond ? label.new(bar_index, high, "next") : na
if not na(reverse) and not na(reassigned)
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert "Label reverse =" in cpp
    assert "(Label{}) : (pf_label_new" in cpp
    assert "reassigned = ((cond) ? (pf_label_new" in cpp
    assert ": (Label{}))" in cpp
    assert "double reverse =" not in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-na")


def test_drawing_handle_ternary_na_respects_shadowed_scalar_local():
    cpp = _cpp(
        "var line x = na\n"
        "f() =>\n"
        "    float x = close > open ? close : na\n"
        "    x := close > open ? open : na\n"
        "    x\n"
        "value = f()"
    )
    function_body = cpp.split("double f() {", 1)[1].split("    }", 1)[0]
    assert "double x = ((" in function_body
    assert "x = ((" in function_body
    assert "Line{}" not in function_body
    assert function_body.count("na<double>()") == 2
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-shadowed-scalar")


def test_drawing_handle_ternary_na_function_return_and_global_var_compile():
    src = '''//@version=6
strategy("drawing ternary return")
cond = close > open
makeLabel() => cond ? label.new(bar_index, close, "returned") : na
var label globalLabel = cond ? label.new(bar_index, close, "initial") : na
globalLabel := cond ? makeLabel() : na
if not na(globalLabel)
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert "Label makeLabel(" in cpp
    assert "return ((cond) ? (pf_label_new" in cpp
    assert ": (Label{}));" in cpp
    assert "globalLabel = ((cond) ? (makeLabel()) : (Label{}));" in cpp
    cond_pos = cpp.index("cond = ([&]{")
    init_pos = cpp.index("if (!_pf_var_init_globalLabel) {")
    init_end = cpp.index("_pf_var_init_globalLabel = true;", init_pos)
    init_block = cpp[init_pos:init_end]
    assert cond_pos < init_pos
    assert "globalLabel = ((cond) ? (pf_label_new" in init_block
    assert 'std::string("initial")' in init_block
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-return-global")


def test_drawing_handle_udf_arm_and_tuple_element_compile():
    src = '''//@version=6
strategy("drawing ternary udf and tuple")
cond = close > open
makeLine() => line.new(bar_index, close, bar_index + 1, close)
makePair() => [cond ? makeLine() : na, makeLine()]
h = cond ? makeLine() : na
[a, b] = makePair()
if not na(h) and not na(a) and not na(b)
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert "Line makeLine(" in cpp
    assert "std::tuple<Line, Line> makePair(" in cpp
    assert "std::make_tuple(((cond) ? (makeLine()) : (Line{})), makeLine())" in cpp
    assert "Line h = Line{};" in cpp
    assert "h = ((cond) ? (makeLine()) : (Line{}));" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-udf-tuple")


def test_drawing_handle_ternary_na_drawing_parameter_reassignment_compile():
    src = '''//@version=6
strategy("drawing ternary parameter")
cond = close > open
update(line h) =>
    h := cond ? line.new(bar_index, close, bar_index + 1, close) : na
    h
var line globalLine = na
globalLine := update(globalLine)
if not na(globalLine)
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    body = cpp.split("Line update(Line& h) {", 1)[1].split("    }", 1)[0]
    assert "h = ((cond) ? (pf_line_new" in body
    assert ": (Line{}));" in body
    assert "na<double>()" not in body
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-parameter")


def test_drawing_handle_ternary_na_function_var_reassignment_compile():
    src = '''//@version=6
strategy("drawing ternary function var")
cond = close > open
make() =>
    var line h = na
    h := cond ? line.new(bar_index, close, bar_index + 1, close) : na
    h
result = make()
if not na(result)
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    body = cpp.split("Line make_cs0() {", 1)[1].split("    }", 1)[0]
    assert "h = ((cond) ? (pf_line_new" in body
    assert ": (Line{}));" in body
    assert "na<double>()" not in body
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-ternary-function-var")


def test_function_drawing_var_initializes_at_declaration_after_prior_local():
    src = '''//@version=6
strategy("function drawing declaration init")
make() =>
    bool cond = close > open
    var line h = cond ? line.new(bar_index, close, bar_index + 1, close) : na
    h
result = make()
'''
    cpp = transpile(src)
    body = cpp.split("Line make_cs0() {", 1)[1].split("    }", 1)[0]
    cond_pos = body.index("bool cond =")
    guard_pos = body.index("if (!this->_pf_var_init_h) {")
    assert cond_pos < guard_pos
    assert "h = ((cond) ? (pf_line_new" in body
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-function-declaration-init")


def test_conditional_function_drawing_var_keeps_lazy_first_entry_guard():
    src = '''//@version=6
strategy("conditional function drawing init")
make(bool cond) =>
    if cond
        var line h = line.new(bar_index, close, bar_index + 1, close)
    0.0
value = make(close > open)
'''
    cpp = transpile(src)
    body = cpp.split("double make_cs0(bool cond) {", 1)[1].split(
        "        return 0.0;", 1
    )[0]
    branch_pos = body.index("if (cond) {")
    guard_pos = body.index("if (!this->_pf_var_init_h) {")
    assert branch_pos < guard_pos
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-function-conditional-init")


@pytest.mark.parametrize(
    "body",
    (
        """f(float h, bool cond) =>
    before = h
    if cond
        var line h = line.new(bar_index, h, bar_index + 1, h)
        line.delete(h)
    before
a = f(close, close > open)
b = f(open, close < open)""",
        """f(bool cond) =>
    float h = close
    if cond
        var line h = line.new(bar_index, h, bar_index + 1, h)
        line.delete(h)
    h
value = f(close > open)""",
        """f(bool cond) =>
    line h = line.new(bar_index, close, bar_index + 1, close)
    if cond
        var line h = line.copy(h)
        line.delete(h)
    h
value = f(close > open)""",
        """f(float h, bool cond) =>
    result = if cond
        var line h = line.new(bar_index, h, bar_index + 1, h)
        h
    else
        na
    result
value = f(close, close > open)""",
    ),
)
def test_callable_persistent_drawing_ancestor_shadow_fails_closed(body):
    with pytest.raises(CompileError) as exc:
        _cpp(body)
    assert "Persistent drawing binding 'h' shadows an ancestor callable" in str(
        exc.value
    )


def test_later_scalar_local_is_not_poisoned_by_nested_drawing_binding():
    src = '''//@version=6
strategy("later local source order")
f(bool cond) =>
    if cond
        var line h = line.new(bar_index, close, bar_index + 1, close)
        line.delete(h)
    float h = close
    h
value = f(close > open)
'''
    cpp = transpile(src)
    assert "double f_cs0(bool cond)" in cpp
    assert "double h = current_bar_.close;" in cpp
    assert "return h;" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-later-scalar-source-order")


def test_function_terminal_drawing_if_accepts_explicit_na_arm():
    src = '''//@version=6
strategy("block if drawing na")
make(bool cond) =>
    if cond
        label.new(bar_index, close, "x")
    else
        na
result = make(close > open)
'''
    cpp = transpile(src)
    assert "Label make(bool cond)" in cpp
    assert "_func_ret = Label{};" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-terminal-explicit-na")


def test_block_valued_drawing_if_propagates_through_wrappers_and_fresh_clone():
    src = '''//@version=6
strategy("block drawing fresh return")
cond = close > open
leg(int size, bool arm) =>
    peak = ta.highest(size)
    result = if arm
        var line l = arm ? na : line.new(bar_index, peak, bar_index + 1, peak)
        old = l[1]
        l
    else
        na
    result
f_get(int len, bool arm) => leg(len, arm)
g_get(int len, bool arm) => leg(len, arm)
a = f_get(10, cond)
b = f_get(20, not cond)
c = g_get(30, cond)
'''
    cpp = transpile(src)
    assert "Line leg_cs0(int size, bool arm)" in cpp
    assert "Line leg_cs1(int size, bool arm)" in cpp
    assert "Line leg__ni1(int size, bool arm)" in cpp
    assert "Series<Line> l__ni1;" in cpp
    assert "l__ni1.push(l__ni1[0])" in cpp
    assert "l__ni1.update(l__ni1[0])" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-block-return-fresh-clone")


def test_drawing_handle_ternary_na_cross_type_shadow_in_both_orders_compile():
    sources = (
        '''//@version=6
strategy("drawing shadow global first")
cond = close > open
var line x = na
make() =>
    label x = cond ? label.new(bar_index, close, "local") : na
    x
result = make()
''',
        '''//@version=6
strategy("drawing shadow function first")
cond = close > open
make() =>
    label x = cond ? label.new(bar_index, close, "local") : na
    x
var line x = na
result = make()
''',
    )
    for index, src in enumerate(sources):
        cpp = transpile(src)
        body = cpp.split("Label make() {", 1)[1].split("    }", 1)[0]
        assert "Label x = ((cond) ? (pf_label_new" in body
        assert ": (Label{}));" in body
        assert "Line{}" not in body
        skip_if_no_compile_env()
        compile_cpp(cpp, label=f"drawing-ternary-cross-shadow-{index}")


def test_sibling_persistent_drawing_members_keep_exact_renamed_types():
    src = '''//@version=6
strategy("sibling drawing vars")
cond = close > open
if cond
    var label x = label.new(bar_index, high, "upper")
if not cond
    var label x = label.new(bar_index, low, "lower")
'''
    cpp = transpile(src)
    assert "Label x;" in cpp
    assert "Label x__blk1;" in cpp
    assert "double x__blk1" not in cpp
    assert "_pf_var_init_x__blk1" in cpp
    assert "x__blk1 = pf_label_new" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-sibling-persistent-members")


def test_only_history_read_sibling_becomes_exact_handle_series():
    src = '''//@version=6
strategy("sibling exact drawing series")
cond = close > open
if cond
    var line x = line.new(bar_index, high, bar_index + 1, high)
if not cond
    var label x = label.new(bar_index, low, "lower")
    prior = x[1]
'''
    cpp = transpile(src)
    assert "Line x;" in cpp
    assert "Series<Label> x__blk1;" in cpp
    assert "Series<Line> x;" not in cpp
    assert "prior = x__blk1[1];" in cpp
    assert "x__blk1.update(pf_label_new" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-sibling-exact-series")


def test_later_local_drawing_decl_does_not_preshadow_global_assignment():
    src = '''//@version=6
strategy("drawing source order shadow")
cond = close > open
var line x = na
f() =>
    x := cond ? line.new(bar_index, close, bar_index + 1, close) : na
    label x = cond ? label.new(bar_index, close, "local") : na
    0.0
v = f()
'''
    cpp = transpile(src)
    body = cpp.split("double f() {", 1)[1].split("    }", 1)[0]
    assert "x = ((cond) ? (pf_line_new" in body
    assert ": (Line{}));" in body
    assert "Label x = ((cond) ? (pf_label_new" in body
    assert ": (Label{}));" in body
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-source-order-shadow")


def test_cross_callable_persistent_drawing_name_collision_fails_closed():
    src = '''//@version=6
strategy("drawing callable member collision")
cond = close > open
makeLine() =>
    var line h = cond ? line.new(bar_index, close, bar_index + 1, close) : na
    h
makeLabel() =>
    var label h = cond ? label.new(bar_index, close, "label") : na
    h
a = makeLine()
b = makeLabel()
'''
    with pytest.raises(
        CompileError,
        match="Persistent drawing bindings named 'h'",
    ):
        transpile(src)


def test_global_scalar_and_persistent_drawing_identity_collision_fails_closed():
    src = '''//@version=6
strategy("drawing global member collision")
float x = close
f() =>
    var label x = na
    x
value = f()
'''
    with pytest.raises(CompileError, match="collides with a top-level"):
        transpile(src)


def test_global_drawing_and_persistent_scalar_get_distinct_storage():
    src = '''//@version=6
strategy("drawing global member isolation")
line x = line.new(bar_index, close, bar_index + 1, close)
f() =>
    var float x = 1.0
    x
value = f()
'''
    cpp = transpile(src)

    assert "    Line x = Line{};" in cpp
    assert "    double _pfv_1_x__f = na<double>();" in cpp
    assert "_pfv_1_x__f = 1.0;" in cpp
    assert "x = pf_line_new(" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-global-persistent-scalar-isolation")


def test_sibling_scalar_does_not_poison_function_var_drawing_target():
    src = '''//@version=6
strategy("sibling scalar and drawing var")
cond = close > open
f() =>
    if cond
        float h = close
    if not cond
        var line h = na
        h := cond ? line.new(bar_index, close, bar_index + 1, close) : na
    0.0
v = f()
'''
    cpp = transpile(src)
    body = cpp.split("double f_cs0() {", 1)[1].split("return 0.0;", 1)[0]
    assert "double h = current_bar_.close;" in body
    assert "h = ((cond) ? (pf_line_new" in body
    assert ": (Line{}));" in body
    assert "na<double>()" not in body
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-sibling-scalar-function-var")


def test_history_drawing_var_initializes_typed_and_in_source_order():
    src = '''//@version=6
strategy("drawing history init")
cond = close > open
var label x = cond ? label.new(bar_index, close, "initial") : na
if not na(x[1])
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert "Series<Label> x;" in cpp
    assert "x.push(Label{});" in cpp
    cond_pos = cpp.index("cond = ([&]{")
    init_pos = cpp.index("if (!_pf_var_init_x) {")
    init_end = cpp.index("_pf_var_init_x = true;", init_pos)
    init_block = cpp[init_pos:init_end]
    assert cond_pos < init_pos
    assert "x.update(((cond) ? (pf_label_new" in init_block
    assert ": (Label{})));" in init_block
    assert "x.push(cond ? label.new" not in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-history-var-init")


def test_function_history_drawing_var_clones_keep_series_handle_type():
    src = '''//@version=6
strategy("function drawing history clones")
cond = close > open
make() =>
    var line h = cond ? line.new(bar_index, close, bar_index + 1, close) : na
    prior = h[1]
    h
a = make()
b = make()
'''
    cpp = transpile(src)
    assert "Series<Line> h;" in cpp
    assert "Series<Line> h_cs1;" in cpp
    assert "Series<double> h" not in cpp
    assert "Line make_cs0()" in cpp
    assert "Line make_cs1()" in cpp
    assert "h.update(((cond) ? (pf_line_new" in cpp
    assert "h_cs1.update(((cond) ? (pf_line_new" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-function-history-clones")


def test_context_sensitive_fresh_drawing_series_advances_history():
    src = '''//@version=6
strategy("nested drawing fresh variants")
cond = close > open
leg(int size, bool arm) =>
    peak = ta.highest(size)
    if arm
        var line l = cond ? line.new(bar_index, peak, bar_index + 1, peak) : na
        old = l[1]
    peak
f_get(int len, bool arm) => leg(len, arm)
g_get(int len, bool arm) => leg(len, arm)
a = f_get(10, cond)
b = f_get(20, not cond)
c = g_get(30, cond)
'''
    cpp = transpile(src)
    assert "Series<Line> l__ni1;" in cpp
    assert "bool _pf_var_init_l__ni1 = false;" in cpp
    assert "if (!this->_pf_var_init_l__ni1)" in cpp
    assert "l__ni1.update(((cond) ? (pf_line_new" in cpp
    assert "l__ni1.push(l__ni1[0])" in cpp
    assert "l__ni1.update(l__ni1[0])" in cpp
    skip_if_no_compile_env()
    compile_cpp(cpp, label="drawing-fresh-series-history")


def test_numeric_ternary_na_remains_double():
    cpp = _cpp("cond = close > open\nvalue = cond ? close : na")
    assert "double value =" in cpp
    assert "na<double>()" in cpp


# ---------------------------------------------------------------------------
# Compile-gated full-feature probe.
# ---------------------------------------------------------------------------
_FULL_PROBE = '''//@version=6
strategy("drawing full probe", overlay=true, max_lines_count=300, max_boxes_count=100, max_labels_count=200)
var box bx = na
if bar_index == 10
    bx := box.new(bar_index, high, bar_index + 5, low, border_color=color.red)
if not na(bx)
    box.set_top(bx, high)
    box.set_rightbottom(bx, bar_index, low)
    if box.get_top(bx) - box.get_bottom(bx) > 0 and box.get_left(bx) > 0
        strategy.entry("L", strategy.long)
var label lb = na
lb := label.new(bar_index, close, "hi", xloc=xloc.bar_index, yloc=yloc.abovebar, style=label.style_label_down, color=color.green)
label.set_text(lb, "bye")
label.set_y(lb, close)
ly = label.get_y(lb)
lt = label.get_text(lb)
p1 = chart.point.now(close)
p2 = chart.point.from_index(bar_index + 3, high)
ln1 = line.new(p1, p2)
ln2 = line.new(bar_index, low, bar_index + 3, low)
lf = linefill.new(ln1, ln2, color.new(color.red, 80))
g1 = linefill.get_line1(lf)
px = line.get_price(ln2, bar_index + 1)
if px > 0
    strategy.close("L")
'''


def test_full_drawing_feature_compiles():
    skip_if_no_compile_env()
    compile_cpp(transpile(_FULL_PROBE), label="drawing-full-probe")

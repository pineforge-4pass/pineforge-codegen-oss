"""Drawing-objects-as-data: full-feature lowering + compile probes.

Covers the line/box/label/linefill/chart.point surface that the public
corpus does not exercise (only ``line.new`` appears there). Transpile-level
assertions pin the emitted ``pf_*`` arena calls; the compile-gated probes
prove the generated C++ type-checks against ``pineforge/drawing.hpp``.
"""

from __future__ import annotations

from pineforge_codegen import transpile
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

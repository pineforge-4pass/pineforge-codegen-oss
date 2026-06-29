"""Drawing-objects-as-data: loop-local drawing handles are REAL data.

Drawing geometry now becomes real C++ state (per-type arena in
``pineforge/drawing.hpp``); the handle variable declares as the C++ handle
struct (``Line``/``Label``) and the call lowers onto the arena. (Previously
these were inert no-ops declared as ``double`` / ``auto``.)
"""

from pineforge_codegen import transpile


def _cpp(body: str) -> str:
    return transpile('//@version=6\nstrategy("t")\n' + body + "\n")


def test_line_handle_in_loop_is_a_real_handle():
    cpp = _cpp(
        "var a = array.new<line>()\n"
        "for k = 1 to 3\n"
        "    ln = line.new(bar_index, close, bar_index + 1, close)\n"
        "    array.push(a, ln)"
    )
    # The loop-local declares as a Line handle and the ctor lowers onto the arena.
    assert "Line ln = pf_line_new(_pf_lines_," in cpp
    # The array<line> is a std::vector<Line>; the handle pushes by value.
    assert "std::vector<Line>" in cpp
    assert "a.push_back(ln)" in cpp
    # Drawing runtime is included + arena declared.
    assert "#include <pineforge/drawing.hpp>" in cpp
    assert "DrawingArena<LineRec> _pf_lines_" in cpp


def test_label_handle_in_loop_is_a_real_handle():
    cpp = _cpp(
        "for k = 1 to 3\n"
        "    lb = label.new(bar_index, high, 'x')\n"
        "    label.delete(lb)"
    )
    assert "Label lb = pf_label_new(_pf_labels_," in cpp
    # label.delete(lb) lowers onto the arena (NOT the generic _delete_ rewrite).
    assert "pf_label_delete(_pf_labels_, lb)" in cpp

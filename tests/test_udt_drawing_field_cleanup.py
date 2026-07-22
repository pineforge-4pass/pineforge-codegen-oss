"""UDT with drawing-typed fields: the field is now REAL handle data.

Drawing-objects-as-data (spec §4.2 / §U): a UDT field of type ``line``/
``box``/``label``/``linefill`` is no longer dropped from the emitted C++
struct — it lowers to a plain handle struct member (``Line ln;`` /
``std::vector<Line> upln;``), and ``m.tag := label.new(...)`` / ``x = m.tag``
become real field read/writes against the shared per-type arena.

(``table``/``polyline`` fields are still dropped — they have no C++ type.)
"""
from pineforge_codegen import transpile


def test_udt_label_field_is_real_handle():
    src = '''//@version=6
strategy("t")
type Marker
    float price
    label tag

m = Marker.new(price = close)
m.tag := label.new(bar_index, close, "x")
v = m.tag
plot(m.price)
'''
    cpp = transpile(src)
    # The struct now declares a real Label handle member.
    assert "Label tag" in cpp
    # The field write lowers onto the arena, NOT a dropped placeholder.
    assert "_pf_udt_Marker.get(m).tag = pf_label_new(_pf_labels_," in cpp
    # The field read is a plain handle copy (real member access survives).
    assert "_pf_udt_Marker.get(m).tag" in cpp


def test_udt_line_field_assignment_is_real():
    src = '''//@version=6
strategy("t")
type Segment
    float p1
    float p2
    line ln

s = Segment.new(p1 = close, p2 = open)
s.ln := line.new(bar_index, close, bar_index + 1, open)
plot(s.p1)
'''
    cpp = transpile(src)
    assert "Line ln" in cpp
    assert "_pf_udt_Segment.get(s).ln = pf_line_new(_pf_lines_," in cpp


def test_udt_box_field_read_is_real():
    src = '''//@version=6
strategy("t")
type Region
    float top
    float bot
    box bx

r = Region.new(top = high, bot = low)
v = r.bx
plot(r.top)
'''
    cpp = transpile(src)
    assert "Box bx" in cpp
    # The field read survives as a real handle member access.
    assert "_pf_udt_Region.read(r).bx" in cpp


def test_udt_without_drawing_fields_unchanged():
    """Regression: pure-numeric UDT continues to work."""
    src = '''//@version=6
strategy("t")
type Pt
    float x
    float y

p = Pt.new(x = 1.0, y = 2.0)
v = p.x + p.y
plot(v)
'''
    cpp = transpile(src)
    # x and y must be emitted as struct fields
    assert "double x" in cpp or "float x" in cpp
    assert "double y" in cpp or "float y" in cpp
    # Direct field access must still work — at least one reference to p.x
    # survives in the executable body (the plot of v depends on it).
    assert "_pf_udt_Pt.read(p).x" in cpp
    assert "_pf_udt_Pt.read(p).y" in cpp
    # No drawing machinery for a non-drawing strategy.
    assert "pineforge/drawing.hpp" not in cpp

"""UDT with drawing-typed fields must compile cleanly even when downstream code
references the dropped field.

Repro: when a Pine UDT contains a drawing-typed field (``label``, ``line``,
``box``, ``linefill``, ``polyline``, ``table``), codegen correctly omits the
field from the C++ struct emission (the engine doesn't model drawing objects).
But downstream ``m.tag := label.new(...)`` or ``x = m.tag`` references the
dropped field and the generated C++ would otherwise fail to compile.

The fix: track omitted fields per UDT and rewrite reads / strip writes so
the generated C++ never references a member that doesn't exist on the
emitted struct.
"""
from pineforge_codegen import transpile


def test_udt_with_dropped_label_field_compiles():
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
    # Struct must NOT declare tag (existing behavior)
    assert "label tag" not in cpp
    # Downstream m.tag references must NOT appear as a raw struct member
    # access (which would be a compile error). They must be commented out
    # or rewritten to a placeholder expression.
    for line in cpp.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*"):
            continue
        assert "m.tag" not in stripped, f"bare m.tag reference in: {stripped!r}"


def test_udt_with_dropped_line_field_assignment_stripped():
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
    assert "line ln" not in cpp
    for line in cpp.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*"):
            continue
        assert "s.ln" not in stripped


def test_udt_with_dropped_box_field_read_replaced():
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
    assert "box bx" not in cpp
    for line in cpp.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*"):
            continue
        assert "r.bx" not in stripped


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
    assert "p.x" in cpp
    assert "p.y" in cpp

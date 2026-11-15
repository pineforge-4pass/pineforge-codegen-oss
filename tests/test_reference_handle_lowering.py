"""Reference values keep their storage types across fields and user calls."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


@pytest.mark.parametrize(
    "kind,cpp_type",
    [
        ("line", "Line"),
        ("box", "Box"),
        ("label", "Label"),
        ("linefill", "Linefill"),
        ("chart.point", "ChartPoint"),
        ("polyline", "_PFPolyline"),
    ],
)
def test_reference_field_na_uses_the_field_type(kind, cpp_type):
    source = f'''//@version=6
strategy("Reference field")
type Holder
    {kind} drawingRef = na
var holderRef = Holder.new()
holderRef.drawingRef := na
'''
    cpp = transpile(source)
    assert f"{cpp_type} drawingRef" in cpp
    assert f"_pf_udt_Holder.get(holderRef).drawingRef = {cpp_type}{{}};" in cpp
    compile_cpp(cpp, label=f"reference-field-na-{kind}")


@pytest.mark.parametrize("element", ["float", "int", "string", "line", "polyline"])
def test_array_field_na_default_has_collection_storage(element):
    source = f'''//@version=6
strategy("Collection field")
type Bag
    array<{element}> xs = na
var bagRef = Bag.new()
bagRef.xs := array.new<{element}>()
plot(bagRef.xs.size())
bagRef.xs := na
'''
    cpp = transpile(source)
    assert ".xs = na<double>()" not in cpp
    assert "_PFUdtRecord_Bag{" in cpp
    compile_cpp(cpp, label=f"collection-field-na-{element}")


@pytest.mark.parametrize(
    "kind,cpp_type,factory",
    [
        ("line", "Line", "line.new(bar_index, low, bar_index, high)"),
        ("box", "Box", "box.new(bar_index, high, bar_index, low)"),
        ("label", "Label", 'label.new(bar_index, high, "marker")'),
    ],
)
@pytest.mark.parametrize("typed_parameter", [True, False], ids=["typed", "untyped"])
def test_handle_parameter_alias_and_tuple_are_values_at_each_call(
    kind, cpp_type, factory, typed_parameter
):
    hint = f"{kind} " if typed_parameter else ""
    source = f'''//@version=6
strategy("Handle values")
mark({hint}initialHandle, bool replaceHandle) =>
    aliasHandle = initialHandle
    if replaceHandle
        aliasHandle := {factory}
    [aliasHandle, bar_index]
var {kind} storedHandleA = na
var {kind} storedHandleB = na
[returnedA, indexA] = mark(storedHandleA, close > open)
[returnedB, indexB] = mark(storedHandleB, close < open)
storedHandleA := returnedA
storedHandleB := returnedB
'''
    cpp = transpile(source)
    definitions = re.findall(
        rf"std::tuple<{cpp_type}, int> mark(?:_cs\d+)?"
        rf"\({cpp_type} initialHandle, bool replaceHandle\)",
        cpp,
    )
    assert definitions
    assert f"std::tuple<{cpp_type}&" not in cpp
    assert "storedHandleA = returnedA;" in cpp
    assert "storedHandleB = returnedB;" in cpp
    compile_cpp(cpp, label=f"handle-value-boundaries-{kind}-{typed_parameter}")


def test_box_field_can_bind_directly_to_a_user_parameter():
    source = '''//@version=6
strategy("Field argument")
type Holder
    box drawingRef = na
extend(box drawingRef) =>
    box.set_right(drawingRef, bar_index)
var holderRef = Holder.new()
if barstate.isfirst
    holderRef.drawingRef := box.new(bar_index, high, bar_index, low)
extend(holderRef.drawingRef)
'''
    cpp = transpile(source)
    assert "extend(Box drawingRef)" in cpp
    assert "extend(_pf_udt_Holder.read(holderRef).drawingRef)" in cpp
    compile_cpp(cpp, label="box-field-user-parameter")


@pytest.mark.parametrize(
    "deletion",
    [
        "polyline priorHandle = done.shift()\npriorHandle.delete()",
        "priorHandle = done.shift()\npriorHandle.delete()",
        "done.shift().delete()",
    ],
    ids=["typed", "untyped", "chained"],
)
def test_polyline_array_element_keeps_its_handle_type(deletion):
    source = '''//@version=6
strategy("Polyline element")
var array<polyline> done = array.new<polyline>()
points = array.from(chart.point.from_index(bar_index, low), chart.point.from_index(bar_index, high))
done.push(polyline.new(points))
''' + deletion + "\n"
    cpp = transpile(source)
    assert "std::vector<_PFPolyline>" in cpp
    assert "None(" not in cpp
    assert "polyline priorHandle" not in cpp
    compile_cpp(cpp, label="polyline-array-element")


def test_polyline_field_and_cast_preserve_typed_reads_and_delete():
    source = '''//@version=6
strategy("Polyline field")
type Holder
    polyline drawingRef = na
var holderRef = Holder.new()
points = array.from(chart.point.from_index(bar_index, low), chart.point.from_index(bar_index, high))
holderRef.drawingRef := polyline.new(points = points)
polyline heldHandle = holderRef.drawingRef
heldHandle.delete()
polyline.delete(id = holderRef.drawingRef)
holderRef.drawingRef := polyline(x = na)
'''
    cpp = transpile(source)
    assert "_PFPolyline drawingRef" in cpp
    assert "_pf_udt_Holder.read(holderRef).drawingRef" in cpp
    assert "_pf_udt_Holder.get(holderRef).drawingRef = _PFPolyline{};" in cpp
    assert "None(" not in cpp
    compile_cpp(cpp, label="polyline-field-read-delete")

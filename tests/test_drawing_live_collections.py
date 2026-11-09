"""Live drawing reads borrow arena order; structural mutations stay refused."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import cpp_code_only
from pineforge_codegen.errors import CompileError


KINDS = [
    ("line", "Line", "_pf_lines_"),
    ("box", "Box", "_pf_boxes_"),
    ("label", "Label", "_pf_labels_"),
    ("linefill", "Linefill", "_pf_linefills_"),
]


@pytest.mark.parametrize("kind,handle,arena", KINDS)
@pytest.mark.parametrize("method,args", [
    ("size", ""), ("first", ""), ("last", ""),
    ("get", "0"), ("get", "index=-1"),
])
def test_live_drawing_method_reads_match_array_functions(kind, handle, arena, method, args):
    method_source = f"{kind}.all.{method}({args})"
    comma = ", " if args else ""
    namespace_source = f"array.{method}({kind}.all{comma}{args})"

    def source(expression):
        consumer = f"count = {expression}" if method == "size" else f"{kind}.delete({expression})"
        return '//@version=6\nstrategy("Live drawings", overlay=true)\n' + consumer + "\n"

    raw_cpp = transpile(source(method_source))
    cpp = cpp_code_only(raw_cpp)
    control = cpp_code_only(transpile(source(namespace_source)))
    assert cpp == control
    assert f"{arena}.order()" in cpp
    assert "0.size()" not in cpp and "0.first()" not in cpp
    if method == "size":
        assert f"static_cast<int>({arena}.order().size())" in cpp
    else:
        assert f"pf_{kind}_delete({arena}, {handle}{{" in cpp
        if method == "get":
            assert '_PF_ARRAY_STOP("index_out_of_bounds", "get"' in raw_cpp
            assert "__pf_array[(size_t)__pf_array_index]" in cpp
        else:
            assert f'_PF_ARRAY_STOP("empty_array_access", "{method}"' in raw_cpp


@pytest.mark.parametrize("kind,handle,arena", KINDS)
def test_deleting_oldest_drawing_then_counting_reads_live_order_again(kind, handle, arena):
    cpp = cpp_code_only(transpile(f"""//@version=6
strategy("Live deletion", overlay=true)
before = {kind}.all.size()
if before > 3
    {kind}.delete({kind}.all.first())
after = {kind}.all.size()
plot(after)
"""))
    assert f"before = static_cast<int>({arena}.order().size());" in cpp
    assert f"after = static_cast<int>({arena}.order().size());" in cpp
    assert f"pf_{kind}_delete({arena}, {handle}{{" in cpp
    assert not re.search(r"std::vector<" + handle + r">.*order\(\)", cpp)


def test_reported_line_collection_read_uses_arena_and_oldest_handle():
    cpp = cpp_code_only(transpile("""//@version=6
strategy("Live lines", overlay=true)
if bar_index % 50 == 0
    line.new(bar_index, low, bar_index + 10, high)
if line.all.size() > 3
    line.delete(line.all.first())
plot(close)
"""))
    assert "static_cast<int>(_pf_lines_.order().size())" in cpp
    assert "pf_line_delete(_pf_lines_, Line{" in cpp
    assert "__pf_array.front()" in cpp
    assert "0.size()" not in cpp and "0.first()" not in cpp


@pytest.mark.parametrize("kind,handle,arena", KINDS)
def test_live_collection_element_retains_handle_type_for_method_dispatch(kind, handle, arena):
    cpp = cpp_code_only(transpile(f"""//@version=6
strategy("Drawing element type")
oldest = {kind}.all.first()
oldest.delete()
"""))
    assert re.search(rf"\b{handle} oldest\b", cpp)
    assert f"pf_{kind}_delete({arena}, oldest)" in cpp


@pytest.mark.parametrize("kind,handle,arena", KINDS)
@pytest.mark.parametrize("use", [
    "{kind}.all.clear()",
    "array.pop({kind}.all)",
    "array.push({kind}.all, {kind}.all.first())",
])
def test_drawing_collection_mutations_have_catalog_refusal(kind, handle, arena, use):
    with pytest.raises(CompileError) as caught:
        transpile('//@version=6\nstrategy("Read only")\n' + use.format(kind=kind) + "\n")
    assert caught.value.diagnostics[0].code == "PF-E5086"


@pytest.mark.parametrize("expression", [
    "polyline.all.size()", "array.first(polyline.all)", "polyline.all",
])
def test_polyline_collection_requires_engine_handle_and_arena(expression):
    with pytest.raises(CompileError) as caught:
        transpile('//@version=6\nstrategy("Polylines")\nvalue = ' + expression + "\n")
    assert caught.value.diagnostics[0].code == "PF-E5085"

"""Declared drawing-count range diagnostics and boundary coverage."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


KIND_FIELDS = [
    pytest.param("max_lines_count", "LineRec", "line.new(bar_index, close, bar_index + 1, close)", id="line"),
    pytest.param("max_boxes_count", "BoxRec", "box.new(bar_index, high, bar_index + 1, low)", id="box"),
    pytest.param("max_labels_count", "LabelRec", 'label.new(bar_index, close, "x")', id="label"),
]


def _count_source(field, written, call):
    return f'//@version=6\nstrategy("declared count", {field}={written})\nobj = {call}\n'


@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_declared_count_at_the_native_boundary_is_emitted_unchanged(field, record, call):
    # The largest accepted count must remain accepted at the native boundary.
    transpile(_count_source(field, "2147483642", call))


@pytest.mark.parametrize("written", [
    "2147483643",     # one above the accepted boundary
    "2147483647",     # INT32_MAX itself
    "4294967297",     # wraps to 1 when narrowed to int32
    "4294967346",     # wraps to 50, the default
    "3e9",            # an integral float spelling above the boundary
])
@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_declared_count_beyond_the_native_boundary_is_refused_at_its_literal(
    field, record, call, written
):
    source = _count_source(field, written, call)
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E0036"
    assert diagnostic.message == "Numeric literal exceeds the generated C++ range."
    header = source.splitlines()[1]
    assert (diagnostic.location.line, diagnostic.location.col) == (2, header.index(written) + 1)


@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_unused_oversized_count_without_drawings_is_not_refused(field, record, call):
    # No drawing declaration means no drawing arena should be emitted, but the
    # unused oversized count remains legal and must not raise.
    cpp = transpile(f'//@version=6\nstrategy("no drawings", {field}=4294967297)\nplot(close)\n')
    assert "DrawingArena<" not in cpp

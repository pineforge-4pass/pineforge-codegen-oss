"""Drawing arena configuration in emitted members and run preparation."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


@pytest.mark.parametrize(
    "header, record, arena",
    [
        pytest.param(
            "max_boxes_count=5, max_labels_count=10",
            "LineRec", "_pf_lines_", id="line",
        ),
        pytest.param(
            "max_lines_count=5, max_labels_count=10",
            "BoxRec", "_pf_boxes_", id="box",
        ),
        pytest.param(
            "max_lines_count=5, max_boxes_count=5",
            "LabelRec", "_pf_labels_", id="label",
        ),
    ],
)
def test_undeclared_count_constructs_bounded_arena_with_default(header, record, arena):
    cpp = transpile(
        f'//@version=6\nstrategy("default drawing count", {header})\n'
        "ln = line.new(bar_index, close, bar_index + 1, close)\n"
        "bx = box.new(bar_index, high, bar_index + 1, low)\n"
        'lb = label.new(bar_index, close, "x")\n'
    )
    assert (
        f"DrawingArena<{record}> {arena}"
        f"{{::pineforge::drawing_bound_from_declared_count<{record}>(50)}};"
    ) in cpp


@pytest.mark.parametrize(
    "source, reset_statement",
    [
        pytest.param(
            '//@version=6\nstrategy("box reset", max_boxes_count=5)\n'
            "bx = box.new(bar_index, high, bar_index + 1, low)\n",
            "this->_pf_boxes_ = decltype(this->_pf_boxes_)"
            "{::pineforge::drawing_bound_from_declared_count<BoxRec>(5)};",
            id="box",
        ),
        pytest.param(
            '//@version=6\nstrategy("label reset", max_labels_count=10)\n'
            'lb = label.new(bar_index, close, "x")\n',
            "this->_pf_labels_ = decltype(this->_pf_labels_)"
            "{::pineforge::drawing_bound_from_declared_count<LabelRec>(10)};",
            id="label",
        ),
    ],
)
def test_run_preparation_reconstructs_declared_bound(source, reset_statement):
    cpp = transpile(source)
    preparation = cpp.split("    void prepare_script_run(", 1)[1].split("\n    }", 1)[0]
    assert reset_statement in preparation


# The native arena factory takes the declared count as an int32 and accepts at most
# INT32_MAX minus its slack of 5 (2,147,483,642). A larger written count must be refused
# at its source location, never clamped, and never printed into the call where it would wrap.
KIND_FIELDS = [
    pytest.param("max_lines_count", "LineRec", "line.new(bar_index, close, bar_index + 1, close)", id="line"),
    pytest.param("max_boxes_count", "BoxRec", "box.new(bar_index, high, bar_index + 1, low)", id="box"),
    pytest.param("max_labels_count", "LabelRec", 'label.new(bar_index, close, "x")', id="label"),
]


def _count_source(field, written, call):
    return f'//@version=6\nstrategy("declared count", {field}={written})\nobj = {call}\n'


@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_declared_count_at_the_native_boundary_is_emitted_unchanged(field, record, call):
    cpp = transpile(_count_source(field, "2147483642", call))
    assert f"drawing_bound_from_declared_count<{record}>(2147483642)" in cpp


@pytest.mark.parametrize("written", [
    "2147483643",     # one above the boundary
    "2147483647",     # INT32_MAX itself: still above INT32_MAX - slack
    "4294967297",     # wraps to 1 when narrowed to int32
    "4294967346",     # wraps to 50, the default, when narrowed
    "3e9",            # an integral float spelling above the boundary
])
@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_declared_count_beyond_the_native_boundary_is_refused_at_its_literal(field, record, call, written):
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
    cpp = transpile(f'//@version=6\nstrategy("no drawings", {field}=4294967297)\nplot(close)\n')
    assert "drawing_bound_from_declared_count" not in cpp


@pytest.mark.parametrize("field, record, call", KIND_FIELDS)
def test_small_declared_count_is_still_emitted_as_written(field, record, call):
    cpp = transpile(_count_source(field, "7", call))
    assert f"drawing_bound_from_declared_count<{record}>(7)" in cpp

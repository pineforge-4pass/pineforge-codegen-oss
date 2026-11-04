"""Drawing arena configuration in emitted members and run preparation."""

import pytest

from pineforge_codegen import transpile


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

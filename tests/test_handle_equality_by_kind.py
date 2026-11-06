"""== and != between drawing handles follow TradingView's rule for each kind.

TradingView compares two `line` or two `label` handles with == and != (by
identity) and refuses the comparison of two `box` or two `linefill` handles
(CE10123). The rule holds the same way at the top level and inside a loop over
the live collection of the kind (`for m in label.all`), with the other operand
a global, a function parameter or a loop variable. The rows pin both halves:
line and label compile in every one of those shapes, box and linefill stay
refused with the analyzer's operator code.

Only the four kinds TradingView has been read on are pinned: table, polyline
and chart.point handles are not.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


HEADER = '//@version=6\nstrategy("handle equality by kind")\n'

# One expression that makes a handle of each kind.
CONSTRUCTORS = {
    "label": "label.new(bar_index, close)",
    "line": "line.new(bar_index, close, bar_index + 1, close)",
    "box": "box.new(bar_index, high, bar_index + 1, low)",
    "linefill": (
        "linefill.new(line.new(bar_index, close, bar_index + 1, close), "
        "line.new(bar_index, open, bar_index + 1, open))"
    ),
}


def _parameter_form(kind: str, operator: str) -> str:
    """The loop variable against a function parameter."""
    return HEADER + (
        f"f({kind} h) =>\n"
        "    bool r = false\n"
        f"    for m in {kind}.all\n"
        f"        if m {operator} h\n"
        "            r := true\n"
        "    r\n"
        f"var {kind} keep = na\n"
        "if barstate.islast\n"
        f"    keep := {CONSTRUCTORS[kind]}\n"
        "bool found = f(keep)\n"
    )


def _top_level_form(kind: str, operator: str) -> str:
    """The loop variable against a global, in a loop at the top level."""
    return HEADER + (
        f"var {kind} keep = na\n"
        "if barstate.islast\n"
        f"    keep := {CONSTRUCTORS[kind]}\n"
        "bool r = false\n"
        f"for m in {kind}.all\n"
        f"    if m {operator} keep\n"
        "        r := true\n"
    )


def _global_pair(kind: str, operator: str) -> str:
    """Two globals compared outside any loop."""
    return HEADER + (
        f"var {kind} lhs = na\n"
        f"var {kind} rhs = na\n"
        "if barstate.islast\n"
        f"    lhs := {CONSTRUCTORS[kind]}\n"
        f"    rhs := {CONSTRUCTORS[kind]}\n"
        f"bool compared = lhs {operator} rhs\n"
    )


SHAPES = [_parameter_form, _top_level_form, _global_pair]


@pytest.mark.parametrize("shape", SHAPES, ids=lambda shape: shape.__name__.strip("_"))
@pytest.mark.parametrize("operator", ["==", "!="])
@pytest.mark.parametrize("kind", ["label", "line"])
def test_line_and_label_handles_compare(kind, operator, shape):
    cpp = transpile(shape(kind, operator))
    # A handle compares by its identity, in the operator that was written.
    assert f".id {operator} (" in cpp, cpp[cpp.index("on_source_bar"):][:600]


@pytest.mark.parametrize("shape", [_parameter_form, _top_level_form],
                         ids=lambda shape: shape.__name__.strip("_"))
@pytest.mark.parametrize("operator", ["==", "!="])
@pytest.mark.parametrize("kind", ["box", "linefill"])
def test_box_and_linefill_handles_stay_refused(kind, operator, shape):
    with pytest.raises(CompileError) as caught:
        transpile(shape(kind, operator))
    diagnostics = caught.value.diagnostics
    assert [diagnostic.code for diagnostic in diagnostics] == ["PF-E2066"], diagnostics
    assert kind in diagnostics[0].message

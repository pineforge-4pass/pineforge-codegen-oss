"""Regression: loop-local drawing-object handles must be declared.

Drawing calls (line.new / label.new / box.new / table.new ...) are no-ops in a
backtest, but the variable they're assigned to may still be referenced (e.g.
pushed into an `array<line>`). When the assignment lives in a for-loop body the
handle is a local; it must be declared (as an inert default) or the generated
C++ references an undeclared identifier.
"""

from pineforge_codegen import transpile


def _cpp(body: str) -> str:
    return transpile('//@version=6\nstrategy("t")\n' + body + "\n")


def test_line_handle_in_loop_is_declared():
    cpp = _cpp(
        "var a = array.new<line>()\n"
        "for k = 1 to 3\n"
        "    ln = line.new(bar_index, close, bar_index + 1, close)\n"
        "    array.push(a, ln)"
    )
    assert "double ln" in cpp or "auto ln" in cpp


def test_label_handle_in_loop_is_declared():
    cpp = _cpp(
        "for k = 1 to 3\n"
        "    lb = label.new(bar_index, high, 'x')\n"
        "    label.delete(lb)"
    )
    assert "double lb" in cpp or "auto lb" in cpp

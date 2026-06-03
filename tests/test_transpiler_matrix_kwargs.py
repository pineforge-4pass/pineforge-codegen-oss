"""Matrix method calls with Pine keyword args (e.g. add_row(array_id=...))."""

from pineforge_codegen import transpile


def test_matrix_add_row_array_id_kwarg_emits_append():
    src = """
//@version=6
strategy("m", overlay=true)
var m = matrix.new<float>()
if bar_index == 0
    m := matrix.new<float>()
    m.add_row(array_id = array.from(1.0, 2.0))
"""
    cpp = transpile(src, check_support=False)
    assert "add_row" in cpp
    assert ".rows()" in cpp

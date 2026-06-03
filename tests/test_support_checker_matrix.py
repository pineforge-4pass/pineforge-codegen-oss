"""Support-checker validation for matrix<T> element types (Task 2.15)."""
import pytest
from pineforge_codegen import transpile


def test_matrix_unknown_element_rejected():
    src = '''//@version=6
strategy("t")
var m = matrix.new<NotAType>(2, 2)
'''
    with pytest.raises(Exception, match="not supported|not declared|element type"):
        transpile(src)


def test_matrix_nested_collection_rejected():
    src = '''//@version=6
strategy("t")
var m = matrix.new<array<float>>(2, 2)
'''
    with pytest.raises(Exception, match="nested collection|not supported"):
        transpile(src)


def test_matrix_float_accepted():
    src = '''//@version=6
strategy("t")
var m = matrix.new<float>(2, 2, 0.0)
'''
    transpile(src)


def test_matrix_int_accepted():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
'''
    transpile(src)


def test_matrix_color_accepted():
    src = '''//@version=6
strategy("t")
var m = matrix.new<color>(2, 2)
'''
    transpile(src)


def test_matrix_udt_accepted():
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(1, 1)
'''
    transpile(src)

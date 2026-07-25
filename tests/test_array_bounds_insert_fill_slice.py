"""Bounds coverage for ``array.insert``, 3-argument ``array.fill``, ``array.slice``.

The checked-access family (``get``/``set``/``remove``/``first``/``last``/``pop``/
``shift``/``percentrank``) already converts an out-of-range Pine index into a
deterministic ``pine_runtime_error``.  These three lowerings were left emitting
raw STL iterator arithmetic, so an out-of-range index produced C++ undefined
behaviour (an iterator outside ``[begin, end]``) instead of Pine's runtime
error.

Bound semantics pinned here, from the Pine v6 reference:

* ``array.insert`` is an *insertion* point, so ``index == size`` is legal and
  appends.  The reference also lists ``insert`` in the negative-indexing set
  (``array.get``/``array.set``/``array.insert``/``array.remove``), so a negative
  index is end-relative exactly as it is for ``get``/``set``/``remove``.
* ``array.fill``/``array.slice`` take a half-open ``[index_from, index_to)``
  range — ``index_to`` is "one greater than the last index", so ``index_to ==
  size`` is legal.  Neither is in the reference's negative-indexing set, so a
  negative endpoint is rejected (the same stance ``array.percentrank`` already
  takes).
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests.test_array_checked_access import _compile_and_run


PRELUDE = '//@version=6\nstrategy("T")\n'
SETUP = "a = array.new_float(3, 0.0)\n"


def _generate(body: str) -> str:
    return transpile(f"{PRELUDE}{body}\n")


def _statement(cpp: str, needle: str) -> str:
    return next(line for line in cpp.splitlines() if needle in line)


# ---------------------------------------------------------------------------
# Out-of-range indices must reach the runtime error path, not raw STL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        SETUP + "array.insert(a, 9, 1.0)",
        SETUP + "a.insert(9, 1.0)",
        SETUP + "array.insert(id=a, index=9, value=1.0)",
        SETUP + "a.insert(index=9, value=1.0)",
        SETUP + "array.fill(a, 1.0, 0, 9)",
        SETUP + "a.fill(1.0, 0, 9)",
        SETUP + "array.fill(id=a, value=1.0, index_from=0, index_to=9)",
        SETUP + "b = array.slice(a, 2, 9)\nplot(array.size(b))",
        SETUP + "b = a.slice(2, 9)\nplot(array.size(b))",
        SETUP + "b = array.slice(id=a, index_from=2, index_to=9)\nplot(array.size(b))",
    ],
)
def test_bounded_methods_route_through_runtime_error_path(body: str):
    assert "pine_runtime_error" in _generate(body)


@pytest.mark.parametrize(
    "body",
    [
        SETUP + "array.insert(a, 9, 1.0)",
        SETUP + "array.fill(a, 1.0, 0, 9)",
        SETUP + "b = array.slice(a, 2, 9)\nplot(array.size(b))",
    ],
)
def test_bounded_methods_do_not_emit_raw_iterator_arithmetic(body: str):
    cpp = _generate(body)
    assert "a.begin() + (int)(" not in cpp
    assert "a.begin()+(int)(" not in cpp


# ---------------------------------------------------------------------------
# Exact bound each lowering enforces
# ---------------------------------------------------------------------------

def test_insert_accepts_index_equal_to_size():
    """``index == size`` appends; only ``index > size`` is out of bounds."""
    stmt = _statement(_generate(SETUP + "array.insert(a, 3, 1.0)"), "__pf_array.insert")
    assert "__pf_array_index<0||__pf_array_index>__pf_array_size)" in stmt
    assert "__pf_array_index>=__pf_array_size" not in stmt


def test_insert_normalizes_negative_indices_like_get_set_remove():
    stmt = _statement(_generate(SETUP + "array.insert(a, -1, 1.0)"), "__pf_array.insert")
    assert "__pf_raw_index<0?__pf_raw_index+__pf_array_size:__pf_raw_index" in stmt


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (SETUP + "array.fill(a, 1.0, 0, 3)", "std::fill"),
        (SETUP + "b = array.slice(a, 0, 3)\nplot(array.size(b))", "__pf_array_index_to"),
    ],
)
def test_range_methods_accept_index_to_equal_to_size(body: str, needle: str):
    """``index_to`` is exclusive, so ``index_to == size`` is a legal endpoint."""
    stmt = _statement(_generate(body), needle)
    assert "__pf_array_index_from<0||__pf_array_index_from>__pf_array_size_index_from)" in stmt
    assert "__pf_array_index_to<0||__pf_array_index_to>__pf_array_size_index_to)" in stmt


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (SETUP + "array.fill(a, 1.0, 0, 3)", "std::fill"),
        (SETUP + "b = array.slice(a, 0, 3)\nplot(array.size(b))", "__pf_array_index_to"),
    ],
)
def test_range_methods_do_not_normalize_negative_endpoints(body: str, needle: str):
    """``fill``/``slice`` are absent from the reference's negative-index set."""
    stmt = _statement(_generate(body), needle)
    assert "int64_t __pf_array_index_from=__pf_raw_index_from;" in stmt
    assert "int64_t __pf_array_index_to=__pf_raw_index_to;" in stmt
    assert "__pf_raw_index_from<0?" not in stmt
    assert "__pf_raw_index_to<0?" not in stmt


def test_range_methods_reject_inverted_ranges():
    for body, needle in (
        (SETUP + "array.fill(a, 1.0, 2, 1)", "std::fill"),
        (SETUP + "b = array.slice(a, 2, 1)\nplot(array.size(b))", "__pf_array_index_to"),
    ):
        stmt = _statement(_generate(body), needle)
        assert "__pf_array_index_from>__pf_array_index_to" in stmt


def test_single_argument_fill_overload_is_unchanged():
    """The 1-argument ``array.fill(id, value)`` form has no index to check."""
    stmt = _statement(_generate(SETUP + "array.fill(a, 1.0)"), "std::fill")
    assert stmt.strip() == "std::fill(a.begin(), a.end(), 1.0);"


# ---------------------------------------------------------------------------
# The emitted C++ still parses against the public engine headers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("insert", SETUP + "array.insert(a, 9, 1.0)"),
        ("insert_at_size", SETUP + "array.insert(a, 3, 1.0)"),
        ("insert_negative", SETUP + "array.insert(a, -1, 1.0)"),
        ("fill_range", SETUP + "array.fill(a, 1.0, 0, 9)"),
        ("fill_single", SETUP + "array.fill(a, 1.0)"),
        ("slice", SETUP + "b = array.slice(a, 2, 9)\nplot(array.size(b))"),
        (
            "slice_typed_method",
            "var array<int> ints = array.from(1, 2, 3)\n"
            "probe() =>\n"
            "    array<int> s = ints.slice(0, 2)\n"
            "    array.size(s)\n"
            "observed = probe()",
        ),
    ],
)
def test_bounded_lowerings_compile(label: str, body: str):
    compile_env.skip_if_no_compile_env()
    compile_env.compile_cpp(_generate(body), label=f"array-bounds-{label}")


# ---------------------------------------------------------------------------
# Runtime probes
# ---------------------------------------------------------------------------

_VALID_SOURCE = """//@version=6
strategy("Array bounds valid ranges")
values = array.new_float(3, 0.0)
array.insert(values, 3, 7.0)
array.insert(values, -1, 5.0)
values_size = array.size(values)
values_at_3 = array.get(values, 3)
values_at_4 = array.get(values, 4)
filled = array.new_float(4, 0.0)
array.fill(filled, 2.0, 1, 3)
filled_sum = array.sum(filled)
array.fill(filled, 9.0, 4, 4)
filled_sum_after_empty_fill = array.sum(filled)
sliced = array.slice(filled, 1, 4)
sliced_size = array.size(sliced)
sliced_sum = array.sum(sliced)
empty_slice = array.slice(filled, 4, 4)
empty_slice_size = array.size(empty_slice)
"""


_ERROR_SOURCE = """//@version=6
strategy("Array bounds errors")
selector = close
values = array.new_float(3, 0.0)
sink = 0.0

if selector == 1
    array.insert(values, 4, 1.0)
else if selector == 2
    array.insert(values, -4, 1.0)
else if selector == 3
    array.fill(values, 1.0, 0, 4)
else if selector == 4
    array.fill(values, 1.0, -1, 3)
else if selector == 5
    array.fill(values, 1.0, 2, 1)
else if selector == 6
    sink := array.size(array.slice(values, 0, 4))
else if selector == 7
    sink := array.size(array.slice(values, -1, 2))
else if selector == 8
    sink := array.size(array.slice(values, 2, 1))
"""


def test_valid_boundary_indices_run_and_produce_pine_results():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.values_size << " "
              << strategy.values_at_3 << " "
              << strategy.values_at_4 << " "
              << strategy.filled_sum << " "
              << strategy.filled_sum_after_empty_fill << " "
              << strategy.sliced_size << " "
              << strategy.sliced_sum << " "
              << strategy.empty_slice_size << "\n";
}
"""
    output = _compile_and_run(transpile(_VALID_SOURCE) + driver)
    assert tuple(float(value) for value in output.split()) == (
        5.0,   # [0, 0, 0, 5, 7] after append-at-size then negative insert
        5.0,
        7.0,
        4.0,   # fill(2.0) over [1, 3) of a 4-element zero array
        4.0,   # empty fill over [4, 4) changes nothing
        3.0,   # slice [1, 4) of a 4-element array
        4.0,
        0.0,   # slice [4, 4) is empty, not out of bounds
    )


def test_out_of_range_indices_surface_deterministic_last_error():
    driver = r"""
#include <iostream>
int main() {
    for (int selector = 1; selector <= 8; ++selector) {
        GeneratedStrategy strategy;
        double value = static_cast<double>(selector);
        Bar bar{value, value, value, value, 1.0, selector};
        strategy.run(&bar, 1);
        std::cout << selector << "\t" << strategy.last_error() << "\n";
    }
}
"""
    output = _compile_and_run(transpile(_ERROR_SOURCE) + driver)
    assert output.splitlines() == [
        "1\tIndex 4 is out of bounds. Array size is 3",
        "2\tIndex -4 is out of bounds. Array size is 3",
        "3\tIndex 4 is out of bounds. Array size is 3",
        "4\tIndex -1 is out of bounds. Array size is 3",
        "5\tIndex range 2..1 is invalid. Array size is 3",
        "6\tIndex 4 is out of bounds. Array size is 3",
        "7\tIndex -1 is out of bounds. Array size is 3",
        "8\tIndex range 2..1 is invalid. Array size is 3",
    ]

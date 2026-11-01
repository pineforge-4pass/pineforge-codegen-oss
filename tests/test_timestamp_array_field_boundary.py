"""Timestamp-bearing integer fields compile and preserve their Pine values."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_timestamp_callable_width import HEADER, _run


FIELD = '''type Book
    array<int> stamps
var b = Book.new(array.new<int>())
'''


@pytest.mark.parametrize("write, read", [
    ("b.stamps.unshift(time)", "b.stamps.get(0)"),
    ("array.unshift(b.stamps, time)", "b.stamps.get(0)"),
    ("array.push(value = time, id = b.stamps)", "b.stamps.get(0)"),
    ("b.stamps.insert(0, time)", "b.stamps.get(0)"),
    ("b.stamps.push(0)\nb.stamps.set(0, time)", "b.stamps.get(0)"),
    ("b.stamps.push(0)\nb.stamps.fill(time)", "b.stamps.get(0)"),
    ("alias = b.stamps\nalias.push(time)", "alias.get(0)"),
    ("alias = b.stamps.copy()\nalias.push(time)", "alias.get(0)"),
    ("alias = b.stamps.slice(0, 0)\nalias.push(time)", "alias.get(0)"),
    ("b.stamps := array.from(time)", "b.stamps.get(0)"),
    ("b.stamps := array.new<int>(1, time)", "b.stamps.get(0)"),
])
def test_epoch_field_storage_preserves_value(write, read):
    # The directly written array, alias or copy reads the full timestamp.
    cpp = transpile(HEADER + FIELD + write + f"\nok = {read} == time ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_array_method_named_argument_is_a_known_gap():
    # Named arguments on array METHOD calls are a separate finding
    # (class: named arguments on array.* calls, see the A4 spec).
    # Update this pin deliberately when that binding gap is fixed.
    # Unchanged pin: push has no keyword slots, so args=[] raises the arity error.
    with pytest.raises(CompileError, match=r"array\.push: wrong number of arguments$"):
        transpile(HEADER + '''var arr = array.new<float>()
arr.push(value = 1.0)
plot(close)
''')


@pytest.mark.parametrize("constructor", [
    "Book.new(array.from(time))",
    "Book.new(stamps = array.new<int>(1, time))",
])
def test_constructor_epoch_field_preserves_value(constructor):
    cpp = transpile(HEADER + "type Book\n    array<int> stamps\n"
                    f"var b = {constructor}\nok = b.stamps.get(0) == time ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


@pytest.mark.parametrize("persistent", ["var ", ""])
def test_both_record_lifetimes_preserve_epoch(persistent):
    cpp = transpile(HEADER + FIELD.replace("var b =", persistent + "b =")
                    + "b.stamps.unshift(time)\nok = b.stamps.get(0) == time ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_callable_array_alias_and_result_are_connected_to_field():
    # The actual field binds xs, the identity result binds alias, and alias receives time.
    cpp = transpile(HEADER + FIELD + '''identity(array<int> xs) => xs
alias = identity(b.stamps)
alias.push(time)
ok = alias.get(0) == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_callable_array_writer_is_connected_to_field():
    # A write through the parameter changes the storage supplied by Book.stamps.
    cpp = transpile(HEADER + FIELD + '''append(array<int> xs, int epoch) =>
    xs.push(epoch)
append(b.stamps, time)
ok = b.stamps.get(0) == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_method_receiver_field_write_is_resolved():
    # Book.self is a typed receiver; its field receives the bound epoch parameter.
    cpp = transpile(HEADER + '''type Book
    array<int> stamps
method append(Book self, int epoch) =>
    self.stamps.push(epoch)
var b = Book.new(array.new<int>())
b.append(epoch = time)
ok = b.stamps.get(0) == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_count_field_and_float_timestamp_field_are_unchanged():
    # A float holds these timestamps exactly; the count-only field keeps its value.
    cpp = transpile(HEADER + '''type Book
    array<int> counts
    array<float> stamps
var b = Book.new(array.new<int>(), array.new<float>())
b.counts.push(bar_index)
b.stamps.unshift(time)
ok = b.counts.get(0) == bar_index and b.stamps.get(0) == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"

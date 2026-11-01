"""Timestamp-bearing integer array fields fail before any truncated run."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_timestamp_callable_width import HEADER, _run


FIELD = '''type Book
    array<int> stamps
var b = Book.new(array.new<int>())
'''
MESSAGE = "Timestamp values in Book.stamps require 64-bit array field storage"


@pytest.mark.parametrize("write", [
    "b.stamps.unshift(time)",
    "array.unshift(b.stamps, time)",
    "array.push(value = time, id = b.stamps)",
    "b.stamps.insert(0, time)",
    "b.stamps.push(0)\nb.stamps.set(0, time)",
    "b.stamps.push(0)\nb.stamps.fill(time)",
    "alias = b.stamps\nalias.push(time)",
    "alias = b.stamps.copy()\nalias.push(time)",
    "alias = b.stamps.slice(0, 0)\nalias.push(time)",
    "b.stamps := array.from(time)",
    "b.stamps := array.new<int>(1, time)",
])
def test_epoch_field_storage_has_a_clear_compile_failure(write):
    # Each component includes Book.stamps and an epoch write; int32 storage must not run.
    cpp = transpile(HEADER + FIELD + write + "\nplot(close)\n")
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


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
def test_constructor_epoch_field_has_a_clear_compile_failure(constructor):
    # An inline constructor stores the epoch in the same int32 field as a later push.
    cpp = transpile(HEADER + "type Book\n    array<int> stamps\n"
                    f"var b = {constructor}\nplot(close)\n")
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


@pytest.mark.parametrize("persistent", ["var ", ""])
def test_both_record_lifetimes_block_epoch_truncation(persistent):
    # Persistent vectors and bar-local array aliases both hold int32 elements today.
    cpp = transpile(HEADER + FIELD.replace("var b =", persistent + "b =")
                    + "b.stamps.unshift(time)\nplot(close)\n")
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


def test_callable_array_alias_and_result_are_connected_to_field():
    # The actual field binds xs, the identity result binds alias, and alias receives time.
    cpp = transpile(HEADER + FIELD + '''identity(array<int> xs) => xs
alias = identity(b.stamps)
alias.push(time)
plot(close)
''')
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


def test_callable_array_writer_is_connected_to_field():
    # A write through the parameter changes the storage supplied by Book.stamps.
    cpp = transpile(HEADER + FIELD + '''append(array<int> xs, int epoch) =>
    xs.push(epoch)
append(b.stamps, time)
plot(close)
''')
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


def test_method_receiver_field_write_is_resolved():
    # Book.self is a typed receiver; its field receives the bound epoch parameter.
    cpp = transpile(HEADER + '''type Book
    array<int> stamps
method append(Book self, int epoch) =>
    self.stamps.push(epoch)
var b = Book.new(array.new<int>())
b.append(epoch = time)
plot(close)
''')
    assert MESSAGE in cpp
    with pytest.raises(AssertionError, match=MESSAGE):
        compile_cpp(cpp)


def test_count_field_and_float_timestamp_field_are_unchanged():
    # Unchanged pin: a float holds the epoch exactly, while a count-only field stays int32.
    cpp = transpile(HEADER + '''type Book
    array<int> counts
    array<float> stamps
var b = Book.new(array.new<int>(), array.new<float>())
b.counts.push(bar_index)
b.stamps.unshift(time)
ok = b.counts.get(0) == bar_index and b.stamps.get(0) == time ? 1 : 0
''')
    assert "require 64-bit array field storage" not in cpp
    assert "std::vector<int> counts" in cpp
    assert "std::vector<double> stamps" in cpp
    compile_cpp(cpp)
    assert _run(cpp) == "1"
